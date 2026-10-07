"""Explicit operator-approved September 2026 SOFR input; never a default fallback."""
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from .verification import digest

SNAPSHOT = Path(__file__).resolve().parents[3] / 'config/september_2026_reference_rates.json'
_KEYS = ('calendar_version', 'series', 'carry_forward_dates')


def approved_reference() -> dict:
    """The complete month snapshot used for the approved 3.88% carry-forward."""
    saved = json.loads(SNAPSHOT.read_text())
    content = {k: saved[k] for k in _KEYS}
    if digest(content) != saved['snapshot_id']:
        raise ValueError('Approved September reference snapshot hash mismatch')
    observations = saved['series']['sofr']['observations']
    if (saved['coverage_complete'] is not False
            or saved['carry_forward_dates'].get('2026-09-30') != '2026-09-29'
            or not any(r['effective_date'] == '2026-09-29' and Decimal(r['apr']) == Decimal('0.0388')
                       for r in observations)
            or any(r['effective_date'] >= '2026-09-30' for r in observations)):
        raise ValueError('Invalid approved September carry-forward')
    return saved


def validate_authorized_reference(prime, period, saved: dict) -> None:
    """Permit only this exact, pinned exception for Spark/Grove September.

    The caller must opt in. Other missing observations still fail closed, and
    the global SOFR calendar and automatic API publisher are unchanged.
    """
    if (prime.id not in {'spark', 'grove'} or period.start != date(2026, 9, 1)
            or period.end != date(2026, 9, 30)):
        raise ValueError('SOFR carry-forward is authorized only for Spark/Grove September 2026')
    approved = approved_reference()
    if (saved.get('coverage_complete') is not False
            or any(saved.get(k) != approved[k] for k in (*_KEYS, 'snapshot_id'))
            or any(saved.get('operator_estimate', {}).get(k) != approved['operator_estimate'][k]
                   for k in ('kind', 'effective_date', 'source_effective_date', 'apr'))):
        raise ValueError('Reference snapshot does not match approved September carry-forward')
