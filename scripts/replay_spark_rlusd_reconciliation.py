"""Offline corrected September replay; save custody funding and reprice costs.

Never fetch provider data or publish. Old artifacts remain immutable. The two
new venues receive explicit research cost policies, without changing revenue.
"""

import argparse
import gzip
import hashlib
import json
import logging
import socket
from datetime import UTC, date, datetime
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from audit_saved_funding_gap import read
from audit_spark_mixed_funding_yields import prepare_history, save_snapshot, selected_accounts
from build_spark_cost_rows import build
from report_allocation_capital_readiness import price_daily

from settle.compute.allocation_capital import replay_history
from settle.compute.allocation_funding_bridge import validate_funding_bridge
from settle.normalize.allocation_history_cache import load_history


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--history", type=Path, required=True)
    p.add_argument("--saved-dir", type=Path, required=True)
    p.add_argument("--control", type=Path, required=True)
    p.add_argument("--idle", type=Path, required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    a = p.parse_args()
    a.output_dir.mkdir(exist_ok=True, parents=True)

    def write(name, value):
        path = a.output_dir / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        raw = (json.dumps(value, default=str, indent=2) + "\n").encode()
        tmp.write_bytes(gzip.compress(raw, mtime=0) if name.endswith(".gz") else raw)
        tmp.replace(path)

    def status(stage):
        write(
            "status.json",
            {"stage": stage, "updated_at": datetime.now(UTC).isoformat(), "publishable": False},
        )
        logging.warning(stage)

    def offline(*args, **kwargs):
        raise RuntimeError("Offline replay cannot access network")

    socket.socket.connect = offline
    paths = [
        a.history,
        a.history.with_suffix(".audit.json"),
        a.control,
        a.idle,
        *[
            a.saved_dir / n
            for n in ("conditional-costs.json.gz", "global-comparison.json", "vat-accrual.json")
        ],
    ]
    hashes = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in paths}
    source_paths = [
        Path(__file__),
        Path("scripts/patch_spark_rlusd_history.py"),
        *sorted(Path("src/settle/compute").glob("*.py")),
    ]
    sources = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in source_paths}
    write("identity.json", {"inputs": hashes, "sources": sources})
    try:
        status("loading_saved_corrected_history")
        with gzip.open(a.history, "rt") as f:
            header = json.loads(next(f))
        history = prepare_history(load_history(a.history, header["fingerprint"]))
        selected = set(history.venue_accounts.values()) | {
            v for vals in history.custody_accounts.values() for v in vals
        }
        diagnostic = {
            m.account for b in history.batches for m in b.movements if m.account.startswith("psm:")
        }
        diagnostic |= {
            "financing:saver-interest:" + op.source
            for b in history.batches
            for op in b.external_funding
            if op.kind == "interest"
        }
        status("replaying_offline_with_custody_funding")
        replay = replay_history(
            history, date(2026, 8, 31), date(2026, 9, 30), funding_accounts=selected | diagnostic
        )
        # Diagnostics must not turn PSM3 into selected allocations. Restore
        # the disjoint bridge buckets before saving the venue snapshot.
        diagnostic_rows = {}
        with localcontext() as ctx:
            ctx.prec = 60
            for day, rows in replay.daily_funding.items():
                diagnostic_rows[str(day)] = {k: rows.pop(k) for k in diagnostic - selected}
                for source in replay.daily_funding_bridge[day]["sources"]:
                    amount = sum(
                        (
                            r["sky_principal"]
                            if source["kind"] == "sky"
                            else r["savings_by_source"].get(source["source"], D(0))
                            for k, r in diagnostic_rows[str(day)].items()
                            if k.startswith("psm:")
                        ),
                        D(0),
                    )
                    source["selected_principal"] -= amount
                    source["other_principal"] += amount
                validate_funding_bridge(replay.daily_funding_bridge[day], rows)
        status("saving_replayed_funding")
        write("custody-funding.json.gz", diagnostic_rows)
        write("daily-by-ilk.json.gz", {str(d): v for d, v in replay.daily_by_ilk.items()})
        save_snapshot(
            a.output_dir / "funding-snapshot.json.gz",
            {"accounts": sorted(selected), "input_sha256": hashes, "source_sha256": sources},
            replay,
        )
        status("repricing_and_reconciling")
        control = read(a.control)
        idle = read(a.idle)
        old = read(a.saved_dir / "conditional-costs.json.gz")
        days = {r["date"]: r for r in control["sky_revenue_daily"]}
        rates = {d: D(r["daily_sky_rev"]) / D(r["utilized"]) for d, r in days.items()}
        policies = {r["venue_id"]: r for r in control["venue_breakdown"]}
        sde = {
            r["venue_id"]: {v["block_date"]: v["cum_value"] for v in r["daily"]}
            for r in control["sde_daily_breakdown"]
        }
        oldrows = {r["venue"]: r for r in old["allocations"]}
        account_map = selected_accounts(history, [*oldrows, "S70", "S72"])
        rows = []
        for venue, keys in account_map.items():
            daily = {}
            for d in days:
                parts = [replay.daily_funding[date.fromisoformat(d)][k] for k in keys]
                daily[d] = {
                    "sky_principal": sum((r["sky_principal"] for r in parts), D(0)),
                    "savings_principal": sum(
                        (sum(r["savings_by_source"].values(), D(0)) for r in parts), D(0)
                    ),
                    "uncertain": any(r["uncertain"] for r in parts),
                }
            priced = (
                venue in ("S70", "S72")
                or oldrows[venue]["conditional_sky_borrowing_cost"] is not None
            )
            excluded = policies.get(venue, {}).get("cof_excluded", False) or all(
                k in history.idle_accounts for k in keys
            )
            cost = (
                price_daily(
                    {d: r["sky_principal"] for d, r in daily.items()},
                    rates,
                    idle["daily"].get(venue, {}),
                    sde.get(venue, {}),
                    excluded=excluded,
                )
                if priced
                else None
            )
            rows.append({"venue": venue, "daily": daily, "conditional_sky_borrowing_cost": cost})
        conditional = {
            "start": "2026-09-01",
            "end": "2026-09-30",
            "publishable": False,
            "allocations": rows,
        }
        write("conditional-costs.json.gz", conditional)
        comparison = build(
            control,
            read(a.output_dir / "funding-snapshot.json.gz"),
            conditional,
            {str(d): v for d, v in replay.daily_by_ilk.items()},
            read(a.saved_dir / "vat-accrual.json"),
            idle,
            header,
            read(a.saved_dir / "global-comparison.json"),
            {"associated_residual_cost": "0"},
        )
        comparison["rlusd_routes_incorporated"] = True
        comparison["limitations"] = [
            v for v in comparison["limitations"] if not v.startswith("RLUSD deposit costs")
        ]
        comparison["limitations"].append(
            "RLUSD corrected funding remains conditional on the replay attribution policy; no API publication."
        )
        comparison["input_sha256"] = hashes
        write("reconciliation.json", comparison)
        assert hashes == {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in paths}
        assert sources == {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in source_paths}
        status("completed_not_published")
    except Exception as exc:
        write("status.json", {"stage": "failed", "error": str(exc), "publishable": False})
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    main()
