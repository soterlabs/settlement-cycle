"""Authenticate Spark stablecoin swaps using events and actual ALM cash.

Only configured par-stable pairs are considered. Liquidity modifications,
unknown pools and transactions whose actual cash differs are left unclassified.
The signed V4 amounts are the caller's delta (opposite to V3's convention).
No price inference or percentage fee assumption is used.
"""

from collections import defaultdict
from decimal import Decimal as D

from ..extract._keccak import keccak256
from ..extract.aave_reconstruct import _words
from ..extract.transfer_logs import TRANSFER_TOPIC0

HOLDER = "0x1601843c5e9bc251a3272907010afa41fa18347e"
PM = "0x000000000004444c5dc75cb358380d2e3de08a90"
CURVE = "0xa632d59b9b804a956bfaa9b48af3a1b74808fc1f"
DECIMALS = {
    "0xdc035d45d973e3ec169d2276ddab16f1e407384f": 18,
    "0x6c3ea9036406852006290770bedfcaba0e23a0e8": 6,
    "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,
    "0x8292bb45bf1ee4d140127049757c2e0ff06317ed": 18,
}
SWAP = "0x" + keccak256(b"Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)").hex()
CURVESWAP = "0x" + keccak256(b"TokenExchange(address,int128,uint256,int128,uint256)").hex()
MODIFY = "0x" + keccak256(b"ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)").hex()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def prove_swaps(raw, metadata):
    # Authenticate pool keys instead of trusting a supplied poolId/token map.
    from ..domain.primes import Address
    from ..extract.uniswap_v4 import V4PoolKey

    require(metadata["pin"] == raw["pin"], "Swap metadata pin mismatch")
    require(
        metadata["curve_coins"]
        == [
            "0x6c3ea9036406852006290770bedfcaba0e23a0e8",
            "0xdc035d45d973e3ec169d2276ddab16f1e407384f",
        ],
        "Unexpected Curve coins",
    )
    for pool_id, value in metadata["v4_pools"].items():
        require(
            all(value[k] in DECIMALS for k in ("currency0", "currency1")), "Non-par V4 currency"
        )
        key = V4PoolKey(
            Address.from_str(value["currency0"]),
            Address.from_str(value["currency1"]),
            value["fee"],
            value["tick_spacing"],
            Address.from_str(value["hooks"]),
        )
        require("0x" + key.pool_id().hex() == pool_id, "V4 pool key mismatch")
    groups = defaultdict(list)
    unique = {}
    for row in raw["rows"]:
        key = (row["block_number"], row["log_index"])
        require(key not in unique or unique[key] == row, "Conflicting swap event identity")
        unique[key] = row
    for row in unique.values():
        groups[row["transaction_hash"]].append(row)
    verified = []
    excluded = []
    for tx, rows in groups.items():
        require(
            len({(r["block_number"], r["block_time"]) for r in rows}) == 1,
            "Conflicting swap transaction metadata",
        )
        expected = defaultdict(int)
        actual = defaultdict(int)
        kinds = set()
        reason = None
        events = []
        for row in rows:
            if row["address"] == PM and row["topic0"] == MODIFY:
                reason = "liquidity modification"
                break
            if row["address"] == PM and row["topic0"] == SWAP:
                pool = metadata["v4_pools"].get(row["topic1"])
                if pool is None:
                    reason = "unreviewed V4 pool"
                    break
                words = _words(row["data"])
                require(len(words) == 6, "Invalid V4 Swap layout")
                delta = [n - 2**256 if n >= 2**255 else n for n in words[:2]]
                require(delta[0] * delta[1] < 0, "Invalid V4 swap directions")
                for i in (0, 1):
                    expected[(PM, pool["currency" + str(i)])] += delta[i]
                kinds.add("uniswap-v4")
                events.append(row["log_index"])
            elif row["address"] == CURVE and row["topic0"] == CURVESWAP:
                if "0x" + row["topic1"][-40:] != HOLDER:
                    reason = "other Curve trader"
                    break
                words = _words(row["data"])
                require(len(words) == 4, "Invalid Curve swap layout")
                i, sold, j, bought = words
                require(
                    i in (0, 1) and j == 1 - i and min(sold, bought) > 0,
                    "Invalid Curve swap amounts",
                )
                expected[(CURVE, metadata["curve_coins"][i])] -= sold
                expected[(CURVE, metadata["curve_coins"][j])] += bought
                kinds.add("curve")
                events.append(row["log_index"])
        if reason or not events:
            excluded.append({"identity": "ethereum:" + tx, "reason": reason or "no selected swap"})
            continue
        for row in rows:
            if row["topic0"] != TRANSFER_TOPIC0 or row["address"] not in DECIMALS:
                continue
            require(len(row["data"]) == 66, "Invalid swap token transfer")
            a, b = "0x" + row["topic1"][-40:], "0x" + row["topic2"][-40:]
            amount = int(row["data"], 16)
            if a == HOLDER and b in (CURVE, PM):
                actual[(b, row["address"])] -= amount
            if b == HOLDER and a in (CURVE, PM):
                actual[(a, row["address"])] += amount
        expected = {k: v for k, v in expected.items() if v}
        actual = {k: v for k, v in actual.items() if v}
        if actual != expected:
            excluded.append(
                {
                    "identity": "ethereum:" + tx,
                    "reason": "cash mismatch",
                    "expected": str(expected),
                    "actual": str(actual),
                }
            )
            continue
        net = defaultdict(int)
        for (_, token), amount in expected.items():
            net[token] += amount
        gain = sum(D(amount) / 10 ** DECIMALS[token] for token, amount in net.items())
        verified.append(
            {
                "identity": "ethereum:" + tx,
                "block": rows[0]["block_number"],
                "timestamp": rows[0]["block_time"],
                "gain": str(gain),
                "protocols": sorted(kinds),
                "events": events,
                "net": {k: str(D(v) / 10 ** DECIMALS[k]) for k, v in net.items()},
            }
        )
    return verified, excluded
