"""V4 earned fees from authenticated growth checkpoints and pending claims.

Hookless, configured Spark NFTs only. No reward transfers or principal residuals
are used as fees. Unsupported ownership or same-block ambiguity fails closed.
"""

from collections import Counter, defaultdict
from decimal import Decimal
from functools import cache

from ..extract._keccak import keccak256
from ..extract.cache import cached
from ..extract.transfer_logs import TRANSFER_TOPIC0
from ..extract.uniswap_v3 import get_amounts_for_liquidity, get_sqrt_ratio_at_tick
from ..extract.uniswap_v4 import TOPIC_MODIFY_LIQUIDITY
from .spark_onchain import topic
from .verification import digest

POOL = "0x000000000004444c5dc75cb358380d2e3de08a90"
MANAGER = "0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e"
MOD, Q128 = 1 << 256, 1 << 128
DECIMALS = {
    "0x6c3ea9036406852006290770bedfcaba0e23a0e8": 6,
    "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,
    "0xdc035d45d973e3ec169d2276ddab16f1e407384f": 18,
    "0x8292bb45bf1ee4d140127049757c2e0ff06317ed": 18,
}


def growth_inside(global_growth, lower_outside, upper_outside, tick, lower, upper):
    below = lower_outside if tick >= lower else (global_growth - lower_outside) % MOD
    above = upper_outside if tick < upper else (global_growth - upper_outside) % MOD
    return (global_growth - below - above) % MOD


def fee_delta(liquidity, previous, current):
    return liquidity * ((current - previous) % MOD) // Q128


