#!/usr/bin/env python3
"""Calculate selected October 2026 primes with reviewed transfers and official rates.

Load credentials into the environment before invoking. Defaults to the five
primes added after Grove. Does not publish to the API or overwrite reports.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from settle.compute import compute_monthly_pnl  # noqa: E402
from settle.domain import Month  # noqa: E402
from settle.domain.config import load_prime_by_id  # noqa: E402
from settle.load.dr_rewards import load_dr  # noqa: E402
from settle.load.dr_snapshot import snapshot_rows  # noqa: E402
from settle.load.writer import check_settlement_destination, write_settlement  # noqa: E402
from settle.revenue.monthly import _reference_history, validate_interest  # noqa: E402

PRIMES = ("spark", "obex", "osero", "keel", "skybase", "grove")


def check_review(prime_id: str) -> str:
    """Check the attestation's integrity; actual review follows AGENTS.md."""
    directory = ROOT / ".msc-transfer-reviews" / prime_id / "2026-10"
    path = directory / "review.json"
    record = json.loads(path.read_text())
    expected_period = {"from": "2026-10-01T00:00:00Z", "to_exclusive": "2026-11-01T00:00:00Z"}
    if (not (record.get("period") or record.get("utc_period"))
            or record.get("period", "2026-10") != "2026-10"
            or record.get("utc_period", expected_period) != expected_period):
        raise ValueError(f"{prime_id}: transfer review does not cover October 2026")
    if (record.get("prime") != prime_id or record.get("status") != "reviewed"
            or record.get("unclassified_count") != 0):
        raise ValueError(f"{prime_id}: incomplete October ALM transfer review")
    digest = hashlib.sha256((ROOT / "config" / f"{prime_id}.yaml").read_bytes()).hexdigest()
    if record.get("config_sha256") != digest:
        raise ValueError(f"{prime_id}: prime configuration changed since review")
    evidence = record.get("evidence_hashes") or {}
    if not evidence:
        raise ValueError(f"{prime_id}: transfer review has no evidence hashes")
    for name, expected in evidence.items():
        artifact = directory / name
        if artifact.parent != directory or hashlib.sha256(artifact.read_bytes()).hexdigest() != expected:
            raise ValueError(f"{prime_id}: transfer review evidence mismatch: {name}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primes", default=",".join(PRIMES[:-1]))
    parser.add_argument("--output-root", type=Path, default=ROOT / "settlements")
    parser.add_argument("--check-inputs", action="store_true")
    args = parser.parse_args()
    selected = args.primes.split(",")
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(PRIMES):
        parser.error("--primes must be a unique comma-separated selection of " + ",".join(PRIMES))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    month = Month(2026, 10)
    reference = json.loads((ROOT / "config/october_2026_official_reference_rates.json").read_text())
    period = SimpleNamespace(start=month.first_day, end=month.last_day)
    # Validate the full batch before any calculation or report write.
    if snapshot_rows(ROOT, str(month)) is None:
        raise ValueError("Finalized October DR snapshot is unavailable")
    prepared = []
    for prime_id in selected:
        prime = load_prime_by_id(prime_id, config_dir=ROOT / "config")
        history = _reference_history(prime, period, {"reference_rates": reference})
        rewards = load_dr(prime_id, str(month))
        if args.check_inputs:
            print(f"{prime_id}: official rates validated; DR={rewards['total'] if rewards else 0}")
            continue
        review_hash = check_review(prime_id)
        destination = args.output_root / prime_id / str(month)
        check_settlement_destination(destination)
        prepared.append((prime, history, review_hash, destination))
    if args.check_inputs:
        return 0
    for prime, history, review_hash, destination in prepared:
        pnl = compute_monthly_pnl(prime, month, reference_rate_history=history)
        validate_interest(pnl, prime, {"reference_rates": reference})
        paths = write_settlement(
            pnl, destination,
            sources={"calculation": "scripts/run_october_close.py; full October 2026 calculation",
                     "alm_transfer_review_sha256": review_hash,
                     "reference_rate_status": "official October coverage; NY Fed SOFR via FRED"},
            reference_rates=reference,
        )
        if "xlsx" not in paths:
            raise RuntimeError(f"{prime.id}: canonical workbook generation failed")
        # Keep generated Markdown clean under git diff --check.
        summary = paths["summary"]
        summary.write_text(summary.read_text().rstrip() + "\n")
        print(f"{prime.id}: October 2026 summary and workbook written", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
