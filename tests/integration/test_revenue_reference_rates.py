from dataclasses import replace
from datetime import date
from decimal import Decimal

import psycopg
import pytest

from settle.domain.config import load_prime_by_id
from settle.domain.period import Month, Period
from settle.revenue import reference_rates as rates, store, worker
from settle.revenue.verification import validate_window
from tests.integration.test_input_cache_postgres import database  # noqa: F401
from tests.integration.test_revenue_store import example


def test_official_snapshot_drives_compute_and_revision_reuse(database, monkeypatch):  # noqa: F811
    cutoff = date(2026, 9, 14)
    monkeypatch.setattr(store, 'validate_window', lambda d: validate_window(d, today=date(2026, 9, 16)))
    value = ['3.62']
    fetched = []
    class Response:
        def raise_for_status(self): pass
        def json(self, **kw): return {'refRates': [
            {'type': 'SOFR', 'effectiveDate': str(d), 'percentRate': value[0]}
            for d in fetched[-1]]}
    def get(url, *, params, timeout):
        assert url == rates.SOURCE
        calendar = __import__('yaml').safe_load(rates.CALENDAR.read_text())
        from datetime import timedelta
        lo, hi = map(date.fromisoformat, (params['startDate'], params['endDate']))
        fetched.append(sorted({rates.effective_day(lo+timedelta(days=i), calendar)
                               for i in range((hi-lo).days+1)}))
        return Response()
    monkeypatch.setattr(rates.requests, 'get', get)
    pnl = replace(example(), prime_id='grove', period=Period.from_month(Month(2026, 9), {}, as_of=cutoff))
    computed = []
    def compute(config, month, *, as_of, reference_rate_history):
        rate = reference_rate_history.at(as_of)
        assert rate == Decimal(value[0])/100
        computed.append(rate)
        return replace(pnl, revenue=rate)
    versions = store.Versions('code', 'cfg', 'manual')
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        def run():
            return worker.run_prime(conn, 'grove', today=date(2026, 9, 15), compute=compute,
                                    capture=lambda: versions, attempts=1)
        assert run()['status'] == 'ok'
        original = store.read(conn, 'grove')
        meta = original['input_provenance']['reference_rates']
        assert meta['coverage_complete']
        assert meta['carry_forward_dates']['2026-09-07'] == '2026-09-04'
        assert original['input_revision'] != versions.inputs
        assert run()['results'][0]['status'] == 'reused'
        assert len(computed) == 1 and len(fetched) == 2
        value[0] = '3.63'
        assert run()['status'] == 'ok'
        corrected = store.read(conn, 'grove')
        assert corrected['revision_id'] != original['revision_id']
        assert corrected['input_revision'] != original['input_revision']
        assert len(store.revisions(conn, 'grove', cutoff)) == 2
        assert conn.execute('SELECT count(*) FROM revenue_reference_snapshots').fetchone()[0] == 2
        # A provider correction can return to a previously observed rate.
        # That must become a new latest revision, not reuse an obsolete pointer.
        value[0] = '3.62'
        assert run()['status'] == 'ok'
        reverted = store.read(conn, 'grove')
        assert reverted['revision_id'] not in {original['revision_id'], corrected['revision_id']}
        assert reverted['result']['revenue'] == '0.0362'
        assert len(store.revisions(conn, 'grove', cutoff)) == 3
        assert run()['results'][0]['status'] == 'reused'
        assert len(computed) == 3
        def unavailable(*args, **kwargs): raise rates.ReferenceRatesUnavailable('not yet published')
        monkeypatch.setattr(rates.requests, 'get', unavailable)
        assert run()['status'] == 'failed'
        assert store.read(conn, 'grove')['revision_id'] == reverted['revision_id']


def test_missing_middle_business_day_blocks_before_calculation(database, monkeypatch):  # noqa: F811
    class Response:
        def raise_for_status(self): pass
        def json(self, **kw): return {'refRates': [
            {'type': 'SOFR', 'effectiveDate': '2026-09-14', 'percentRate': '3.62'}]}
    monkeypatch.setattr(rates.requests, 'get', lambda *a, **kw: Response())
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        with pytest.raises(rates.ReferenceRatesUnavailable, match='Missing official'):
            rates.prepare(conn, load_prime_by_id('grove'), date(2026, 9, 14))
        assert conn.execute('SELECT count(*) FROM revenue_reference_snapshots').fetchone()[0] == 0


def test_full_calculation_consumes_supplied_history(database, monkeypatch):  # noqa: F811
    import os
    import subprocess
    import sys
    program = '''
from datetime import date
from decimal import Decimal
from unittest.mock import patch
import os, pandas as pd, requests
from settle.compute import compute_monthly_pnl
from settle.compute import monthly_pnl
from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.domain.subsidy import ReferenceRateHistory
from settle.extract.rpc import RPC_ENV_VARS
from tests.fixtures.revenue_transport import send
for name in RPC_ENV_VARS.values(): os.environ[name] = 'http://fixture.invalid'
history = ReferenceRateHistory(pd.DataFrame([{'effective_date':date(2026,8,1), 'ref_rate_apr':Decimal('0.0123')}]), 'sofr')
original = monthly_pnl.compute_sky_revenue_daily
seen = []
def inspect(*args, **kwargs):
 assert kwargs['ref_rate_history'] is history
 seen.append(True)
 return original(*args, **kwargs)
with patch.object(requests.Session, 'send', send), patch.object(monthly_pnl, 'load_reference_rates_for', side_effect=AssertionError('must not load YAML rates')), patch.object(monthly_pnl, 'compute_sky_revenue_daily', inspect):
 result = compute_monthly_pnl(load_prime_by_id('grove'), Month(2026,8), as_of=date(2026,8,1), reference_rate_history=history)
assert seen
assert result.sky_revenue_daily[0]['ref_rate_apr'] == .0123
'''
    env = dict(os.environ, PYTHON_DOTENV_DISABLED='1', DATABASE_URL=database,
               SETTLE_REQUIRE_POSTGRES='1', ENVIO_API_TOKEN='fixture')
    result = subprocess.run([sys.executable, '-c', program], capture_output=True, text=True, env=env, timeout=90)
    assert result.returncode == 0, result.stderr


def test_weekend_month_opening_requires_the_prior_business_day_seed(database, monkeypatch):  # noqa: F811
    class Response:
        def raise_for_status(self): pass
        def json(self, **kw): return {'refRates': [
            {'type': 'SOFR', 'effectiveDate': '2026-07-31', 'percentRate': '3.62'}]}
    calls = []
    def get(url, *, params, timeout):
        calls.append(params)
        return Response()
    monkeypatch.setattr(rates.requests, 'get', get)
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        snapshot = rates.prepare(conn, load_prime_by_id('grove'), date(2026, 8, 1))
        assert snapshot.history.at(date(2026, 8, 1)) == Decimal('0.0362')
        assert calls == [{'startDate': '2026-07-31', 'endDate': '2026-08-01'}]
        assert snapshot.provenance['reference_rates']['carry_forward_dates'] == {'2026-08-01': '2026-07-31'}