def collect(source, venue):
    config = venue.univ4_pool_key

    def addr(value):
        return value.hex if hasattr(value, "hex") else str(value).lower()

    c0, c1, hooks = addr(config.currency0), addr(config.currency1), addr(config.hooks)
    if int(hooks, 16) or c0 not in DECIMALS or c1 not in DECIMALS:
        raise ValueError("V4 reporting requires reviewed stable, hookless pool")
    encoded = (
        int(c0, 16).to_bytes(32, "big")
        + int(c1, 16).to_bytes(32, "big")
        + config.fee.to_bytes(32, "big")
        + config.tick_spacing.to_bytes(32, "big", signed=True)
        + int(hooks, 16).to_bytes(32, "big")
    )
    pool_id = keccak256(encoded)
    root = int.from_bytes(keccak256(pool_id + (6).to_bytes(32, "big")), "big")
    holder = source.prime.alm[venue.chain].hex.lower()
    ids = set(venue.univ4_token_ids)
    rows = source.logs(
        "ethereum",
        [
            {
                "address": [POOL],
                "topics": [[TOPIC_MODIFY_LIQUIDITY], ["0x" + pool_id.hex()], [topic(MANAGER)]],
            }
        ],
    )
    # Opening inventory requires prior ERC721 ownership events, not prior
    # revenue calculations. The lookup also prevents newly discovered NFTs
    # disappearing from reporting when the next calendar month begins.
    if hasattr(source, "opening_pins"):
        history = ownership_history(holder, source.closing_pins["ethereum"])
        opening_ids = set()
        incoming = set()
        for r in history:
            tid = int(r.topic3, 16)
            if r.block_number <= source.opening_pins["ethereum"]:
                if r.topic1 == topic(holder):
                    opening_ids.discard(tid)
                if r.topic2 == topic(holder):
                    opening_ids.add(tid)
            elif r.topic2 == topic(holder):
                incoming.add(tid)
        candidates = opening_ids | incoming
    else:
        received = source.logs(
            "ethereum", [{"address": [MANAGER], "topics": [[TRANSFER_TOPIC0], [], [topic(holder)]]}]
        )
        candidates = {int(r.topic3, 16) for r in received if r.topic3}
    pool_ids = {int(r.data[-64:], 16) for r in rows if len(r.data) == 258}
    discovered = (candidates & pool_ids) - ids
    for tid in candidates - pool_ids - ids:
        raw = source.call(
            "ethereum",
            MANAGER,
            "getPoolAndPositionInfo(uint256)",
            source.block("ethereum", source.period.end),
            tid,
        )
        if len(raw) == 386 and keccak256(bytes.fromhex(raw[2:322])) == pool_id:
            discovered.add(tid)
    ids |= discovered
    events = []
    for row in rows:
        if len(row.data) != 258:
            raise ValueError("Invalid V4 modification shape")
        words = [int(row.data[2 + i * 64 : 66 + i * 64], 16) for i in range(4)]
        if words[3] in ids:
            events.append((row, words))
    counts = Counter((r.block_number, w[3]) for r, w in events)
    if any(n != 1 for n in counts.values()):
        raise ValueError(
            "Multiple V4 position modifications in one block require transaction state"
        )
    nft_moves = source.logs(
        "ethereum",
        [
            {
                "address": [MANAGER],
                "topics": [[TRANSFER_TOPIC0], [], [], ["0x" + f"{tid:064x}" for tid in ids]],
            }
        ],
    )
    if any(
        r.topic1 not in (topic(holder), topic("0x" + "0" * 40))
        or r.topic2 not in (topic(holder), topic("0x" + "0" * 40))
        for r in nft_moves
    ):
        raise ValueError("V4 NFT ownership changed outside Spark")
    before = source.period.start.fromordinal(source.period.start.toordinal() - 1)
    start, end = source.block("ethereum", before), source.block("ethereum", source.period.end)

    def read(slot, block):
        raw = source.call("ethereum", POOL, "extsload(bytes32)", block, slot % MOD)
        if len(raw) != 66:
            raise ValueError("Invalid V4 storage response")
        return int(raw, 16)

    def position(tid, lower, upper, block):
        key = keccak256(
            bytes.fromhex(MANAGER[2:])
            + (lower % (1 << 24)).to_bytes(3, "big")
            + (upper % (1 << 24)).to_bytes(3, "big")
            + tid.to_bytes(32, "big")
        )
        slot = int.from_bytes(keccak256(key + ((root + 6) % MOD).to_bytes(32, "big")), "big")
        state = [read(slot + i, block) for i in range(3)]
        if state[0] >= 1 << 128:
            raise ValueError("Invalid V4 liquidity storage")
        return state

    def pending(state, lower, upper, block):
        if not state[0]:
            return [0, 0]
        packed = read(root, block)
        tick = (packed >> 160) & ((1 << 24) - 1)
        tick = tick - (1 << 24) if tick >= 1 << 23 else tick
        lower_slot = int.from_bytes(
            keccak256((lower % MOD).to_bytes(32, "big") + ((root + 4) % MOD).to_bytes(32, "big")),
            "big",
        )
        upper_slot = int.from_bytes(
            keccak256((upper % MOD).to_bytes(32, "big") + ((root + 4) % MOD).to_bytes(32, "big")),
            "big",
        )
        return [
            fee_delta(
                state[0],
                state[1 + i],
                growth_inside(
                    read(root + 1 + i, block),
                    read(lower_slot + 1 + i, block),
                    read(upper_slot + 1 + i, block),
                    tick,
                    lower,
                    upper,
                ),
            )
            for i in range(2)
        ]

    collected = [0, 0]
    opening = [0, 0]
    closing = [0, 0]
    witnesses = []
    principal = [[0, 0], [0, 0]]
    capital_events = []
    for tid in sorted(ids):
        owned = []
        ticks = None
        for block in (start, end):
            raw = source.call("ethereum", MANAGER, "getPoolAndPositionInfo(uint256)", block, tid)
            if raw == "0x" or int(raw, 16) == 0:
                owned.append(False)
                continue
            owner = source.call("ethereum", MANAGER, "ownerOf(uint256)", block, tid)
            if len(raw) != 386 or keccak256(bytes.fromhex(raw[2:322])) != pool_id:
                raise ValueError("V4 NFT does not match configured pool")
            info = int(raw[322:], 16)
            pair = [(info >> shift) & ((1 << 24) - 1) for shift in (8, 32)]
            pair = tuple(t - (1 << 24) if t >= 1 << 23 else t for t in pair)
            if ticks is not None and ticks != pair:
                raise ValueError("V4 position ticks changed")
            ticks = pair
            if owner != "0x" and int(owner, 16) and "0x" + owner[-40:] != holder:
                raise ValueError("Configured V4 position is not owned by Spark")
            owned.append(owner != "0x" and int(owner, 16) != 0)
        own_events = [(r, w) for r, w in events if w[3] == tid]
        if ticks is None and own_events:
            w = own_events[0][1]
            ticks = tuple(
                (x % (1 << 24)) - (1 << 24) if (x % (1 << 24)) >= 1 << 23 else x % (1 << 24)
                for x in w[:2]
            )
        if ticks is None:
            continue
        lower, upper = ticks
        states = [position(tid, lower, upper, b) for b in (start, end)]
        for j, (block, state) in enumerate(zip((start, end), states, strict=True)):
            if state[0] and not owned[j]:
                raise ValueError("Unowned V4 boundary liquidity")
            amounts_principal = get_amounts_for_liquidity(
                read(root, block) & ((1 << 160) - 1),
                get_sqrt_ratio_at_tick(lower),
                get_sqrt_ratio_at_tick(upper),
                state[0],
            )
            for i in range(2):
                principal[j][i] += amounts_principal[i]
            target = opening if j == 0 else closing
            amounts = pending(state, lower, upper, block)
            for i in range(2):
                target[i] += amounts[i]
        for row, w in own_events:
            lo, hi = (x % (1 << 24) for x in w[:2])
            if (lo, hi) != (lower % (1 << 24), upper % (1 << 24)):
                raise ValueError("V4 event ticks mismatch")
            old, new = (
                position(tid, lower, upper, b) for b in (row.block_number - 1, row.block_number)
            )
            delta = w[2] if w[2] < 1 << 255 else w[2] - MOD
            if old[0] + delta != new[0]:
                raise ValueError("V4 liquidity checkpoint mismatch")
            fees = [fee_delta(old[0], old[i + 1], new[i + 1]) for i in range(2)]
            for i in range(2):
                collected[i] += fees[i]
            witnesses.append((tid, row.block_number, row.log_index, *fees))
            capital_events.append(
                {
                    "transaction": row.transaction_hash,
                    "block": row.block_number,
                    "log_index": row.log_index,
                    "pool_id": "0x" + pool_id.hex(),
                    "position_id": tid,
                    "liquidity_delta": str(delta),
                    "tick_lower": lower,
                    "tick_upper": upper,
                    "collected_fee_raw": [str(x) for x in fees],
                }
            )

    def usd(values):
        return sum(
            (
                Decimal(value) / 10 ** DECIMALS[coin]
                for value, coin in zip(values, (c0, c1), strict=True)
            ),
            Decimal(0),
        )

    earned = usd(collected) + usd(closing) - usd(opening)
    return {
        "venue": venue.id,
        "pool_id": "0x" + pool_id.hex(),
        "status": "available",
        "value": str(earned),
        "quality": "supported",
        "recognition": "accrual",
        "basis": "v4_checkpoint_fee_collections_plus_closing_pending_minus_opening_pending",
        "collected_usd": str(usd(collected)),
        "opening_pending_usd": str(usd(opening)),
        "closing_pending_usd": str(usd(closing)),
        "event_digest": digest(witnesses),
        "position_ids": sorted(ids),
        "discovered_position_ids": sorted(discovered),
        "capital_events": capital_events,
        "currencies": [c0, c1],
        "opening_position_usd": str(usd(principal[0])),
        "closing_position_usd": str(usd(principal[1])),
    }


