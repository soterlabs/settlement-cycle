#!/usr/bin/env python3
"""Independently check pinned daily USDC exposure against normalized transfers.

Streams saved inputs; no provider calls and no capital replay. Requires the
original normalized history and selected-account snapshot named in evidence.
"""

import argparse
import gzip
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

from settle.revenue.spark_idle_usdc_repair import EVIDENCE, EXPOSURE_SHA256, ROOT


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def check(history, snapshot):
    raw = (ROOT / EVIDENCE).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == EXPOSURE_SHA256
    evidence = json.loads(raw)
    for path in (history, snapshot):
        assert sha256(path) == evidence["inputs"][path.name], "Input hash differs"
    with gzip.open(snapshot, "rt") as stream:
        previous = json.load(stream)
    accounts = {v: row["account"] for v, row in evidence["allocations"].items()}
    keys = set(accounts.values())
    updates = {}
    with gzip.open(history, "rt") as stream:
        metadata = json.loads(next(stream))
        assert all(metadata["venue_accounts"][v] == a for v, a in accounts.items())
        # Input batches can be appended by adapters. Use replay ordering, not file order.
        latest = {}
        for line in stream:
            batch = json.loads(line)
            order = (batch["timestamp"], batch["chain"], batch["block"], batch["log_index"])
            if batch["day"] > "2026-08-31":
                continue
            for movement in batch["movements"]:
                if movement["account"] not in keys:
                    continue
                if batch["day"] >= "2026-08-01":
                    assert D(movement["external_income"]) == 0, "Unexpected idle cash income"
                key = (batch["day"], movement["account"])
                if key not in latest or order >= latest[key]:
                    latest[key] = order
                    updates.setdefault(batch["day"], {})[movement["account"]] = D(
                        movement["value_before"]
                    ) + D(movement["change"])
    balances = dict.fromkeys(keys, D(0))
    checked = 0
    for day in sorted(set(updates) | set(previous["daily_funding"])):
        balances.update(updates.get(day, {}))
        if not "2026-08-01" <= day <= "2026-08-31":
            continue
        for venue, account in accounts.items():
            expected = D(evidence["allocations"][venue]["daily"][day])
            assert balances[account] == expected
            assert D(previous["daily_funding"][day][account]["observed_value"]) == expected
            checked += 1
    assert checked == 93, "Incomplete daily comparison"
    return {"matched_daily_balances": checked, "provider_calls": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.history, args.snapshot)))
