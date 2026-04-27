"""
Single-day position-value snapshot via JSON-RPC + external price APIs.

One representative asset per ASSET_CATALOG category. Used to cross-check
Dune-SQL methodology in VALUATION_METHODOLOGY.md.

Run: python3 snapshot.py > out.json
"""

import json
import os
import sys
import time
from decimal import Decimal, getcontext

import requests

getcontext().prec = 50

ETH_RPC = os.environ.get(
    "ETH_RPC", "https://eth-mainnet.g.alchemy.com/v2/7OVFNfBZ3g3aRyji5iTNW"
)
BASE_RPC = os.environ.get("BASE_RPC", "https://mainnet.base.org")
ETHERSCAN_KEY = os.environ.get("ETHERSCAN_KEY", "HQ7VNQSBCGG5JA5D7J7GUCS337221SV1BA")

# Prime-agent ALM proxies
SPARK_ETH_ALM = "0x1601843c5e9bc251a3272907010afa41fa18347e"
SPARK_BASE_ALM = "0x2917956eff0b5eaf030abdb4ef4296df775009ca"
SPARK_ARB_ALM = "0x92afd6f2385a90e44da3a8b60fe36f6cbe1d8709"
GROVE_ETH_ALM = "0x491edfb0b8b608044e227225c715981a30f3a44e"

# --- Function selectors (first 4 bytes of keccak256 of the signature) ---
SEL_BALANCE_OF = "0x70a08231"           # balanceOf(address)
SEL_DECIMALS = "0x313ce567"             # decimals()
SEL_TOTAL_SUPPLY = "0x18160ddd"         # totalSupply()
SEL_CONVERT_TO_ASSETS = "0x07a2d13a"    # convertToAssets(uint256)
SEL_SYMBOL = "0x95d89b41"               # symbol()


def rpc(url: str, method: str, params: list):
    r = requests.post(
        url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"{method} error: {body['error']}")
    return body["result"]


def pad_address(a: str) -> str:
    return a.lower().removeprefix("0x").rjust(64, "0")


def pad_uint(n: int) -> str:
    return hex(n)[2:].rjust(64, "0")


def eth_call(url: str, to: str, data: str, block="latest") -> str:
    return rpc(url, "eth_call", [{"to": to, "data": data}, block])


def balance_of(url: str, token: str, holder: str, block="latest") -> int:
    return int(eth_call(url, token, SEL_BALANCE_OF + pad_address(holder), block), 16)


def decimals_of(url: str, token: str) -> int:
    return int(eth_call(url, token, SEL_DECIMALS), 16)


def total_supply(url: str, token: str) -> int:
    return int(eth_call(url, token, SEL_TOTAL_SUPPLY), 16)


def convert_to_assets(url: str, vault: str, shares: int) -> int:
    return int(eth_call(url, vault, SEL_CONVERT_TO_ASSETS + pad_uint(shares)), 16)


def native_balance(url: str, holder: str) -> int:
    return int(rpc(url, "eth_getBalance", [holder, "latest"]), 16)


def latest_block(url: str) -> int:
    return int(rpc(url, "eth_blockNumber", []), 16)


def block_timestamp(url: str, block_num: int) -> int:
    block = rpc(url, "eth_getBlockByNumber", [hex(block_num), False])
    return int(block["timestamp"], 16)


def coingecko_price(token_id: str) -> float:
    # polite: 1 req/s
    r = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": token_id, "vs_currencies": "usd"},
        timeout=30,
    )
    r.raise_for_status()
    return float(r.json()[token_id]["usd"])


# -----------------------------------------------------------------------------
# Per-category valuation
# -----------------------------------------------------------------------------

def category_A_par_stable():
    """PYUSD held by Spark ETH ALM — balance × $1.00.
    (USDC @ Spark ETH is zero — ALM is a routing proxy, not an idle wallet.)"""
    token = "0x6c3ea9036406852006290770bedfcaba0e23a0e8"  # PYUSD
    raw = balance_of(ETH_RPC, token, SPARK_ETH_ALM)
    dec = decimals_of(ETH_RPC, token)
    balance = Decimal(raw) / Decimal(10**dec)
    return {
        "category": "A",
        "label": "Par stablecoin — PYUSD @ Spark ETH ALM",
        "chain": "ethereum",
        "token": token,
        "holder": SPARK_ETH_ALM,
        "balance_raw": str(raw),
        "decimals": dec,
        "balance_units": str(balance),
        "unit_price_usd": "1.00",
        "value_usd": str(balance * Decimal("1.00")),
    }


