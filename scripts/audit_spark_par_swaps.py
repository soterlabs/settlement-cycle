#!/usr/bin/env python3
"""Audit proven stablecoin swaps and optionally emit historical tracing rules."""

import argparse
import gzip
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal as D
from pathlib import Path

from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.spark_par_swap_evidence import DECIMALS, HOLDER, prove_swaps


def audited_rules(evidence):
    verified, excluded = prove_swaps(evidence["raw"], evidence["metadata"])
    # Complete ALM token deltas can differ from the DEX leg alone, e.g. when
    # the swap proceeds immediately pay a saver. Authenticate that net delta.
    net = defaultdict(int)
    seen = set()
    for row in evidence["raw"]["rows"]:
        key = row["block_number"], row["log_index"]
        if key in seen:
            continue
        seen.add(key)
        if row["topic0"] != TRANSFER_TOPIC0 or row["address"] not in DECIMALS:
            continue
        amount = int(row["data"], 16)
        source, target = "0x" + row["topic1"][-40:], "0x" + row["topic2"][-40:]
        net[(row["transaction_hash"], row["address"])] += amount * (
            (target == HOLDER) - (source == HOLDER)
        )
    rules = []
    for row in verified:
        if D(row["gain"]) <= D(".01"):
            continue
        targets = [token for token, amount in row["net"].items() if D(amount) > 0]
        if len(targets) != 1:
            raise ValueError("A swap gain needs an unambiguous receiving token")
        token = targets[0]
        rules.append(
            {
                "identity": row["identity"],
                "block": row["block"],
                "timestamp": row["timestamp"],
                "account": "ethereum:" + HOLDER + ":" + token,
                "change": str(
                    D(net[(row["identity"].split(":")[1], token)]) / 10 ** DECIMALS[token]
                ),
                "earned": row["gain"],
            }
        )
    return verified, excluded, rules


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evidence", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--write-rules", type=Path)
    args = p.parse_args()
    paths = [args.evidence, args.output] + ([args.write_rules] if args.write_rules else [])
    if len({p.resolve() for p in paths}) != len(paths):
        raise ValueError("Inputs and outputs must be distinct")
    evidence = json.loads(gzip.decompress(args.evidence.read_bytes()))
    verified, excluded, rules = audited_rules(evidence)
    digest = hashlib.sha256(args.evidence.read_bytes()).hexdigest()
    summary = {
        "scope": "Historical par-stable swap outcomes, not monthly revenue or borrowing costs.",
        "pin": evidence["raw"]["pin"],
        "verified": len(verified),
        "earned_transactions": len(rules),
        "earned_total": str(sum(D(r["earned"]) for r in rules)),
        "shortfall_transactions": sum(D(r["gain"]) < D("-.01") for r in verified),
        "shortfall_total": str(-sum(D(r["gain"]) for r in verified if D(r["gain"]) < D("-.01"))),
        "excluded_reasons": dict(Counter(r["reason"] for r in excluded)),
        "input_hashes": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [
                args.evidence,
                Path(__file__),
                Path("src/settle/normalize/spark_par_swap_evidence.py"),
            ]
        },
    }
    if args.write_rules:
        args.write_rules.parent.mkdir(parents=True, exist_ok=True)
        args.write_rules.write_text(
            json.dumps(
                {
                    "scope": "Tracing only. Proven swap earnings; no settlement recognition change.",
                    "evidence_sha256": digest,
                    "rows": rules,
                },
                indent=2,
            )
            + "\n"
        )
        summary["rules_sha256"] = hashlib.sha256(args.write_rules.read_bytes()).hexdigest()
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "input_hashes"}, indent=2))


if __name__ == "__main__":
    main()
