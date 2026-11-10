"""Frozen-rate replay must measure only the newly omitted USDS deduction."""
from decimal import Decimal as D

import pytest

from scripts.audit_usds_bridge_transit_2026 import _frozen_charge_delta, cost_delta


def test_audit_preserves_original_subsidy_cap_and_zero_floor():
    row = {'utilized': '150', 'daily Sky charge': str(D(100)*D('.02')/365+D(50)*D('.04')/365),
           'base APR': '.04', 'sub APR': '.02'}
    # Removing 100 crosses the cap: only 50 remains at the subsidised rate.
    assert abs(_frozen_charge_delta(row, D(100), D(100)) - D(3)/365) < D('1e-20')
    assert abs(_frozen_charge_delta(row, D(200), D(100)) - D(4)/365) < D('1e-20')


def test_audit_does_not_guess_unreproducible_charge_or_double_deduct():
    row = {'utilized': '100', 'daily Sky charge': '999', 'base APR': '.04', 'alm_usds':'0'}
    assert cost_delta(row, D(50), D(100), wallet_usds=D(0)) is None
    with pytest.raises(ValueError, match='verified wallet'):
        cost_delta(row, D(50), D(100))


def test_superseded_additive_estimate_remains_reproducible_but_is_not_a_credit():
    import hashlib
    import json
    from functools import cache

    from scripts.audit_usds_bridge_transit_2026 import ROOT, historical_rows
    path = ROOT / 'reconciliation/usds-bridge-2026'
    report = json.loads((path / 'audit.json').read_text())
    spark = report['primes']['spark']
    retained = json.loads((path / 'spark-october-retained-daily.json').read_text())
    assert retained['revision_id'] == spark['retained_source']['revision_id']
    october = {r['date']: r for r in retained['daily']}
    books = cache(historical_rows)
    totals = {'historical': D(0), 'october': D(0)}
    for name, expected in spark['baseline_workbook_sha256'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected
    for day in spark['daily']:
        month = day['date'][:7]
        # Use the retained October baseline even when a new October close exists.
        row = (books('spark', month) or {}).get(day['date']) if month != '2026-10' else None
        if day['date'] in october:
            r = october[day['date']]
            row = {'utilized': r['utilized'], 'daily Sky charge': r['daily_sky_rev'],
                   'base APR': r['base_apr'], 'sub APR': r.get('sub_apr'),
                   'bridge_usds': r.get('bridge_usds', '0')}
        if day['measured_excess_charge'] is None:
            assert row is None  # November is unmeasured, not zero.
            continue
        # Deliberately reproduce ONLY the superseded scalar arithmetic. Actual
        # workbook callers are now rejected without verified wallet evidence.
        row = {k: v for k, v in row.items() if k not in ('− ALM idle', 'alm_usds')}
        measured = _frozen_charge_delta(row, D(day['in_transit_usds']), D(1000000000))
        assert abs(measured - D(day['measured_excess_charge'])) < D('1e-20')
        totals['historical' if month <= '2026-09' else 'october'] += measured
    assert totals['historical'].quantize(D('.01')) == D('255869.48')
    assert totals['october'].quantize(D('.01')) == D('75063.88')


def test_zero_native_usds_matches_does_not_certify_other_grove_chains():
    import json
    from datetime import date

    from scripts.audit_usds_bridge_transit_2026 import ROOT, bridge_scope
    from settle.domain import Chain, Period
    from settle.domain.config import load_prime_by_id
    data = json.loads((ROOT / 'reconciliation/usds-bridge-2026/audit.json').read_text())
    grove = data['primes']['grove']
    period = Period(date(2026, 1, 1), date(2026, 11, 8),
                    {Chain(c): n for c, n in data['pins'].items()})
    scope = bridge_scope(load_prime_by_id('grove'), period)
    assert all(grove[k] == v for k, v in scope.items())
    assert grove['transfers'] == 0
    assert grove['result_scope'] == 'supported_native_usds_routes_only'
    assert grove['supported_ethereum_routes'] == ['base']
    assert set(grove['unscanned_alm_chains']) == {'avalanche_c', 'plume', 'monad'}
