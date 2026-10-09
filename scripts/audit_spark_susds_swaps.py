#!/usr/bin/env python3
"""Read-only Curve execution audit using independently reconstructed sUSDS NAV.

SUsds.sol, sky-ecosystem/sdai at dfc7f41cb7599afcb0f0eb1ddaadbf9dd4015dce:
file requires an up-to-date chi; drip and convertToAssets use ray exponentiation.
No accounting classification or funding amount is changed by this audit.
"""

import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path

from audit_spark_savings_principal import rpow

from settle.extract._keccak import keccak256

RAY = 10**27
HOLDER = "0x1601843c5e9bc251a3272907010afa41fa18347e"
SUSDS = "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
POOL = "0x00836fe54625be242bcfa286207795405ca4fd10"


def topic(signature):
    return "0x" + keccak256(signature.encode()).hex()


DRIP = topic("Drip(uint256,uint256)")
FILE = topic("File(bytes32,uint256)")
SWAP = topic("TokenExchange(address,int128,uint256,int128,uint256)")
TRANSFER = topic("Transfer(address,address,uint256)")
REMOVE_LIQUIDITY = topic("RemoveLiquidity(address,uint256[],uint256[],uint256)")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def words(data):
    require(data.startswith("0x") and (len(data) - 2) % 64 == 0, "Invalid event data")
    return [int(data[i:i + 64], 16) for i in range(2, len(data), 64)]


