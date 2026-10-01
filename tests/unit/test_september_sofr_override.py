"""An explicit one-month authorization must not become a generic missing-rate bypass."""
import copy
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from settle.domain.config import load_prime_by_id
from settle.revenue.monthly import _reference_history
from settle.revenue.september_close import approved_reference
from settle.revenue.verification import digest

ROOT = Path(__file__).resolve().parents[2]


def prime(name='spark'):
    return load_prime_by_id(name, config_dir=ROOT / 'config')


def period():
    return SimpleNamespace(start=date(2026, 9, 1), end=date(2026, 9, 30))


@pytest.mark.parametrize('name', ['spark', 'grove'])
def test_only_explicit_opt_in_accepts_authorized_snapshot(name):
    provenance = {'reference_rates': approved_reference()}
    with pytest.raises(ValueError, match='invalid reference-rate snapshot'):
        _reference_history(prime(name), period(), provenance)
    history = _reference_history(prime(name), period(), provenance, allow_september_sofr_carry=True)
    assert history.at(date(2026, 9, 30)) == Decimal('0.0388')
    assert provenance['reference_rates']['coverage_complete'] is False


@pytest.mark.parametrize('problem', ['rate', 'missing_day', 'carry', 'authorization', 'month', 'prime'])
def test_override_rejects_other_missing_or_modified_inputs(problem):
    saved = copy.deepcopy(approved_reference())
    p, window = prime(), period()
    if problem == 'rate':
        saved['series']['sofr']['observations'][-1]['apr'] = '0.04'
    elif problem == 'missing_day':
        saved['series']['sofr']['observations'].pop(3)
    elif problem == 'carry':
        saved['carry_forward_dates']['2026-09-30'] = '2026-09-28'
    elif problem == 'authorization':
        saved['operator_estimate'] = {}
    elif problem == 'month':
        window.end = date(2026, 10, 31)
    else:
        p = SimpleNamespace(id='unapproved', subsidy=p.subsidy)
    # A self-consistent forged hash still does not match the approved input.
    saved['snapshot_id'] = digest({k: saved[k] for k in ('calendar_version', 'series', 'carry_forward_dates')})
    with pytest.raises(ValueError, match=r'authorized|approved'):
        _reference_history(p, window, {'reference_rates': saved}, allow_september_sofr_carry=True)