def category_B_erc4626():
    """sUSDS @ Spark Ethereum ALM — balance × convertToAssets / 1e18 × USDS($1)"""
    # Try Spark ETH ALM first, fall back to Spark Arbitrum ALM if balance=0.
    token_eth = "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
    chain = "ethereum"
    url = ETH_RPC
    holder = SPARK_ETH_ALM
    raw = balance_of(url, token_eth, holder)
    if raw == 0:
        # fall back to Arbitrum
        token_arb = "0xddb46999f8891663a8f2828d25298f70416d7610"
        arb_rpc = f"https://arb-mainnet.g.alchemy.com/v2/7OVFNfBZ3g3aRyji5iTNW"
        try:
            raw_arb = balance_of(arb_rpc, token_arb, SPARK_ARB_ALM)
        except Exception as e:
            raw_arb = 0
        if raw_arb > 0:
            token_eth = token_arb
            holder = SPARK_ARB_ALM
            chain = "arbitrum"
            url = arb_rpc
            raw = raw_arb
    share_dec = decimals_of(url, token_eth)
    # convertToAssets(shares) → underlying amount in underlying decimals
    assets_raw = convert_to_assets(url, token_eth, raw) if raw > 0 else 0
    # pps = assets per 1 share (1e18 if share_dec=18)
    pps_raw = convert_to_assets(url, token_eth, 10 ** share_dec)
    # USDS is 18 decimals, price $1
    underlying_dec = 18
    balance_units = Decimal(raw) / Decimal(10**share_dec)
    value_usds = Decimal(assets_raw) / Decimal(10**underlying_dec)
    pps = Decimal(pps_raw) / Decimal(10**underlying_dec)
    return {
        "category": "B",
        "label": f"ERC-4626 — sUSDS @ Spark {chain.upper()} ALM",
        "chain": chain,
        "token": token_eth,
        "holder": holder,
        "balance_raw": str(raw),
        "share_decimals": share_dec,
        "balance_units": str(balance_units),
        "convertToAssets_for_1_share": str(pps),
        "underlying_decimals": underlying_dec,
        "underlying_assets_raw": str(assets_raw),
        "unit_price_usd": "1.00 (USDS peg)",
        "value_usd": str(value_usds),
    }


def category_C_aave_atoken():
    """aHorRwaRLUSD @ Grove ETH ALM — Aave Horizon RWA market aToken.
    (aEthUSDC @ Spark ETH is dust $0.06 — not a meaningful test case.)"""
    token = "0xe3190143eb552456f88464662f0c0c4ac67a77eb"  # aHorRwaRLUSD
    raw = balance_of(ETH_RPC, token, GROVE_ETH_ALM)
    dec = decimals_of(ETH_RPC, token)  # 18 (matches RLUSD)
    balance = Decimal(raw) / Decimal(10**dec)
    return {
        "category": "C",
        "label": "Aave v3 aToken — aHorRwaRLUSD @ Grove ETH ALM",
        "chain": "ethereum",
        "token": token,
        "holder": GROVE_ETH_ALM,
        "balance_raw_rebased": str(raw),
        "decimals": dec,
        "balance_units": str(balance),
        "unit_price_usd": "1.00 (RLUSD underlying)",
        "value_usd": str(balance),
        "note": "balanceOf returns rebased amount — interest already accrued",
    }


def category_D_sparklend():
    """spUSDC @ Spark ETH ALM — same mechanics as Aave aToken"""
    token = "0x377c3bd93f2a2984e1e7be6a5c22c525ed4a4815"  # spUSDC
    raw = balance_of(ETH_RPC, token, SPARK_ETH_ALM)
    dec = decimals_of(ETH_RPC, token)
    balance = Decimal(raw) / Decimal(10**dec)
    return {
        "category": "D",
        "label": "SparkLend spToken — spUSDC @ Spark ETH ALM",
        "chain": "ethereum",
        "token": token,
        "holder": SPARK_ETH_ALM,
        "balance_raw_rebased": str(raw),
        "decimals": dec,
        "balance_units": str(balance),
        "unit_price_usd": "1.00 (USDC underlying)",
        "value_usd": str(balance),
        "note": "SparkLend is an Aave-v3 fork; balanceOf is rebased same as C",
    }


