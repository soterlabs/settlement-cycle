"""Closing-only RPC snapshots. BA/third-party balance-sheet data is never an input."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time
from decimal import Decimal as D
from decimal import localcontext

import requests

from ..domain.config import load_prime_by_id
from ..extract._keccak import keccak256
from .cash_flow.classify import is_cash
from .cash_flow.collector import rpc as configured_rpc
from .cash_flow.config import load as cash_config
from .verification import digest


def rpc(chain, method, params):
    # Public mainnet endpoint documented by OKX; explicit env configuration wins.
    if chain != "xlayer" or os.environ.get("XLAYER_RPC"):
        return configured_rpc(chain, method, params)
    for _ in range(3):
        try:
            response = requests.post(
                "https://rpc.xlayer.tech",
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=25,
            )
            data = response.json()
            if response.ok and isinstance(data, dict) and "result" in data:
                return data["result"]
        except (requests.RequestException, ValueError):
            pass
    raise ValueError("Historical XLayer RPC unavailable")


CHAIN_IDS = {
    "ethereum": 1,
    "base": 8453,
    "arbitrum": 42161,
    "optimism": 10,
    "unichain": 130,
    "avalanche_c": 43114,
    "robinhood": 4663,
    "xlayer": 196,
}

VAT = "0x35d1b3f3d7966a1dfe207aa4514c12a259a0492b"
ETH_FEED = "0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419"
ETH_USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
ETH_USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
ETH_WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
SUSDS = "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
# Addresses independently checked against sparkdotfi/spark-address-registry.
EXTRA_SAVINGS = [
    (
        "arb_usdt",
        "arbitrum",
        "0x45d91340b3b7b96985a72b5c678f7d9e8d664b62",
        "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",
        6,
        "Savings USDT (Arbitrum)",
    ),
    (
        "xlayer_usdc",
        "xlayer",
        "0xf90e63079d97a0a1f479b2b168457f420caff6ba",
        "0xb6ceceab302e2e4948951ee7843fc24e92933061",
        6,
        "Savings USDC (XLayer)",
    ),
    (
        "xlayer_usdt",
        "xlayer",
        "0xc358c90d32375721cb3924320fdc2f8b694347ca",
        "0x779ded0c9e1022225f8e0630b35a9b54be713736",
        6,
        "Savings USDT (XLayer)",
    ),
]
PAU = "0x8d719a830b00e5571db00d173505cd56c0ec224a"
PAU_SUSDC = "0xbc65ad17c5c0a2a4d159fa5a503f4992c7b545fe"


def boundary(chain, day, candidate=None):
    """Prove the last finalized block of the UTC day, including RPC-only chains."""
    chain_id = int(rpc(chain, "eth_chainId", []), 16)
    if chain_id != CHAIN_IDS[chain]:
        raise ValueError("RPC chain identity differs")
    target = int(datetime.combine(day, time.max, UTC).timestamp())

    def header(n):
        r = rpc(chain, "eth_getBlockByNumber", [hex(n), False])
        if not r or int(r["number"], 16) != n:
            raise ValueError("Invalid block header")
        return r

    head = int(rpc(chain, "eth_blockNumber", []), 16) - 64
    if head < 1:
        raise ValueError("No finalized successor")
    first = header(1)
    if int(first["timestamp"], 16) > target:
        h = header(0)
        return {
            "chain_id": chain_id,
            "block": 0,
            "hash": h["hash"],
            "timestamp": int(h["timestamp"], 16),
            "before_first_block": True,
            "successor_timestamp": int(first["timestamp"], 16),
        }
    if candidate is not None:
        lo = int(candidate)
        h = header(lo)
        after = header(lo + 1)
        if lo < head and int(h["timestamp"], 16) <= target < int(after["timestamp"], 16):
            return {
                "chain_id": chain_id,
                "block": lo,
                "hash": h["hash"],
                "timestamp": int(h["timestamp"], 16),
                "successor_timestamp": int(after["timestamp"], 16),
            }
    lo, hi = 1, head
    if int(header(hi)["timestamp"], 16) <= target:
        raise ValueError("Cutoff is not finalized")
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if int(header(mid)["timestamp"], 16) <= target:
            lo = mid
        else:
            hi = mid
    h = header(lo)
    after = header(lo + 1)
    return {
        "chain_id": chain_id,
        "block": lo,
        "hash": h["hash"],
        "timestamp": int(h["timestamp"], 16),
        "successor_timestamp": int(after["timestamp"], 16),
    }


class Reader:
    def __init__(self, pins):
        self.pins = pins
        self.psms = {}
        self.reads = []
        self._cache = {}

    def code(self, chain, contract):
        key = (chain, contract, "code")
        if key not in self._cache:
            raw = rpc(chain, "eth_getCode", [contract, hex(self.pins[chain]["block"])])
            self._cache[key] = raw
            self.reads.append(
                dict(chain=chain, contract=contract, method="eth_getCode", result=raw)
            )
        return self._cache[key]

    def call(self, chain, contract, signature, *args):
        data = "0x" + keccak256(signature.encode()).hex()[:8] + "".join(f"{a:064x}" for a in args)
        key = (chain, contract, data)
        if key not in self._cache:
            raw = rpc(
                chain, "eth_call", [{"to": contract, "data": data}, hex(self.pins[chain]["block"])]
            )
            self._cache[key] = raw
            self.reads.append(
                dict(chain=chain, contract=contract, method="eth_call", data=data, result=raw)
            )
        return self._cache[key]

    def number(self, *args):
        raw = self.call(*args)
        if not isinstance(raw, str) or len(raw) != 66:
            raise ValueError("Invalid scalar getter")
        return int(raw, 16)

    def balance(self, chain, token, owner):
        if self.code(chain, token) == "0x":
            return 0
        return self.number(chain, token, "balanceOf(address)", int(owner, 16))

    def native(self, chain, owner):
        raw = rpc(chain, "eth_getBalance", [owner, hex(self.pins[chain]["block"])])
        if not isinstance(raw, str) or not raw.startswith("0x"):
            raise ValueError("Invalid native balance")
        self.reads.append(dict(chain=chain, contract=owner, method="eth_getBalance", result=raw))
        return int(raw, 16)

    def price(self, symbol, chain="ethereum"):
        if symbol in {"ETH", "WETH"}:
            raw = self.call("ethereum", ETH_FEED, "latestRoundData()")
            words = [int(raw[i : i + 64], 16) for i in range(2, len(raw), 64)]
            if len(words) != 5 or words[1] <= 0 or words[1] >= 2**255 or words[4] < words[0]:
                raise ValueError("Invalid ETH oracle")
            stamp = self.pins["ethereum"]["timestamp"]
            if not 0 <= stamp - words[3] <= 7200:
                raise ValueError("Stale ETH oracle")
            return D(words[1]) / 10 ** self.number("ethereum", ETH_FEED, "decimals()")
        if symbol.lower() in {"susds", "susds-arb", "susds-base", "susds-op", "susds-uni"}:
            if chain != "ethereum":
                psm = self.psms[chain]
                provider = self.number(chain, psm, "rateProvider()")
                if not 0 < provider < 2**160:
                    raise ValueError("Invalid local sUSDS rate provider")
                rate = self.number(chain, f"0x{provider:040x}", "getConversionRate()")
                if rate <= 0:
                    raise ValueError("Invalid local sUSDS rate")
                return D(rate) / 10**27
            return D(self.number("ethereum", SUSDS, "convertToAssets(uint256)", 10**18)) / 10**18
        if symbol.upper() in {
            "USDS",
            "DAI",
            "USDC",
            "USDT",
            "USDT0",
            "USDE",
            "PYUSD",
            "RLUSD",
            "USDG",
        }:
            return D(1)
        raise ValueError("No approved onchain valuation")

    def vault(
        self, chain, token, owner, underlying=None, decimals=None, symbol="USDC", extra_shares=0
    ):
        shares = self.balance(chain, token, owner) + extra_shares
        if not shares:
            return D(0)
        asset = "0x" + f"{self.number(chain, token, 'asset()'):040x}"
        if underlying and asset != underlying.lower():
            raise ValueError("Vault asset differs")
        decimals = decimals if decimals is not None else self.number(chain, asset, "decimals()")
        return (
            D(self.number(chain, token, "convertToAssets(uint256)", shares))
            / 10**decimals
            * self.price(symbol, chain)
        )


def collect(cutoff, *, candidates=None, progress=None):
    prime = load_prime_by_id("spark")
    cfg = cash_config()
    candidates = candidates or {}
    chains = sorted(
        {str(v.chain) for v in prime.venues if v.reporting_view.enabled}
        | {r[1] for r in EXTRA_SAVINGS}
    )
    pins = {}
    errors = {}

    def pin(ch):
        try:
            return ch, boundary(ch, cutoff, candidates.get(ch))
        except Exception as e:
            return ch, {"error": type(e).__name__}

    with ThreadPoolExecutor(max_workers=5) as pool:
        for ch, value in pool.map(pin, chains):
            if "block" in value:
                pins[ch] = value
            else:
                errors["chain." + ch] = value["error"]
    reader = Reader(pins)
    reader.psms = {str(chain): spec.address.hex for chain, spec in prime.psm.items()}
    positions = {}
    liabilities = {}
    extra = {}
    ownership = {}
    async_claims = {}

    def attempt(key, fn):
        if progress:
            progress(key)
        start = len(reader.reads)
        try:
            with localcontext() as ctx:
                ctx.prec = 100
                value = fn()
            return dict(
                status="available",
                value=str(value),
                quality="supported",
                read_indices=list(range(start, len(reader.reads))),
            )
        except Exception as e:
            errors[key] = type(e).__name__
            return dict(
                status="unavailable", value=None, reason="Closing chain evidence unavailable"
            )

    def venue_value(v):
        ch = str(v.chain)
        token = v.token.address.hex
        owner = (v.holder_override or prime.alm[v.chain]).hex
        if ch not in pins:
            raise ValueError("Missing chain pin")
        if reader.code(ch, token) == "0x":
            return D(0)
        kind = str(v.pricing_category)
        if kind == "S2":
            asset = reader.number(ch, token, "asset()")
            if asset != int(v.underlying.address.hex, 16):
                raise ValueError("Savings underlying differs")
            return (
                D(reader.number(ch, token, "totalAssets()"))
                / 10**v.underlying.decimals
                * reader.price(v.underlying.symbol)
            )
        if v.lp_kind in {"uniswap_v4", "curve_stableswap"}:
            return amm(v)
        if v.id == "S23":
            raise ValueError("Offchain claim needs principal reconciliation")
        from ..normalize.async_vaults import async_vault_exposure

        exposure = async_vault_exposure(prime, v, pins[ch]["block"])
        async_claims[v.id] = dict(
            shares=str(exposure.shares),
            fixed_cash=str(exposure.redemption_cash),
            claims=list(exposure.claims),
        )
        raw_extra = exposure.shares * 10**v.token.decimals
        if raw_extra != int(raw_extra):
            raise ValueError("Fractional raw async claim")
        units = reader.balance(ch, token, owner) + int(raw_extra)
        fixed = exposure.redemption_cash
        if not units:
            return fixed
        if kind == "B":
            if v.id == "S17":
                return fixed + reader.vault(ch, token, owner, SUSDS, 18, "sUSDS", int(raw_extra))
            if v.sky_savings_token:
                return fixed + D(units) / 10**v.token.decimals * reader.price("sUSDS", ch)
            return fixed + reader.vault(
                ch,
                token,
                owner,
                v.underlying.address.hex,
                v.underlying.decimals,
                v.underlying.symbol,
                int(raw_extra),
            )
        if kind in {"A", "C", "D"}:
            return fixed + (
                D(units)
                / 10**v.token.decimals
                * reader.price(v.underlying.symbol if v.underlying else v.token.symbol)
            )
        # Existing RWA oracle adapter reads chain state; no issuer/dashboard API.
        if kind == "E" and v.nav_oracle and v.nav_oracle.kind != "const_one":
            from ..normalize.prices import get_unit_price

            return fixed + D(units) / 10**v.token.decimals * get_unit_price(v, pins[ch]["block"])
        if kind == "E":
            return fixed + D(units) / 10**v.token.decimals
        raise ValueError("Unsupported closing measure")

    def pending_fees(v, tid):
        from .spark_v4_fees import MANAGER, MOD, POOL, fee_delta, growth_inside

        cfg = v.univ4_pool_key
        if int(cfg.hooks.hex, 16):
            raise ValueError("Unsupported hooked pool")
        encoded = (
            int(cfg.currency0.hex, 16).to_bytes(32, "big")
            + int(cfg.currency1.hex, 16).to_bytes(32, "big")
            + cfg.fee.to_bytes(32, "big")
            + cfg.tick_spacing.to_bytes(32, "big", signed=True)
            + int(cfg.hooks.hex, 16).to_bytes(32, "big")
        )
        pid = keccak256(encoded)
        root = int.from_bytes(keccak256(pid + (6).to_bytes(32, "big")), "big")
        raw = reader.call("ethereum", MANAGER, "getPoolAndPositionInfo(uint256)", tid)
        if len(raw) != 386:
            raise ValueError("Invalid V4 position metadata")
        info = int(raw[-64:], 16)
        lo = (info >> 8) & 0xFFFFFF
        hi = (info >> 32) & 0xFFFFFF
        lo = lo - (1 << 24) if lo >= 1 << 23 else lo
        hi = hi - (1 << 24) if hi >= 1 << 23 else hi
        key = keccak256(
            bytes.fromhex(MANAGER[2:])
            + (lo % (1 << 24)).to_bytes(3, "big")
            + (hi % (1 << 24)).to_bytes(3, "big")
            + tid.to_bytes(32, "big")
        )
        slot = int.from_bytes(keccak256(key + ((root + 6) % MOD).to_bytes(32, "big")), "big")

        def read(n):
            return reader.number("ethereum", POOL, "extsload(bytes32)", n % MOD)

        state = [read(slot + i) for i in range(3)]
        if state[0] >= 1 << 128:
            raise ValueError("Invalid liquidity")
        if not state[0]:
            return [0, 0]
        packed = read(root)
        tick = (packed >> 160) & 0xFFFFFF
        tick = tick - (1 << 24) if tick >= 1 << 23 else tick
        lower = int.from_bytes(
            keccak256((lo % MOD).to_bytes(32, "big") + ((root + 4) % MOD).to_bytes(32, "big")),
            "big",
        )
        upper = int.from_bytes(
            keccak256((hi % MOD).to_bytes(32, "big") + ((root + 4) % MOD).to_bytes(32, "big")),
            "big",
        )
        return [
            fee_delta(
                state[0],
                state[i + 1],
                growth_inside(
                    read(root + 1 + i), read(lower + 1 + i), read(upper + 1 + i), tick, lo, hi
                ),
            )
            for i in range(2)
        ]

    def amm(v):
        from .spark_ownership import WINDOWS

        ch = str(v.chain)
        block = pins[ch]["block"]
        owner = (v.holder_override or prime.alm[v.chain]).hex
        legs = []
        pending_usdt = D(0)
        if v.lp_kind == "uniswap_v4":
            from ..normalize.positions import _venue_v4_pool_key
            from ..normalize.sources.uniswap_v4 import RPCUniswapV4PositionSource
            from .spark_v4_fees import ownership_history

            ids = set()
            for e in ownership_history(owner, block):
                if e.block_number > block:
                    continue
                tid = int(e.topic3, 16)
                if int(e.topic1, 16) == int(owner, 16):
                    ids.discard(tid)
                if int(e.topic2, 16) == int(owner, 16):
                    ids.add(tid)
            for pos in RPCUniswapV4PositionSource().positions_in_pool(
                ch, bytes.fromhex(owner[2:]), sorted(ids), _venue_v4_pool_key(v), block
            ):
                fees = pending_fees(v, pos.token_id)
                for token, raw in [
                    (pos.currency0.hex, pos.amount0),
                    (pos.currency1.hex, pos.amount1),
                ]:
                    decimals = (
                        6
                        if token.lower()
                        in {
                            "0xdac17f958d2ee523a2206206994597c13d831ec7",
                            "0x6c3ea9036406852006290770bedfcaba0e23a0e8",
                        }
                        else 18
                    )
                    if token.lower() == "0xdac17f958d2ee523a2206206994597c13d831ec7":
                        pending_usdt += (
                            D(fees[0 if token.lower() == pos.currency0.hex.lower() else 1])
                            / 10**decimals
                        )
                    legs.append(
                        (
                            token.lower(),
                            D(raw + fees[0 if token.lower() == pos.currency0.hex.lower() else 1])
                            / 10**decimals,
                        )
                    )
        else:
            from ..normalize.sources.curve_pool import CurvePoolSource

            q = reader.balance(ch, v.token.address.hex, owner)
            if not q:
                return D(0)
            pool = CurvePoolSource().read_pool(ch, v.token.address.value, block)
            if not pool.total_supply:
                raise ValueError("LP without pool supply")
            for token, raw in zip(pool.coins, pool.balances, strict=True):
                symbol = (
                    "sUSDS"
                    if token.hex.lower() == SUSDS
                    else "USDT"
                    if token.hex.lower() == "0xdac17f958d2ee523a2206206994597c13d831ec7"
                    else "PYUSD"
                    if token.hex.lower() == "0x6c3ea9036406852006290770bedfcaba0e23a0e8"
                    else "USDS"
                )
                decimals = 6 if symbol in {"USDT", "PYUSD"} else 18
                legs.append(
                    (
                        token.hex.lower(),
                        D(q) / pool.total_supply * D(raw) / 10**decimals * reader.price(symbol),
                    )
                )
        gross = sum((value for _, value in legs), D(0))
        window = WINDOWS.get(v.id)
        sky = (
            sum(
                (
                    value
                    for token, value in legs
                    if token == "0xdac17f958d2ee523a2206206994597c13d831ec7"
                ),
                D(0),
            )
            if window and window[0] <= cutoff <= window[1]
            else D(0)
        )
        # Fee income belongs to Spark, including fees denominated in USDT.
        if window and window[0] <= cutoff <= window[1]:
            sky -= pending_usdt
        ownership[v.id] = dict(
            gross_value=str(gross),
            sky_value=str(sky),
            prime_value=str(gross - sky),
            legs=[dict(token=t, value=str(q)) for t, q in legs],
            basis="Owned LP liquidity at pinned slot0; designated USDT leg only",
            uncollected_fees="included_from_fee_growth_checkpoints"
            if v.lp_kind == "uniswap_v4"
            else "embedded_in_pool_reserves",
            prime_usdt_fee_claim=str(pending_usdt),
        )
        return gross - sky

    for v in prime.venues:
        if not v.reporting_view.enabled or v.id in {"S67", "S68", "S69"}:
            continue
        key = v.id
        target = liabilities if str(v.pricing_category) == "S2" else positions
        target[key] = attempt(key, lambda v=v: venue_value(v))
        target[key].update(label=v.label, chain=str(v.chain), token=v.token.address.hex)
    # Savings-funded WETH supply is an asset distinct from the depositor claim.
    positions["sparklend_weth"] = attempt(
        "sparklend_weth",
        lambda: (
            D(
                reader.balance(
                    "ethereum",
                    "0x59cd1c87501baa753d0b5b5ab5d8416a45cd71db",
                    prime.alm[next(k for k in prime.alm if str(k) == "ethereum")].hex,
                )
            )
            / 10**18
            * reader.price("WETH")
        ),
    )
    for key, ch, vault, asset, decimals, label in EXTRA_SAVINGS:

        def claim(ch=ch, vault=vault, asset=asset, decimals=decimals):
            if reader.code(ch, vault) == "0x":
                return D(0)
            if reader.number(ch, vault, "asset()") != int(asset, 16):
                raise ValueError("Savings asset mismatch")
            return D(reader.number(ch, vault, "totalAssets()")) / 10**decimals

        liabilities[key] = attempt(key, claim)
        liabilities[key].update(label=label, chain=ch, token=vault)
    # Only add idle balances absent from the existing cash perimeter.
    savings = [
        (
            v.id,
            str(v.chain),
            v.token.address.hex,
            v.underlying.address.hex,
            v.underlying.decimals,
            v.underlying.symbol,
        )
        for v in prime.venues
        if str(v.pricing_category) == "S2"
    ] + [
        (k, ch, v, a, dec, "USDC" if "usdc" in k else "USDT")
        for k, ch, v, a, dec, _ in EXTRA_SAVINGS
    ]
    for key, ch, vault, asset, decimals, symbol in savings:
        if ch in cfg["wallets"] and is_cash(cfg, ch, asset, vault):
            extra["idle." + key] = dict(
                status="available",
                value="0",
                quality="supported",
                label="Already included in measured cash",
                basis="cash_perimeter_exclusion",
                read_indices=[],
            )
            continue
        extra["idle." + key] = attempt(
            "idle." + key,
            lambda ch=ch, asset=asset, vault=vault, decimals=decimals, symbol=symbol: (
                D(reader.balance(ch, asset, vault)) / 10**decimals * reader.price(symbol)
            ),
        )
        extra["idle." + key].update(label="Uncounted savings idle: " + key, chain=ch, token=asset)
    extra["pau_susdc"] = attempt("pau_susdc", lambda: reader.vault("ethereum", PAU_SUSDC, PAU))
    for chain, spec in prime.psm.items():
        ch = str(chain)
        address = spec.address.hex
        owner = prime.alm[chain].hex

        def psm_claim(ch=ch, address=address, owner=owner):
            if reader.code(ch, address) == "0x":
                return D(0)
            units = reader.number(ch, address, "shares(address)", int(owner, 16))
            if not units:
                return D(0)
            return D(reader.number(ch, address, "convertToAssetValue(uint256)", units)) / 10**18

        extra["psm." + ch] = attempt("psm." + ch, psm_claim)
        extra["psm." + ch].update(label="PSM3 shares (" + ch + ")", chain=ch, token=address)

    def alm_eth():
        from .spark_onchain import WETH

        units = 0
        for chain, token in WETH.items():
            owner = next(value.hex for key, value in prime.alm.items() if str(key) == chain)
            units += reader.native(chain, owner) + reader.balance(chain, token, owner)
        return D(units) / 10**18 * reader.price("ETH")

    extra["alm_eth"] = attempt("alm_eth", alm_eth)

    def debt():
        raw = reader.call(
            "ethereum", VAT, "ilks(bytes32)", int.from_bytes(prime.ilk_bytes32, "big")
        )
        if len(raw) != 322:
            raise ValueError("Invalid Vat debt read")
        art, rate = int(raw[2:66], 16), int(raw[66:130], 16)
        if art and not rate:
            raise ValueError("Debt rate missing")
        return D(art * rate) / 10**45

    sky_debt = attempt("sky_debt", debt)
    doc = dict(
        schema_version="spark-closing-stocks.v1",
        prime="spark",
        cutoff=str(cutoff),
        pins=pins,
        positions=positions,
        liabilities=liabilities,
        extra_assets=extra,
        sky_debt=sky_debt,
        ownership=ownership,
        async_claims=async_claims,
        errors=errors,
        reads=reader.reads,
        source_policy="Direct finalized chain state; approved par conventions; no BA data",
    )
    doc["proof_hash"] = digest({k: v for k, v in doc.items() if k != "proof_hash"})
    return doc
