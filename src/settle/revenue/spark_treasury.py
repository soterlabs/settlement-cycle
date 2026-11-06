"""Reporting-only treasury receipt and disposal bridges; see ACCOUNTING_RULES.md.

Every month bridges independently pinned inventories to external token movements.
Internal transfers and wrapping cancel. Disposal proceeds replace quoted outflow
values, so sale differences enter gains once, not reward/fee income again.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D
from decimal import localcontext
from functools import cache

import requests

from ..domain.primes import Address, Chain
from ..extract.cache import cached
from ..extract.rpc import balance_of, native_balance
from ..extract.transfer_logs import TRANSFER_TOPIC0
from .cash_flow.config import load
from .spark_onchain import (
    ETH_ALM,
    MORPHO,
    OTHER_NONSTABLE_RESERVES,
    SPWETH,
    TREASURIES,
    WAVAX,
    WETH,
    market_price,
    topic,
)
from .verification import digest

OPS = "0x2e1b01adabb8d4981863394bea23a1263cbaedfc"
ZERO = "0x" + "0" * 40


def cell(value, **extra):
    return dict(
        status="available",
        quality="supported",
        recognition="receipt_in_kind",
        value=str(value),
        basis="event_valued_treasury_inventory_bridge",
        **extra,
    )


@cached(source_id="spark-reporting.native-treasury-history.v2")
def native_events(owner, start, end):
    root = "https://api.routescan.io/v2/network/mainnet/evm/43114"
    response = requests.get(root + "/blocks", params={"limit": 1}, timeout=30)
    response.raise_for_status()
    if response.json()["items"][0]["number"] < end + 128:
        raise ValueError("Native index has not finalized the cutoff")
    result = []
    for action in ("txlist", "txlistinternal"):
        response = requests.get(
            root + "/etherscan/api",
            params=dict(
                module="account",
                action=action,
                address=owner,
                startblock=start + 1,
                endblock=end,
                page=1,
                offset=10000,
                sort="asc",
            ),
            timeout=30,
        )
        response.raise_for_status()
        raw = response.json()
        rows = raw.get("result")
        if not isinstance(rows, list) or len(rows) >= 10000 or raw.get("status") not in ("0", "1"):
            raise ValueError("Native activity is incomplete")
        if raw["status"] == "0" and (rows or raw.get("message") != "No transactions found"):
            raise ValueError("Native empty history is not certified")
        for row in rows:
            if not start < int(row["blockNumber"]) <= end:
                raise ValueError("Native activity boundary differs")
            if action == "txlist" and row["from"].lower() == owner:
                # Failed calls still consume gas, but never transfer the stated value.
                gas = D(row["gasUsed"]) * D(row["gasPrice"]) / 10**18
                if gas:
                    result.append(
                        dict(
                            block=int(row["blockNumber"]),
                            timestamp=int(row["timeStamp"]),
                            tx=row["hash"].lower(),
                            src=owner,
                            dst=ZERO,
                            units=str(gas),
                            kind="gas",
                            identity="gas:" + row["hash"],
                        )
                    )
            if row.get("isError") == "1" or row.get("type", "call") not in (
                "call",
                "suicide",
                "create",
            ):
                continue  # delegatecall does not transfer value between accounts.
            if not int(row["value"]):
                continue
            if not start < int(row["blockNumber"]) <= end:
                raise ValueError("Native activity boundary differs")
            result.append(
                dict(
                    block=int(row["blockNumber"]),
                    timestamp=int(row["timeStamp"]),
                    tx=row["hash"].lower(),
                    src=row["from"].lower(),
                    dst=row["to"].lower(),
                    units=str(D(row["value"]) / 10**18),
                    identity=action + ":" + row["hash"] + ":" + row.get("traceId", ""),
                )
            )
    if len({r["identity"] for r in result}) != len(result):
        raise ValueError("Duplicate native event")
    return result


def collect(source):
    config = load()
    before, end = source.period.start - timedelta(days=1), source.period.end
    result = {"fees": {}, "rewards": {}, "gains": {}, "errors": {}, "cash_disposals": []}
    # MORPHO rewards belong to the reporting entity, including Foundation.
    eth_owners = [
        a
        for a, r in config["wallets"]["ethereum"].items()
        if r["role"] not in ("savings_idle", "intents")
    ]

    @cache
    def price(coin, stamp):
        return D(market_price(coin, stamp)["price"])

    def token_events(chain, tokens, owners):
        rows = source.logs(
            chain,
            [
                {"address": tokens, "topics": [[TRANSFER_TOPIC0], [topic(a) for a in owners]]},
                {"address": tokens, "topics": [[TRANSFER_TOPIC0], [], [topic(a) for a in owners]]},
            ],
        )
        unique = {(r.block_number, r.log_index): r for r in rows}
        return list(sorted(unique.values(), key=lambda r: (r.block_number, r.log_index)))

    def token_balance(chain, token, owner, day):
        block = source.block(chain, day)
        if source.code(chain, token, block) == "0x":
            return D(0)
        return D(balance_of(Chain(chain), Address.from_str(token), Address.from_str(owner), block))

    @cache
    def other_asset_events(chain):
        tokens = (
            [MORPHO["ethereum"], WETH["ethereum"], SPWETH, *OTHER_NONSTABLE_RESERVES]
            if chain == "ethereum"
            else [MORPHO["base"]]
            if chain == "base"
            else [WAVAX]
        )
        return token_events(chain, tokens, list(config["wallets"][chain]))

    def bridge(key, chain, tokens, owners, *, reward=False, native=False):
        # Token specifications are (address, decimals, price coin).
        specs = {t: (scale, coin) for t, scale, coin in tokens}
        rows = token_events(chain, list(specs), owners)
        events = [
            dict(
                block=r.block_number,
                timestamp=r.block_time,
                tx=r.transaction_hash.lower(),
                src="0x" + r.topic1[-40:],
                dst="0x" + r.topic2[-40:],
                units=str(D(int(r.data, 16)) / 10 ** specs[r.address][0]),
                token=r.address,
                coin=specs[r.address][1],
                identity=f"{r.block_number}:{r.log_index}",
            )
            for r in rows
            if int(r.data, 16)
        ]
        if native:
            for owner in owners:
                events += [
                    dict(r, token=ZERO, coin="coingecko:avalanche-2")
                    for r in native_events(
                        owner, source.opening_pins[chain], source.closing_pins[chain]
                    )
                ]
            events = list({r["identity"]: r for r in events}.values())
        # Stable cash legs in the same transaction prove a purchase/disposal.
        cash = {t: m for t, m in config["tokens"][chain].items() if m["cash"]}
        cash_owners = [
            a
            for a, r in config["wallets"][chain].items()
            if r["role"] not in ("savings_idle", "intents")
        ]
        stable = token_events(chain, list(cash), cash_owners)
        cash_by_tx = defaultdict(D)
        for r in stable:
            sign = (("0x" + r.topic2[-40:]) in cash_owners) - (
                ("0x" + r.topic1[-40:]) in cash_owners
            )
            cash_by_tx[r.transaction_hash.lower()] += (
                D(int(r.data, 16)) / 10 ** cash[r.address]["decimals"] * sign
            )
        inventories = []
        inventory_units = []
        for day in (before, end):
            value = D(0)
            stamp = int(datetime.combine(day, datetime.max.time(), UTC).timestamp())
            combined_units = D(0)
            for token, scale, coin in tokens:
                units = sum((token_balance(chain, token, a, day) for a in owners), D(0)) / 10**scale
                combined_units += units
                if units:
                    value += units * price(coin, stamp)
            if native:
                units = sum(
                    (
                        D(
                            native_balance(
                                Chain(chain), Address.from_str(a), source.block(chain, day)
                            )
                        )
                        / 10**18
                        for a in owners
                    ),
                    D(0),
                )
                if units:
                    value += units * price("coingecko:avalanche-2", stamp)
                combined_units += units
            inventories.append(value)
            inventory_units.append(combined_units)
        flows = defaultdict(D)
        rewards = fees = D(0)
        receipts = []
        owned = set(owners)
        net_units = D(0)
        for e in events:
            # Native/wrapped AVAX conversion is one inventory, not an external flow.
            if native and (
                (e["token"] == ZERO and e["dst"] == WAVAX)
                or (e["token"] == WAVAX and e["src"] == ZERO)
                or (e["token"] == WAVAX and e["dst"] == ZERO)
                or (e["token"] == ZERO and e["src"] == WAVAX)
            ):
                continue
            sign = (e["dst"] in owned) - (e["src"] in owned)
            if not sign:
                continue
            net_units += D(e["units"]) * sign
            value = D(e["units"]) * price(e["coin"], e["timestamp"])
            flows[e["tx"] + (":gas" if e.get("kind") == "gas" else "")] += value * sign
            fee = e["src"] in TREASURIES and sign > 0
            earned = reward and sign > 0 and cash_by_tx[e["tx"]] >= 0 and e["src"] not in owned
            if key == "morpho":
                earned = earned and e["dst"] == "0x92e4629a4510af5819d7d1601464c233599ff5ec"
            # The small AVAX setup transfer is not evidenced as a reward.
            earned = earned and not (native and D(e["units"]) == D("0.02"))
            if fee or earned:
                fees += value if fee else D(0)
                rewards += value if earned and not fee else D(0)
                receipts.append(dict(e, usd_value=str(value), kind="fee" if fee else "reward"))
        # Swap consideration replaces quoted flow, not adds a second gain.
        for tx, flow in list(flows.items()):
            consideration = cash_by_tx[tx]
            if consideration and any(
                r.transaction_hash.lower() == tx
                and r.address not in specs
                and int(r.data, 16)
                and "0x" + r.topic1[-40:] in cash_owners
                and "0x" + r.topic2[-40:] not in cash_owners
                for r in other_asset_events(chain)
            ):
                raise ValueError("Multi-asset sale needs an explicit consideration split")
            if flow < 0 and consideration > 0:
                flows[tx] = -consideration
                result["cash_disposals"].append(
                    dict(chain=chain, tx_hash=tx, amount_usd=str(consideration), asset=key)
                )
            elif flow > 0 and consideration < 0:
                flows[tx] = -consideration
        if (native or key.startswith("morpho")) and inventory_units[
            0
        ] + net_units != inventory_units[1]:
            raise ValueError("Treasury token/native units do not reproduce closing inventory")
        gain = inventories[1] - inventories[0] - sum(flows.values(), D(0))
        proof = dict(
            opening_value=str(inventories[0]),
            closing_value=str(inventories[1]),
            net_external_value=str(sum(flows.values(), D(0))),
            receipts=receipts,
            event_digest=digest(events),
            owners=owners,
            chain=chain,
            opening_units=str(inventory_units[0]),
            closing_units=str(inventory_units[1]),
            net_external_units=str(net_units),
        )
        result["gains"][key] = cell(gain, inventory_bridge=proof)
        result["fees"][key] = cell(fees, receipts=receipts)
        result["rewards"][key] = cell(rewards, receipts=receipts)

    tasks = [
        (
            "morpho",
            lambda: bridge(
                "morpho",
                "ethereum",
                [(MORPHO["ethereum"], 18, "ethereum:" + MORPHO["ethereum"])],
                eth_owners,
                reward=True,
            ),
        ),
        (
            "morpho_base",
            lambda: bridge(
                "morpho_base",
                "base",
                [(MORPHO["base"], 18, "ethereum:" + MORPHO["ethereum"])],
                [source.prime.alm[Chain("base")].hex.lower()],
            ),
        ),
        (
            "avax",
            lambda: bridge(
                "avax",
                "avalanche_c",
                [(WAVAX, 18, "coingecko:avalanche-2")],
                list(dict.fromkeys([OPS, source.prime.alm[Chain("avalanche_c")].hex.lower()])),
                reward=True,
                native=True,
            ),
        ),
    ]

    # Discover the underlying of reviewed SparkLend reserve tokens; no guessed prices.
    def reserves():
        tokens = []
        for token in (SPWETH, *OTHER_NONSTABLE_RESERVES):
            rows = token_events("ethereum", [token], [OPS, ETH_ALM])
            quantities = [
                token_balance("ethereum", token, owner, day)
                for owner in (OPS, ETH_ALM)
                for day in (before, end)
            ]
            if not rows and not any(quantities):
                continue
            block = source.closing_pins["ethereum"]
            underlying = (
                f"0x{source.number('ethereum', token, 'UNDERLYING_ASSET_ADDRESS()', block):040x}"
            )
            decimals = source.number("ethereum", token, "decimals()", block)
            tokens.append((token, decimals, "ethereum:" + underlying))
        # Include wrapped ETH proceeds while held at Ops, avoiding a phantom disposal.
        tokens.append((WETH["ethereum"], 18, "coingecko:ethereum"))
        bridge("reserve_nonstable", "ethereum", tokens, [OPS])
        direct = D(0)
        for row in token_events("ethereum", [t for t, _, _ in tokens], [ETH_ALM]):
            if "0x" + row.topic1[-40:] not in TREASURIES or "0x" + row.topic2[-40:] != ETH_ALM:
                continue
            _, decimals, coin = next(t for t in tokens if t[0] == row.address)
            direct += D(int(row.data, 16)) / 10**decimals * price(coin, row.block_time)
        fees = result["fees"]["reserve_nonstable"]
        fees["value"] = str(D(fees["value"]) + direct)
        fees["direct_alm_receipts_usd"] = str(direct)
        from .spark_onchain import STABLE_RESERVES

        receipts = source.logs(
            "ethereum",
            [
                {
                    "topics": [
                        [TRANSFER_TOPIC0],
                        [topic(a) for a in TREASURIES],
                        [topic(OPS), topic(ETH_ALM)],
                    ]
                }
            ],
        )
        valued = set(STABLE_RESERVES) | {t for t, _, _ in tokens}
        unknown = sorted(
            {r.address for r in receipts if r.address not in valued and int(r.data, 16)}
        )
        if unknown:
            fees.update(
                status="partial", reason="unvalued_reserve_fee_tokens", unknown_tokens=unknown
            )

    tasks.append(("reserve_nonstable", reserves))
    for key, task in tasks:
        try:
            with localcontext() as ctx:
                ctx.prec = 100
                task()
        except Exception as exc:
            result["errors"][key] = type(exc).__name__
            for family in ("fees", "rewards", "gains"):
                result[family][key] = dict(
                    status="unavailable", reason="treasury_source_not_verified"
                )
    if all(
        result["gains"].get(k, {}).get("status") == "available" for k in ("morpho", "morpho_base")
    ):
        result["gains"]["morpho"]["base_inventory_bridge"] = result["gains"]["morpho_base"][
            "inventory_bridge"
        ]
        result["gains"]["morpho"]["value"] = str(
            D(result["gains"]["morpho"]["value"]) + D(result["gains"]["morpho_base"]["value"])
        )
    elif "morpho_base" in result["errors"]:
        result["gains"]["morpho"] = dict(
            status="unavailable", reason="Base_MORPHO_inventory_not_verified"
        )
    return result