def category_E_rwa():
    """JTRSY @ Grove ETH ALM — on-chain NAV probe + CoinGecko fallback vs $1 placeholder."""
    token = "0x8c213ee79581ff4984583c6a801e5263418c4b86"
    raw = balance_of(ETH_RPC, token, GROVE_ETH_ALM)
    dec = decimals_of(ETH_RPC, token)
    balance = Decimal(raw) / Decimal(10**dec)

    # 1) On-chain probe — try standard price-getter selectors. JTRSY reverts on all
    # of pricePerShare / sharePrice / latestAnswer / convertToAssets.
    onchain_nav = None
    for sel in ("0x87b0c7a7", "0x99530b06", "0x50d25bcd", "0x07a2d13a"):
        try:
            data = sel + (pad_uint(10**dec) if sel == "0x07a2d13a" else "")
            r = eth_call(ETH_RPC, token, data)
            onchain_nav = r
            break
        except Exception:
            pass

    # 2) Off-chain — CoinGecko (free tier, rate-limited)
    nav_coingecko = Decimal(
        str(coingecko_price("janus-henderson-anemoy-treasury-fund"))
    )

    # 3) Placeholder baseline
    nav_placeholder = Decimal("1.00")

    return {
        "category": "E",
        "label": "RWA tranche — JTRSY @ Grove ETH ALM",
        "chain": "ethereum",
        "token": token,
        "holder": GROVE_ETH_ALM,
        "balance_raw": str(raw),
        "decimals": dec,
        "balance_units": str(balance),
        "nav_methods": {
            "onchain_getter": onchain_nav or "all probed selectors revert",
            "coingecko_usd": str(nav_coingecko),
            "placeholder_usd": str(nav_placeholder),
        },
        "value_usd_placeholder": str(balance * nav_placeholder),
        "value_usd_coingecko": str(balance * nav_coingecko),
        "unit_price_usd": str(nav_coingecko),
        "value_usd": str(balance * nav_coingecko),
        "note": "No on-chain NAV getter; CoinGecko listing provides a market-implied NAV. Production should subscribe to issuer (Centrifuge) API for authoritative daily NAV.",
    }


def category_F_lp():
    """Curve sUSDSUSDT LP @ Spark ETH ALM — two methods compared:
       (A) virtual_price shortcut: lp_balance × virtual_price / 1e18
       (B) reserves reconstruction: share × Σ balance[i] × price[i]
    """
    pool = "0x00836fe54625be242bcfa286207795405ca4fd10"  # Curve sUSDSUSDT pool (also LP token)
    susds = "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
    usdt = "0xdac17f958d2ee523a2206206994597c13d831ec7"

    lp_balance_raw = balance_of(ETH_RPC, pool, SPARK_ETH_ALM)
    total_supply = int(eth_call(ETH_RPC, pool, SEL_TOTAL_SUPPLY), 16)
    virtual_price = int(eth_call(ETH_RPC, pool, "0xbb7b8b80"), 16)  # get_virtual_price()
    bal0 = int(eth_call(ETH_RPC, pool, "0x4903b0d1" + pad_uint(0)), 16)  # balances(0) sUSDS
    bal1 = int(eth_call(ETH_RPC, pool, "0x4903b0d1" + pad_uint(1)), 16)  # balances(1) USDT

    # sUSDS price-per-share = convertToAssets(1 share) in USDS terms
    susds_pps_raw = convert_to_assets(ETH_RPC, susds, 10**18)
    susds_price_usd = Decimal(susds_pps_raw) / Decimal(10**18)  # USDS pegs to $1

    lp_units = Decimal(lp_balance_raw) / Decimal(10**18)
    vp = Decimal(virtual_price) / Decimal(10**18)

    # Method A — virtual_price shortcut (assumes pool base unit ≈ $1)
    value_usd_A = lp_units * vp

    # Method B — reconstruct from reserves
    share = Decimal(lp_balance_raw) / Decimal(total_supply)
    susds_in_share = Decimal(bal0) * share / Decimal(10**18)
    usdt_in_share = Decimal(bal1) * share / Decimal(10**6)
    value_usd_B = (susds_in_share * susds_price_usd) + (usdt_in_share * Decimal("1.00"))

    return {
        "category": "F",
        "label": "Curve sUSDSUSDT LP @ Spark ETH ALM",
        "chain": "ethereum",
        "pool_and_lp_token": pool,
        "holder": SPARK_ETH_ALM,
        "lp_balance_raw": str(lp_balance_raw),
        "lp_balance_units": str(lp_units),
        "pool_total_supply": str(total_supply),
        "share_of_pool_pct": str(share * 100),
        "virtual_price_1e18": str(vp),
        "reserves": {
            "susds_raw": str(bal0),
            "susds_units": str(Decimal(bal0) / Decimal(10**18)),
            "usdt_raw": str(bal1),
            "usdt_units": str(Decimal(bal1) / Decimal(10**6)),
        },
        "underlying_prices": {
            "susds_price_usd": str(susds_price_usd),
            "usdt_price_usd": "1.00",
        },
        "method_A_virtual_price_usd": str(value_usd_A),
        "method_B_reserves_usd": str(value_usd_B),
        "value_usd": str(value_usd_B),  # B is the more accurate of the two
    }


