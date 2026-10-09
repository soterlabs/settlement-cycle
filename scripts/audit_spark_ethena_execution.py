#!/usr/bin/env python3
"""Prove Spark's Ethena mint/redeem shortfalls from events and actual cash.

These are paid-versus-received differences at the diagnostic's par valuation.
They are not unidentified transfers or additional invested principal. This
read-only audit neither changes revenue nor assumes the loss was Sky-funded.
"""

import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path

from settle.extract._keccak import keccak256
from settle.extract.aave_reconstruct import _words
from settle.extract.transfer_logs import TRANSFER_TOPIC0

ALM = "0x1601843c5e9bc251a3272907010afa41fa18347e"
USDE = "0x4c9edd5852cd905f086c759e8383e09bff1e68b3"
COINS = {
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": 6,
    "0xdac17f958d2ee523a2206206994597c13d831ec7": 6,
}
EVENTS = {}
for contract, types, offset in (
    (
        "0x2cc440b721d2cafd6d64908d6d8c4acc57f8afc3",
        "address,address,address,address,uint256,uint256",
        0,
    ),
    (
        "0xe3490297a08d6fc8da46edb7b6142e4f461b62d3",
        "string,address,address,address,address,uint256,uint256",
        1,
    ),
):
    for kind in ("Mint", "Redeem"):
        EVENTS[(contract, "0x" + keccak256((kind + "(" + types + ")").encode()).hex())] = (
            kind,
            offset,
        )


def audit(rows):
    unique = {}
    for r in rows:
        key = r["block_number"], r["log_index"]
        if key in unique and unique[key] != r:
            raise ValueError("Conflicting Ethena event identity")
        unique[key] = r
    grouped = defaultdict(list)
    for r in unique.values():
        grouped[r["transaction_hash"]].append(r)
    result = []
    for tx, logs in grouped.items():
        expected = defaultdict(int)
        minted = 0
        events = []
        kinds = set()
        for r in logs:
            layout = EVENTS.get((r["address"], r["topic0"]))
            if layout is None:
                continue
            kind, offset = layout
            kinds.add(kind)
            if any("0x" + r[k][-40:] != ALM for k in ("topic2", "topic3")):
                raise ValueError("Ethena execution belongs to a different holder")
            words = _words(r["data"])
            if len(words) != offset + 3:
                raise ValueError("Unexpected Ethena execution layout")
            token = "0x" + words[offset].to_bytes(32)[-20:].hex()
            if token not in COINS:
                raise ValueError("Unreviewed Ethena collateral asset")
            paid, made = words[offset + 1 :]
            expected[token] += paid
            minted += made
            events.append(r["log_index"])
        if not events:
            continue
        if len(kinds) != 1:
            raise ValueError("Mixed Ethena mint/redeem needs separate routing proof")
        kind = next(iter(kinds))
        actual = defaultdict(int)
        delivered = 0
        for r in logs:
            if r["topic0"] != TRANSFER_TOPIC0:
                continue
            if len(r["data"]) != 66:
                raise ValueError("Invalid Ethena cash transfer")
            sender, recipient = "0x" + r["topic1"][-40:], "0x" + r["topic2"][-40:]
            raw = int(r["data"], 16)
            if (sender if kind == "Mint" else recipient) == ALM and r["address"] in expected:
                actual[r["address"]] += raw
            issuance = (
                (int(r["topic1"], 16) == 0 and recipient == ALM)
                if kind == "Mint"
                else (sender == ALM and int(r["topic2"], 16) == 0)
            )
            if r["address"] == USDE and issuance:
                delivered += raw
        if dict(actual) != dict(expected) or delivered != minted:
            raise ValueError("Ethena event amounts disagree with actual ALM cash")
        paid = sum(D(v) / 10 ** COINS[k] for k, v in expected.items())
        received = D(minted) / 10**18
        result.append(
            {
                "identity": "ethereum:" + tx,
                "block": logs[0]["block_number"],
                "event_indexes": events,
                "kind": kind,
                "collateral_amount": str(paid),
                "usde_amount": str(received),
                "par_value_shortfall": str(paid - received if kind == "Mint" else received - paid),
            }
        )
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("events", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    p.add_argument(
        "--financing", type=Path, help="Optional completed replay for exact residual matching"
    )
    args = p.parse_args()
    if args.events.resolve() == args.output.resolve() or (
        args.financing and args.financing.resolve() == args.output.resolve()
    ):
        raise ValueError("Inputs and output must be distinct")
    records = audit(json.loads(gzip.decompress(args.events.read_bytes())))
    result = {
        "scope": __doc__,
        "transactions": len(records),
        "total_par_value_shortfall": str(sum(D(r["par_value_shortfall"]) for r in records)),
        "by_kind": {
            k: {
                "transactions": sum(r["kind"] == k for r in records),
                "shortfall": str(
                    sum(D(r["par_value_shortfall"]) for r in records if r["kind"] == k)
                ),
            }
            for k in ("Mint", "Redeem")
        },
        "records": records,
    }
    paths = [args.events, Path(__file__)]
    if args.financing:
        paths.append(args.financing)
        raw = args.financing.read_bytes()
        finance = json.loads(gzip.decompress(raw) if args.financing.suffix == ".gz" else raw)
        residuals = finance["unmatched_outflows"]
        matched = []
        for r in records:
            candidates = [
                k
                for k in residuals
                if k.split(":")[1] == r["identity"].split(":")[1]
                and abs(D(residuals[k]) - D(r["par_value_shortfall"])) < D("1e-8")
            ]
            if len(candidates) > 1:
                raise ValueError("Ambiguous Ethena residual match")
            if candidates:
                matched.append(
                    {"identity": candidates[0], "par_value_shortfall": r["par_value_shortfall"]}
                )
        result["matched_replay_residuals"] = matched
        result["matched_replay_shortfall"] = str(sum(D(r["par_value_shortfall"]) for r in matched))
    result["input_hashes"] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("records", "matched_replay_residuals", "scope", "input_hashes")
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
