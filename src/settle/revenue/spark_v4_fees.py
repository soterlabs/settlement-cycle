"""V4 earned fees from authenticated growth checkpoints and pending claims.

Hookless, configured Spark NFTs only. No reward transfers or principal residuals
are used as fees. Unsupported ownership or same-block ambiguity fails closed.
"""

from collections import Counter
from decimal import Decimal

from ..extract._keccak import keccak256
from ..extract.transfer_logs import TRANSFER_TOPIC0
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
    for tid in sorted(ids):
        owned = []
        ticks = None
        for block in (start, end):
            raw = source.call("ethereum", MANAGER, "getPoolAndPositionInfo(uint256)", block, tid)
            owner = source.call("ethereum", MANAGER, "ownerOf(uint256)", block, tid)
            if raw == "0x" or int(raw, 16) == 0:
                owned.append(False)
                continue
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
    }
