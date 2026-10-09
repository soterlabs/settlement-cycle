"""Proven historical Spark swap earnings, without new borrowed principal.

The registry is reproduced by scripts/audit_spark_par_swaps.py from complete
Swap/TokenExchange and token-transfer evidence. Mixed liquidity changes and
cash mismatches are excluded. This never changes published revenue policy.
"""

import json
from dataclasses import replace
from decimal import Decimal as D
from functools import lru_cache
from pathlib import Path

PREFIX = "ethereum:0x1601843c5e9bc251a3272907010afa41fa18347e:"
MARKER = ":par-swap-gain"


@lru_cache(maxsize=1)
def rules():
    path = Path(__file__).resolve().parents[3] / "config/capital-tracing/spark-par-swap-gains.json"
    rows = json.loads(path.read_text())["rows"]
    if len({r["identity"] for r in rows}) != len(rows):
        raise ValueError("Duplicate Spark swap-gain rule")
    for r in rows:
        if (
            not r["account"].startswith(PREFIX)
            or not D(r["earned"]).is_finite()
            or D(r["earned"]) <= 0
        ):
            raise ValueError("Invalid Spark swap-gain rule")
    return tuple(rows)


def recognize_spark_par_swap_gains(history):
    if not any(a.startswith(PREFIX) for a in history.venue_accounts.values()):
        return history
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError("Duplicate Spark swap-gain batch")
    changes = {}
    for r in rules():
        identity = r["identity"]
        already = identity + MARKER in index
        if already and identity in index:
            raise ValueError("Mixed Spark swap-gain transformation")
        batch = index.get(identity + MARKER if already else identity)
        if batch is None:
            continue
        if (batch.chain, batch.block, batch.timestamp) != ("ethereum", r["block"], r["timestamp"]):
            raise ValueError("Spark swap-gain transaction metadata changed")
        movements = list(batch.movements)
        matches = [i for i, m in enumerate(movements) if m.account == r["account"]]
        if len(matches) != 1:
            raise ValueError("Spark swap-gain receiving account missing")
        i = matches[0]
        old = movements[i]
        earned = D(r["earned"])
        if abs(old.change - D(r["change"])) > D("1e-12") or old.external_income != (
            earned if already else 0
        ):
            raise ValueError("Spark swap-gain cash or income changed")
        if already:
            continue
        movements[i] = replace(old, external_income=earned)
        changes[identity] = replace(batch, identity=identity + MARKER, movements=tuple(movements))
    return (
        replace(history, batches=tuple(changes.get(b.identity, b) for b in history.batches))
        if changes
        else history
    )