@cached(source_id="spark-reporting.v4-capital-receipt.v1")
def capital_receipt(tx):
    from ..domain.primes import Chain
    from ..extract.rpc import _post, rpc_url

    return _post(rpc_url(Chain.ETHEREUM), "eth_getTransactionReceipt", [tx])


def principal_amounts(sqrt_price, lower, upper, delta):
    """Pool.modifyLiquidity principal deltas, with Solidity's signed rounding."""
    a, b = get_sqrt_ratio_at_tick(lower), get_sqrt_ratio_at_tick(upper)
    price = max(a, min(b, sqrt_price))
    liquidity = abs(delta)

    def divide(numerator, denominator):
        return (numerator + (denominator - 1 if delta > 0 else 0)) // denominator

    amount0 = divide((liquidity * (b - price)) << 96, b * price)
    amount1 = divide(liquidity * (price - a), 1 << 96)
    return [x if delta >= 0 else -x for x in (amount0, amount1)]


def reconcile_capital(source, results):
    """Price modifications at the event's spot, then bridge whole receipts.

    Several configured pools can settle together. Verify their combined cash
    once, after independently allocating principal and fees to each position.
    """

    swap = (
        "0x" + keccak256(b"Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)").hex()
    )
    holder = source.prime.alm[next(c for c in source.prime.alm if str(c) == "ethereum")].hex
    groups, expected, capital = (
        defaultdict(list),
        defaultdict(lambda: defaultdict(int)),
        defaultdict(lambda: defaultdict(int)),
    )
    for venue, result in results.items():
        if result.get("status") != "available":
            continue
        for event in result.get("capital_events", []):
            groups[event["transaction"]].append((venue, event))
    witnesses = []
    pool_currencies = {
        e["pool_id"]: results[venue]["currencies"]
        for events in groups.values()
        for venue, e in events
    }
    pool_currencies.update({r["pool_id"]: r["currencies"] for r in results.values() if r.get("status") == "available" and r.get("pool_id")})
    relevant_blocks = {e["block"] for events in groups.values() for _, e in events}
    swaps_by_block = defaultdict(list)
    if relevant_blocks:
        for row in pool_swaps(
            source.opening_pins["ethereum"],
            source.closing_pins["ethereum"],
            tuple(sorted(pool_currencies)),
        ):
            if row.block_number in relevant_blocks:
                swaps_by_block[row.block_number, row.topic1].append(row)
    for tx, events in sorted(groups.items()):
        receipt = capital_receipt(tx)
        if (
            not receipt
            or receipt.get("transactionHash", "").lower() != tx.lower()
            or int(receipt["status"], 16) != 1
        ):
            raise ValueError("Missing or reverted V4 capital receipt")
        wanted = {(e["block"], e["log_index"]) for _, e in events}
        if len(wanted) != len(events):
            raise ValueError("V4 capital events overlap across venues")
        event_by_coordinate = {(e["block"], e["log_index"]): e for _, e in events}
        actual, cash, seen = set(), defaultdict(int), set()
        for row in receipt["logs"]:
            if (
                row.get("removed")
                or row["transactionHash"].lower() != tx.lower()
                or row["logIndex"] in seen
                or row["blockNumber"] != receipt["blockNumber"]
            ):
                raise ValueError("Invalid V4 receipt log coordinates")
            seen.add(row["logIndex"])
            address, topics = row["address"].lower(), [t.lower() for t in row["topics"]]
            if address == POOL and topics[0] == swap:
                coins = pool_currencies.get(topics[1])
                if not coins:
                    raise ValueError("V4 receipt includes unreviewed swap settlement")
                # Swap emits the caller's signed BalanceDelta. Its negative
                # is cash owed to PoolManager; keep this outside LP capital.
                for i, coin in enumerate(coins):
                    word = int(row["data"][2 + i * 64 : 66 + i * 64], 16)
                    signed = word - MOD if word >= 1 << 255 else word
                    expected[tx][coin] -= signed
            if address == POOL and topics[0] == TOPIC_MODIFY_LIQUIDITY:
                coordinate = int(row["blockNumber"], 16), int(row["logIndex"], 16)
                event = event_by_coordinate.get(coordinate)
                if (
                    event is None
                    or topics[1] != event["pool_id"]
                    or topics[2] != topic(MANAGER)
                    or row["data"].lower()
                    != "0x"
                    + "".join(
                        f"{v % MOD:064x}"
                        for v in (
                            event["tick_lower"],
                            event["tick_upper"],
                            int(event["liquidity_delta"]),
                            event["position_id"],
                        )
                    )
                ):
                    raise ValueError("V4 capital event differs from authenticated receipt")
                actual.add(coordinate)
            if address in DECIMALS and topics[0] == TRANSFER_TOPIC0 and len(topics) == 3:
                if topics[1] == topic(holder) and topics[2] == topic(POOL):
                    cash[address] += int(row["data"], 16)
                elif topics[1] == topic(POOL) and topics[2] == topic(holder):
                    cash[address] -= int(row["data"], 16)
                elif topic(POOL) in topics[1:]:
                    raise ValueError("V4 currencies settled to another account")
        if actual != wanted:
            raise ValueError("V4 receipt contains unreviewed position modifications")
        for venue, event in events:
            block, pool_id = event["block"], bytes.fromhex(event["pool_id"][2:])
            root = int.from_bytes(keccak256(pool_id + (6).to_bytes(32, "big")), "big")

            def price(at, root=root):
                return int(source.call("ethereum", POOL, "extsload(bytes32)", at, root), 16) & (
                    (1 << 160) - 1
                )

            swaps = swaps_by_block[block, event["pool_id"]]
            current = price(block - 1)
            event_price = current
            for row in swaps:
                current = int(row.data[130:194], 16)
                if row.log_index < event["log_index"]:
                    event_price = current
            if not event_price or current != price(block):
                raise ValueError("V4 Swap sequence does not reconcile block prices")
            amounts = principal_amounts(
                event_price, event["tick_lower"], event["tick_upper"], int(event["liquidity_delta"])
            )
            event["sqrt_price_x96"] = str(event_price)
            event["principal_raw"] = [str(x) for x in amounts]
            for coin, amount, fee in zip(
                results[venue]["currencies"], amounts, event["collected_fee_raw"], strict=True
            ):
                expected[tx][coin] += amount - int(fee)
                capital[venue][coin] += amount
        if {c: v for c, v in cash.items() if v} != {c: v for c, v in expected[tx].items() if v}:
            raise ValueError("V4 principal and fees do not reproduce actual settled cash")
        witnesses.append(
            {
                "transaction": tx,
                "net_cash_raw": {c: str(v) for c, v in sorted(cash.items())},
                "event_count": len(events),
            }
        )
    for venue, result in results.items():
        if result.get("status") != "available":
            continue
        net = sum((Decimal(v) / 10 ** DECIMALS[c] for c, v in capital[venue].items()), Decimal(0))
        gross = (
            Decimal(result["closing_position_usd"])
            - Decimal(result["opening_position_usd"])
            - net
            + Decimal(result["value"])
        )
        result["capital_reconciliation"] = {
            "status": "available",
            "net_capital_added_usd": str(net),
            "opening_position_usd": result["opening_position_usd"],
            "closing_position_usd": result["closing_position_usd"],
            "gross_return_usd": str(gross),
            "receipt_count": len(witnesses),
            "receipt_digest": digest(witnesses),
            "basis": "LP_principal_change_minus_event_spot_principal_plus_accrued_fees_with_combined_receipt_bridge",
        }
    return results


