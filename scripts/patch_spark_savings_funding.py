#!/usr/bin/env python3
"""Attach independently audited Savings funding to a frozen diagnostic history.

Never mutates the original snapshot, published reports, debt, or asset marks.
Run audit_spark_savings_principal.py first. Policy remains explicitly provisional.
"""

import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from settle.compute.executed_spell_capital import apply_executed_spells
from settle.compute.spark_separate_savings_routes import SAVER, TX
from settle.normalize.allocation_capital import ExternalFundingOperation
from settle.normalize.allocation_history_cache import load_history, save_history

POLICY = "Provisional Savings policy: repay principal and accrued VSR proportionally; refinance remaining source-funded holdings proportionally."
ROUTES = "Provisional Savings routing: transaction clearing pools token legs except independently proven separated routes."


def attach(history, audit):
    if not audit.get("policy", "").startswith("PROVISIONAL:"):
        raise ValueError("Savings principal audit does not declare its policy")
    history = apply_executed_spells(history)
    grouped = defaultdict(list)
    for vault in audit["vaults"]:
        for op in vault["operations"]:
            if op["kind"] not in ("draw", "repay", "interest"):
                raise ValueError("Prepaid Savings funding needs a separate reviewed ledger adapter")
            chain = op["source"].split(":")[0]
            identity = chain + ":" + op["transaction_hash"]
            if identity == TX:
                identity = SAVER
            grouped[identity].append(op)
    missing = set(grouped) - {b.identity for b in history.batches}
    if missing:
        raise ValueError(
            f"Savings cash has no normalized transaction: {len(missing)}, sample {sorted(missing)[:3]}"
        )
    result = []
    for batch in history.batches:
        rows = grouped.get(batch.identity, [])
        if not rows:
            result.append(batch)
            continue
        if batch.external_funding:
            raise ValueError("Savings operations already attached")
        if any((r["block"], r["timestamp"]) != (batch.block, batch.timestamp) for r in rows):
            raise ValueError("Savings operation disagrees with transaction metadata")
        ops = tuple(
            ExternalFundingOperation(r["kind"], r["source"], D(r["amount"]))
            for r in sorted(rows, key=lambda r: (r["log_index"], r["kind"]))
        )
        result.append(
            replace(batch, external_funding=ops, funding_assumption=POLICY + " " + ROUTES)
        )
    return replace(history, batches=tuple(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("history", "audit", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in (args.history.resolve(), args.audit.resolve()):
        raise ValueError("Diagnostic patch cannot overwrite input evidence")
    with gzip.open(args.history, "rt") as source:
        metadata = json.loads(next(source))
    history = load_history(args.history, metadata["fingerprint"])
    if history is None:
        raise ValueError("Unsupported history snapshot")
    audit = json.loads(args.audit.read_text())
    for name, digest in audit["input_hashes"].items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
            raise ValueError("Savings audit source evidence changed")
    patched = attach(history, audit)
    # Adapters can split transactions, but not create or extinguish Sky debt.
    with localcontext() as ctx:
        ctx.prec = 100

        def debt_by_day(h):
            result = defaultdict(D)
            for b in h.batches:
                for ilk, value in b.minted_by_ilk.items():
                    result[b.day, ilk] += value
            return result

        if debt_by_day(history) != debt_by_day(patched):
            raise ValueError("Savings patch changed Sky debt")
    save_history(args.output, patched, metadata["fingerprint"])
    patch = {
        "policy": POLICY,
        "routing": ROUTES,
        "batches_with_savings": sum(bool(b.external_funding) for b in patched.batches),
        "input_hashes": {
            str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.history, args.audit)
        },
        "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "original_metadata": metadata,
    }
    args.output.with_suffix(".audit.json").write_text(json.dumps(patch, indent=2) + "\n")
    print(json.dumps({k: v for k, v in patch.items() if k != "original_metadata"}, indent=2))


if __name__ == "__main__":
    main()
