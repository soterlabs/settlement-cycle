#!/usr/bin/env python3
"""Close Grove's October 2026 MSC settlement using the official SOFR snapshot.

Requires normal RPC/indexer credentials and a finalized October DR source.
Writes only settlements/grove/2026-10; does not publish to the API.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from settle.compute import compute_monthly_pnl  # noqa: E402
from settle.domain import Month  # noqa: E402
from settle.domain.config import load_prime_by_id  # noqa: E402
from settle.load.dr_rewards import load_dr  # noqa: E402
from settle.load.writer import write_settlement  # noqa: E402
from settle.revenue.monthly import _reference_history, validate_interest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-inputs', action='store_true',
                        help='Validate SOFR and DR inputs without extracting on-chain data')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
    prime = load_prime_by_id('grove', config_dir=ROOT / 'config')
    month = Month(2026, 10)
    period = SimpleNamespace(start=month.first_day, end=month.last_day)
    reference = json.loads((ROOT / 'config/october_2026_official_reference_rates.json').read_text())
    provenance = {'reference_rates': reference}
    history = _reference_history(prime, period, provenance)
    if load_dr(prime.id, str(month)) is None:
        raise RuntimeError('October 2026 Grove DR is unavailable; refusing to write a settlement with missing rewards')
    if args.check_inputs:
        print('Grove October 2026: official SOFR coverage and DR inputs validated')
        return 0
    pnl = compute_monthly_pnl(prime, month, reference_rate_history=history)
    validate_interest(pnl, prime, provenance)
    paths = write_settlement(
        pnl, ROOT / 'settlements' / prime.id / str(month),
        sources={'calculation': 'scripts/run_october_grove_close.py; independent full monthly calculation',
                 'reference_rate_status': 'official coverage complete; NY Fed SOFR via FRED'},
        reference_rates=reference,
    )
    if 'xlsx' not in paths:
        raise RuntimeError('Grove October settlement workbook was not generated')
    print('Grove: October 2026 MSC settlement written')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
