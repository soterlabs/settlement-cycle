#!/usr/bin/env python3
"""Recompute September with the approved SOFR snapshot; only write September.

Normal RPC/indexer environment is required. No API writes or DR replay.
Use --include-protocol after all six prime reports to build non-MSC/Sky/TMF.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from settle.compute.monthly_pnl import compute_monthly_pnl  # noqa: E402
from settle.domain import Month  # noqa: E402
from settle.domain.config import load_prime_by_id  # noqa: E402
from settle.load.writer import write_settlement  # noqa: E402
from settle.revenue.monthly import _reference_history, validate_interest  # noqa: E402
from settle.revenue.september_close import approved_reference  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--primes', nargs='+', default=['spark', 'grove', 'osero', 'obex', 'keel', 'skybase'],
                        choices=['spark', 'grove', 'osero', 'obex', 'keel', 'skybase'])
    parser.add_argument('--allow-september-sofr-carry', action='store_true', required=True,
                        help='Explicitly use approved 3.88%% September 29 SOFR for September 30')
    parser.add_argument('--include-protocol', action='store_true')
    args = parser.parse_args()
    month = Month(2026, 9)
    from types import SimpleNamespace
    period = SimpleNamespace(start=month.first_day, end=month.last_day)
    for name in args.primes:
        prime = load_prime_by_id(name, config_dir=ROOT / 'config')
        provenance = {'reference_rates': approved_reference()} if prime.subsidy.enabled else {}
        history = _reference_history(prime, period, provenance, allow_september_sofr_carry=True)
        pnl = compute_monthly_pnl(prime, month, reference_rate_history=history)
        validate_interest(pnl, prime, provenance, allow_september_sofr_carry=True)
        sources = {'calculation': 'scripts/run_september_close.py; independent full monthly calculation',
                   'reference_rate_status': 'operator-authorized Sep 29 SOFR 3.88% carried to Sep 30'
                   if history is not None else 'not applicable'}
        paths = write_settlement(pnl, ROOT / 'settlements' / name / str(month), sources=sources)
        if 'xlsx' not in paths:
            raise RuntimeError(f'{name}: settlement workbook not generated')
        payload = json.loads(paths['provenance'].read_text())
        payload['close_reference_rate_provenance'] = provenance.get('reference_rates')
        paths['provenance'].write_text(json.dumps(payload, indent=2))
        print(f'{name}: September report written', flush=True)
    if args.include_protocol:
        for script in ['run_non_msc_2026.py', 'build_sky_total_2026.py', 'run_tmf_2026.py']:
            subprocess.run([sys.executable, str(ROOT / 'scripts' / script), '--months', '2026-09'],
                           cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