def category_G_native_wrapped():
    """Native ETH + WETH @ Grove ETH ALM — priced via CoinGecko.
    (Spark ETH ALM holds 0 of both; Grove ALM has 0.05 ETH gas reserve.)"""
    holder = GROVE_ETH_ALM
    token_weth = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
    raw_weth = balance_of(ETH_RPC, token_weth, holder)
    raw_eth = native_balance(ETH_RPC, holder)
    eth_price = coingecko_price("ethereum")
    weth_units = Decimal(raw_weth) / Decimal(10**18)
    eth_units = Decimal(raw_eth) / Decimal(10**18)
    return {
        "category": "G",
        "label": "Native/wrapped — native ETH + WETH @ Grove ETH ALM",
        "chain": "ethereum",
        "holder": holder,
        "weth": {
            "token": token_weth,
            "balance_raw": str(raw_weth),
            "balance_units": str(weth_units),
            "value_usd": str(weth_units * Decimal(str(eth_price))),
        },
        "native_eth": {
            "balance_raw_wei": str(raw_eth),
            "balance_units": str(eth_units),
            "value_usd": str(eth_units * Decimal(str(eth_price))),
        },
        "eth_usd_price": str(eth_price),
        "value_usd": str((weth_units + eth_units) * Decimal(str(eth_price))),
    }


def category_H_governance():
    """MORPHO @ Spark Base ALM — priced via CoinGecko"""
    token = "0xbaa5cc21fd487b8fcc2f632f3f4e8d37262a0842"
    raw = balance_of(BASE_RPC, token, SPARK_BASE_ALM)
    dec = decimals_of(BASE_RPC, token)
    balance = Decimal(raw) / Decimal(10**dec)
    morpho_price = coingecko_price("morpho")
    return {
        "category": "H",
        "label": "Governance — MORPHO @ Spark Base ALM",
        "chain": "base",
        "token": token,
        "holder": SPARK_BASE_ALM,
        "balance_raw": str(raw),
        "decimals": dec,
        "balance_units": str(balance),
        "unit_price_usd": str(morpho_price),
        "value_usd": str(balance * Decimal(str(morpho_price))),
    }


def main():
    eth_block = latest_block(ETH_RPC)
    eth_ts = block_timestamp(ETH_RPC, eth_block)
    base_block = latest_block(BASE_RPC)
    base_ts = block_timestamp(BASE_RPC, base_block)
    out = {
        "snapshot_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "eth_block": eth_block,
        "eth_block_ts_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(eth_ts)
        ),
        "base_block": base_block,
        "base_block_ts_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(base_ts)
        ),
        "results": {},
    }
    for name, fn in [
        ("A", category_A_par_stable),
        ("B", category_B_erc4626),
        ("C", category_C_aave_atoken),
        ("D", category_D_sparklend),
        ("E", category_E_rwa),
        ("F", category_F_lp),
        ("G", category_G_native_wrapped),
        ("H", category_H_governance),
    ]:
        try:
            out["results"][name] = fn()
        except Exception as e:
            out["results"][name] = {"category": name, "error": str(e)}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
