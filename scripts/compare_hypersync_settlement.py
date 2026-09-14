"""Compare complete calculations without writing or replacing settlement files.

The baseline uses Dune for the migrated event inputs. The candidate uses the
checked-in configuration, with every Dune execute_query alias forbidden even
on a cache hit. RPC contract valuation remains shared between both runs.
"""

import argparse
import dataclasses
import json
import sys
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from settle.compute import Sources, compute_monthly_pnl
from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.extract import dune
from settle.normalize.sources.dune_balances import DuneBalanceSource
from settle.normalize.sources.dune_ssr import DuneSSRSource


def differences(left, right, path="result"):
    if dataclasses.is_dataclass(left):
        left, right = dataclasses.asdict(left), dataclasses.asdict(right)
    if isinstance(left, dict) and isinstance(right, dict):
        for key in left.keys() | right.keys():
            if key not in left or key not in right:
                yield {"path": f"{path}.{key}", "reason": "missing key"}
            else:
                yield from differences(left[key], right[key], f"{path}.{key}")
    elif isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            yield {"path": path, "reason": "different lengths"}
        for i, (a, b) in enumerate(zip(left, right)):
            yield from differences(a, b, f"{path}[{i}]")
    elif isinstance(left, (Decimal, float)) and isinstance(right, (Decimal, float)):
        delta = abs(Decimal(str(left)) - Decimal(str(right)))
        if delta > Decimal("0.000001"):
            yield {"path": path, "dune": str(left), "hypersync": str(right), "difference": str(delta)}
    elif left != right:
        yield {"path": path, "dune": str(left), "hypersync": str(right)}


def compare(prime_id, month_label):
    prime, month = load_prime_by_id(prime_id), Month.parse(month_label)
    if prime.psm:
        raise ValueError("This full-calculation oracle currently covers primes without PSM; use the PSM comparator separately")
    oracle = dataclasses.replace(prime, venues=[dataclasses.replace(v, event_source="dune") for v in prime.venues])
    print("Dune baseline", prime_id, month_label, flush=True)
    baseline = compute_monthly_pnl(oracle, month, sources=Sources(balance=DuneBalanceSource(), ssr=DuneSSRSource()))
    print("HyperSync candidate (Dune forbidden)", prime_id, month_label, flush=True)
    original = dune.execute_query
    forbidden = []
    with ExitStack() as stack:
        # Imports throughout normalize bind the function directly. Patch all
        # aliases plus its defining module so future imports fail too.
        for module in list(sys.modules.values()):
            if module and getattr(module, "__name__", "").startswith("settle."):
                for name, value in list(vars(module).items()):
                    if value is original:
                        forbidden.append(stack.enter_context(patch.object(module, name, side_effect=AssertionError("Unexpected Dune query in migrated calculation"))))
        candidate = compute_monthly_pnl(prime, month)
    calls = sum(mock.call_count for mock in forbidden)
    if calls:
        raise AssertionError(f"Migrated calculation attempted {calls} Dune queries (including swallowed fallback errors)")
    mismatches = list(differences(baseline, candidate))
    fields = ["sky_revenue", "agent_rate", "prime_agent_revenue", "monthly_pnl"]
    return {"prime": prime_id, "month": month_label, "matched": not mismatches,
            "comparison": "all MonthlyPnL dataclass fields; absolute numeric tolerance 0.000001",
            "candidate_dune_calls": 0, "mismatches": mismatches,
            "headlines": {f: {"dune": str(getattr(baseline, f)), "hypersync": str(getattr(candidate, f))} for f in fields}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prime", required=True)
    parser.add_argument("--month", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.prime, args.month)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print("matched", report["matched"], flush=True)
    return 0 if report["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