@cache
def ownership_history(holder, closing_pin):
    from ..extract.hypersync import _DEFAULT_LOG_FIELDS
    from ..extract.hypersync_store import fetch_logs
    from .spark_reconciliation import unique

    return unique(
        fetch_logs(
            "ethereum",
            [
                {"address": [MANAGER], "topics": [[TRANSFER_TOPIC0], [topic(holder)]]},
                {"address": [MANAGER], "topics": [[TRANSFER_TOPIC0], [], [topic(holder)]]},
            ],
            0,
            closing_pin,
            log_fields=[*_DEFAULT_LOG_FIELDS, "transaction_hash"],
            max_num_logs=100000,
        )
    )


@cached(source_id="spark-reporting.v4-monthly-swaps.v1")
def pool_swaps(opening_pin, closing_pin, pool_ids):
    from ..extract.hypersync import _DEFAULT_LOG_FIELDS
    from ..extract.hypersync_store import fetch_logs
    from .spark_reconciliation import unique

    swap = (
        "0x" + keccak256(b"Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)").hex()
    )
    return unique(
        fetch_logs(
            "ethereum",
            [{"address": [POOL], "topics": [[swap], list(pool_ids)]}],
            opening_pin + 1,
            closing_pin,
            log_fields=[*_DEFAULT_LOG_FIELDS, "transaction_hash"],
            max_num_logs=100000,
        )
    )