def rate_prices(proof, blocks):
    """Check every historical Drip, both pinned states, and end-of-block quotes."""
    meta = proof["metadata"]
    require(meta["susds"] == SUSDS and meta["curve"] == POOL, "Wrong audited contracts")
    require(set(meta["curve_coins"]) == {SUSDS, USDT}, "Wrong Curve currencies")
    initial = meta["states"][str(meta["initial_block"])]
    chi, rho, ssr = (int(initial[k]) for k in ("chi()", "rho()", "ssr()"))

    def quote(stamp):
        require(stamp >= rho, "sUSDS timestamp precedes stored accrual")
        return 10**18 * (rpow(ssr, stamp - rho) * chi // RAY) // RAY

    require(quote(initial["timestamp"]) == int(initial["convertToAssets(1e18)"]),
            "Opening sUSDS quote mismatch")
    unique = {}
    for values in proof["rate_rows"]:
        require(len(values) == len(proof["rate_fields"]), "Incomplete rate event")
        row = dict(zip(proof["rate_fields"], values, strict=True))
        block = row["block_number"]
        require(meta["initial_block"] < block <= meta["pin"], "Rate event outside pinned interval")
        key = (block, row["log_index"])
        require(key not in unique or unique[key] == row, "Conflicting rate event")
        unique[key] = row
    items = [(b, i, r) for (b, i), r in unique.items()]
    for b, stamp in blocks.items():
        require(meta["initial_block"] < b <= meta["pin"], "Swap outside pinned interval")
        items.append((b, 10**9, stamp))
    prices = {}
    drips = files = 0
    for block, index, row in sorted(items):
        if index == 10**9:
            prices[block] = quote(row)
            continue
        data, stamp = words(row["data"]), row["block_time"]
        require(stamp >= rho, "Unordered sUSDS accrual")
        if row["topic0"] == DRIP:
            require(len(data) == 2, "Invalid Drip layout")
            expected = rpow(ssr, stamp - rho) * chi // RAY
            require(expected == data[0], "Drip differs from reconstructed rate")
            chi, rho = expected, stamp
            drips += 1
        elif row["topic0"] == FILE:
            require(bytes.fromhex(row["topic1"][2:]).rstrip(b"\0") == b"ssr",
                    "Unknown sUSDS rate parameter")
            require(rho == stamp and len(data) == 1 and data[0] >= RAY,
                    "Invalid sUSDS rate change")
            ssr = data[0]
            files += 1
        else:
            raise ValueError("Unknown sUSDS rate event")
    final = meta["states"][str(meta["pin"])]
    require((chi, rho, ssr) == tuple(int(final[k]) for k in ("chi()", "rho()", "ssr()")),
            "Closing sUSDS state mismatch")
    require(quote(final["timestamp"]) == int(final["convertToAssets(1e18)"]),
            "Closing sUSDS quote mismatch")
    return prices, {"drips_verified": drips, "rate_changes_verified": files}


def proportional_withdrawal_cash(rows):
    """Authenticate the two-coin cash leg of a proportional LP withdrawal.

    CurveStableSwapNG emits an LP burn followed by RemoveLiquidity; the event
    contains returned coin quantities, an empty fees array, and token supply.
    This separates returned LP capital from swaps sharing the transaction.
    https://github.com/curvefi/stableswap-ng/blob/3332cd44656ec64b3c048885e1dd71955254b262/contracts/main/CurveStableSwapNG.vy
    Other liquidity operations remain outside this narrowly checked shape.
    """
    indexed = {r["log_index"]: r for r in rows}
    cash = defaultdict(int)
    indexes = []
    for row in rows:
        if (row["address"] != POOL or row["topic0"] != REMOVE_LIQUIDITY
                or "0x" + row["topic1"][-40:] != HOLDER):
            continue
        value = words(row["data"])
        require(len(value) == 7 and value[0:2] == [96, 192]
                and value[3] == 2 and value[6] == 0,
                "Unsupported Curve proportional withdrawal layout")
        burn = indexed.get(row["log_index"] - 1)
        require(burn is not None and burn["address"] == POOL
                and burn["topic0"] == TRANSFER
                and "0x" + burn["topic1"][-40:] == HOLDER
                and int(burn["topic2"], 16) == 0
                and burn["transaction_hash"] == row["transaction_hash"]
                and len(burn["data"]) == 66 and int(burn["data"], 16) > 0,
                "Curve proportional withdrawal lacks matching LP burn")
        cash[SUSDS] += value[4]
        cash[USDT] += value[5]
        indexes.append(row["log_index"])
    return cash, indexes


def audit(proof, residuals=None):
    groups, unique, blocks = defaultdict(list), {}, {}
    for row in proof["swap_rows"]:
        key = (row["block_number"], row["log_index"])
        require(key not in unique or unique[key] == row, "Conflicting swap evidence")
        unique[key] = row
    for row in unique.values():
        block, stamp = row["block_number"], row["block_time"]
        require(block not in blocks or blocks[block] == stamp, "Conflicting block timestamp")
        blocks[block] = stamp
        groups[row["transaction_hash"]].append(row)
    prices, summary = rate_prices(proof, blocks)
    outflows = {r["identity"]: D(r["amount"]) for r in (residuals or {}).get("outflows", [])}
    receipts = {r["identity"]: D(r["amount"]) for r in (residuals or {}).get("receipts", [])}
    verified, excluded = [], []
    for tx, rows in groups.items():
        require(len({r["block_number"] for r in rows}) == 1, "Conflicting transaction block")
        expected, actual, logs = defaultdict(int), defaultdict(int), []
        for row in rows:
            if row["address"] == POOL and row["topic0"] == SWAP:
                require("0x" + row["topic1"][-40:] == HOLDER, "Unexpected Curve trader")
                data = words(row["data"])
                require(len(data) == 4, "Invalid Curve swap layout")
                i, sold, j, bought = data
                require(i in (0, 1) and j == 1 - i and min(sold, bought) > 0,
                        "Invalid Curve swap amounts")
                coins = proof["metadata"]["curve_coins"]
                expected[coins[i]] -= sold
                expected[coins[j]] += bought
                logs.append(row["log_index"])
            if row["topic0"] == TRANSFER and row["address"] in (SUSDS, USDT):
                require(len(row["data"]) == 66, "Invalid swap transfer")
                a, b = "0x" + row["topic1"][-40:], "0x" + row["topic2"][-40:]
                n = int(row["data"], 16)
                if a == HOLDER and b == POOL:
                    actual[row["address"]] -= n
                if b == HOLDER and a == POOL:
                    actual[row["address"]] += n
        withdrawal_cash, withdrawal_logs = proportional_withdrawal_cash(rows)
        combined = {token: expected[token] + withdrawal_cash[token] for token in (SUSDS, USDT)}
        if not logs or {k: v for k, v in actual.items() if v} != {k: v for k, v in combined.items() if v}:
            excluded.append({"identity": "ethereum:" + tx, "reason": "absent swap or cash mismatch"})
            continue
        block = rows[0]["block_number"]
        gain = D(expected[SUSDS]) * prices[block] / D(10**36) + D(expected[USDT]) / 10**6
        identity = "ethereum:" + tx
        row = {"identity": identity, "block": block, "timestamp": rows[0]["block_time"],
               "gain": str(gain), "quote": str(prices[block]), "swap_logs": sorted(logs)}
        if withdrawal_logs:
            row["proportional_withdrawal_logs"] = withdrawal_logs
            row["lp_cash_excluded_from_swap_gain"] = {k: str(v) for k, v in withdrawal_cash.items()}
        if residuals is not None:
            row["matches_outflow"] = identity in outflows and abs(outflows[identity] + gain) < D("1e-8")
            row["matches_receipt"] = identity in receipts and abs(receipts[identity] - gain) < D("1e-8")
        verified.append(row)
    summary.update({"scope": "Historical execution value differences, not monthly borrowing costs or certified funding.",
                    "swap_transactions": len(verified), "excluded": excluded, "rows": verified})
    if residuals is not None:
        for name, sign in (("outflow", -1), ("receipt", 1)):
            matches = [r for r in verified if r["matches_" + name]]
            summary[name + "_matches"] = len(matches)
            summary["matched_" + name + "_value"] = str(sum((sign * D(r["gain"]) for r in matches), D(0)))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--residuals", type=Path, help="Optional independently saved transaction residuals")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with gzip.open(args.evidence, "rt") as source:
        proof = json.load(source)
    result = audit(proof, json.loads(args.residuals.read_text()) if args.residuals else None)
    result["input_hashes"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (args.evidence, args.residuals) if p is not None}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("rows", "excluded")}, indent=2))


if __name__ == "__main__":
    main()
