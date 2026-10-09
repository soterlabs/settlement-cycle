#!/usr/bin/env python3
"""Identify reserve earnings carried through SubProxy, excluding tiny seed mints.

The March 12, 2026 spell transfers the complete spDAI/spUSDS balances to ALM:
https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20260312/SparkEthereum_20260312.sol#L111
Its March 14 execution also sweeps current treasury earnings directly. This
history isolates the additional SubProxy legs, without counting those twice.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from settle.extract import aave_reconstruct as aave

HOLDER = "0x3300f198988e4c9c63f75df86de36421f06af8c4"
ALM = "0x1601843c5e9bc251a3272907010afa41fa18347e"
TREASURIES = {
    "0x856900aa78e856a5df1a2665ee3a66b2487cd68f",
    "0xb137e7d16564c81ae2b0c8ee6b55de81dd46ece5",
}
TX = "0xd157dbc535da15f78cfb94eacbfbfe20c0b728f9f561350484919dfe499d239d"


def audit(evidence):
    if evidence["subproxy"] != HOLDER:
        raise ValueError("Wrong SubProxy in reserve evidence")
    scaled, earned = defaultdict(int), defaultdict(int)
    seed = defaultdict(int)
    result = []
    seen = set()
    with localcontext() as ctx:
        ctx.prec = 60
        for r in sorted(evidence["rows"], key=lambda r: (r["block_number"], r["log_index"])):
            key = (r["block_number"], r["log_index"])
            if key in seen or r["block_number"] > evidence["pin"]:
                raise ValueError("Duplicate or out-of-pin SubProxy event")
            seen.add(key)
            token = r["address"]
            sender, recipient = "0x" + r["topic1"][-40:], "0x" + r["topic2"][-40:]
            words = aave._words(r["data"])
            if r["topic0"] == aave.MINT_T0:
                if recipient != HOLDER:
                    continue
                value, increase, index = words
                if value < increase:
                    raise ValueError("Unexpected SubProxy scaled burn via Mint")
                principal = aave.ray_div(value - increase, index)
                scaled[token] += principal
                seed[token] += principal
            elif r["topic0"] == aave.BT_T0:
                amount, index = words
                if recipient == HOLDER:
                    if sender not in TREASURIES:
                        raise ValueError("Unidentified source of SubProxy reserve balance")
                    scaled[token] += amount
                    earned[token] += amount
                elif sender == HOLDER:
                    if r["transaction_hash"] != TX or recipient != ALM or amount != scaled[token]:
                        raise ValueError(
                            "SubProxy execution does not exhaust reconstructed holdings"
                        )
                    result.append(
                        {
                            "token": token,
                            "transaction_hash": TX,
                            "block": r["block_number"],
                            "timestamp": r["block_time"],
                            "log_index": r["log_index"],
                            "earned_scaled": str(earned[token]),
                            "seed_scaled": str(seed[token]),
                            "liquidity_index": str(index),
                            "earned_value": str(D(earned[token]) * index / 10**45),
                            "unclassified_seed_value": str(D(seed[token]) * index / 10**45),
                            "total_transfer_value": str(D(amount) * index / 10**45),
                        }
                    )
                    scaled[token] -= amount
                else:
                    raise ValueError("Unrelated transfer in SubProxy evidence")
            else:
                raise ValueError("Unexpected SubProxy event type")
        if dict(scaled) != evidence["closing_scaled"] or len(result) != 2:
            raise ValueError("SubProxy history differs from pinned closing balance")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    path = Path("tests/fixtures/spark_subproxy_reserve_history.json")
    rows = audit(json.loads(path.read_text()))
    result = {
        "scope": "Reserve-factor earnings only; seed mints remain unclassified, no published revenue restatement.",
        "legs": rows,
        "earned_value": str(sum((D(r["earned_value"]) for r in rows), D(0))),
        "unclassified_seed_value": str(sum((D(r["unclassified_seed_value"]) for r in rows), D(0))),
        "input_hashes": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()},
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
