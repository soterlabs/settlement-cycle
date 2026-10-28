from dataclasses import dataclass, field, replace
from decimal import Decimal

import psycopg

from settle.revenue import financial_backfill, financial_store, store, worker
from settle.revenue.financial import decode_selection, statements
from tests.integration.test_input_cache_postgres import database  # noqa: F401
from tests.integration.test_revenue_store import Pnl, example


@dataclass
class FinancialPnl(Pnl):
    agent_rate: Decimal = Decimal('12.000000000000000001')
    sky_revenue: Decimal = Decimal('130')
    sde_revenue: Decimal = Decimal('30')
    susds_spread_reimbursement: Decimal = Decimal('5')
    venue_breakdown: list = field(default_factory=lambda: [dict(venue_id='V1', pricing_category='B',
        sd_share=Decimal('0'), revenue=Decimal('120'), external_revenue=Decimal('20'),
        value_som=Decimal('1000'), value_eom=Decimal('1100'))])


def test_saved_backfill_replay_corrections_and_read_only_annual(database):  # noqa: F811
    base = example()
    pnl = FinancialPnl(base.prime_id, base.period, base.pin_blocks_som)
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        versions = store.Versions('code', 'config', '0')
        first = store.publish(conn, pnl, versions)
        original = store.read(conn, 'obex')
        report = financial_backfill.run(conn, 'obex', pnl.as_of, pnl.as_of)
        assert report['published'] == 1 and report['failed'] == 0
        assert financial_backfill.run(conn, 'obex', pnl.as_of, pnl.as_of)['reused'] == 1
        saved = financial_store.read(conn, 'obex')
        rows = financial_store.year_rows(conn, 'obex', pnl.as_of.year)
        annual = statements('obex', pnl.as_of.year, pnl.as_of, rows)
        assert saved['document']['metrics']['funding.sky.net']['value'] == '100'
        assert store.read(conn, 'obex') == original
        corrected = store.publish(conn, replace(pnl, agent_rate=Decimal('99')), replace(versions, inputs='corrected'))
        assert financial_store.year_rows(conn, 'obex', pnl.as_of.year)[0]['source_lag'] is True
        assert financial_store.publish_revision(conn, 'obex', corrected) == 'succeeded'
        assert financial_store.read(conn, 'obex')['source_revision'] == corrected
        assert financial_store.read(conn, 'obex', source_revision=first)['revision_id'] == saved['revision_id']
        selection = decode_selection(annual['selection'], 'obex', pnl.as_of.year)
        retained = financial_store.year_rows(conn, 'obex', pnl.as_of.year,
                    revisions=[x for x in selection['revisions'] if x])
        assert statements('obex', pnl.as_of.year, pnl.as_of, retained, selection=selection) == annual
        assert conn.execute('SELECT COUNT(*) FROM reporting_financial_results').fetchone()[0] == 2


def test_derived_failure_does_not_mark_committed_source_failed(database, monkeypatch):  # noqa: F811
    pnl = example()
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        def fail(*args):
            raise ValueError('invalid derivation')
        monkeypatch.setattr(financial_store, 'build', fail)
        result = worker.run_prime(conn, 'obex', start=pnl.as_of, end=pnl.as_of,
            compute=lambda *args, **kw: pnl, capture=lambda: store.Versions('code', 'cfg', '0'),
            prepare=lambda *_: None)
        assert result['status'] == 'failed'
        assert result['results'][0]['status'] == 'succeeded'
        assert result['results'][0]['financial_status'] == 'failed'
        assert store.read(conn, 'obex') is not None
        assert conn.execute('SELECT status FROM revenue_attempts').fetchone()[0] == 'succeeded'
        assert conn.execute('SELECT status,error_type FROM reporting_financial_attempts').fetchone() == ('failed', 'ValueError')


def test_http_financial_reads_use_saved_rows_and_keep_raw_outputs(database, monkeypatch):  # noqa: F811
    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool

    from settle.api.app import PostgresReader, create_app
    def forbidden(*args, **kwargs):
        raise AssertionError('Financial reads must not call providers')
    monkeypatch.setattr('requests.Session.send', forbidden)
    base = example()
    pnl = FinancialPnl(base.prime_id, base.period, base.pin_blocks_som)
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        ref = store.publish(conn, pnl, store.Versions('code', 'cfg', '0'))
        assert financial_store.publish_revision(conn, 'obex', ref) == 'succeeded'
    with ConnectionPool(database, min_size=1, max_size=2) as pool:
        with TestClient(create_app(reader=PostgresReader(pool, {}, [], 0))) as client:
            path = f'/v3/reporting/portfolio?prime=obex&month={pnl.as_of:%Y-%m}&cutoff={pnl.as_of}&source_revision={ref}'
            response = client.get(path)
            assert response.status_code == 200, response.text
            data = response.json()
            assert data['portfolio']['closing_value']['value'] == '1100'
            assert data['source']['revision_id'] == ref
            assert data['source_lag'] is False
            assert client.get(path, headers={'If-None-Match': response.headers['etag']}).status_code == 304
            annual = client.get(f'/v3/reporting/statements?prime=obex&year={pnl.as_of.year}').json()
            pinned = client.get('/v3/reporting/statements', params={'prime': 'obex', 'year': pnl.as_of.year, 'revision': annual['selection']}).json()
            assert pinned == annual
            assert client.get(path.replace('prime=obex', 'prime=grove')).status_code == 404
            assert client.get('/v1/revenue/obex/latest').json()['data']['revision_id'] == ref


def test_policy_upgrade_keeps_v1_annual_and_portfolio_selections_readable(database):  # noqa: F811
    from unittest.mock import patch

    from fastapi.testclient import TestClient
    from psycopg_pool import ConnectionPool

    from settle.api.app import PostgresReader, create_app
    from settle.revenue.financial import FIRST_SLICE_POLICY, build
    base = example()
    pnl = FinancialPnl(base.prime_id, base.period, base.pin_blocks_som)
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        source_ref = store.publish(conn, pnl, store.Versions('code', 'cfg', '0'))
        with patch.object(financial_store, 'POLICY_VERSION', FIRST_SLICE_POLICY), patch.object(
                financial_store, 'build', lambda source: build(source, policy_version=FIRST_SLICE_POLICY)):
            assert financial_store.publish_revision(conn, 'obex', source_ref) == 'succeeded'
        old = conn.execute('SELECT revision_id FROM reporting_financial_results').fetchone()[0]
        rows = financial_store.year_rows(conn, 'obex', pnl.as_of.year, policy_version=FIRST_SLICE_POLICY)
        manifest = {'prime': 'obex', 'year': pnl.as_of.year, 'as_of': str(pnl.as_of), 'exact': False,
                    'policy_version': FIRST_SLICE_POLICY, 'revisions': [None] * 12}
        manifest['revisions'][pnl.as_of.month - 1] = old
        original = statements('obex', pnl.as_of.year, pnl.as_of, rows, selection=manifest)
        assert financial_store.publish_revision(conn, 'obex', source_ref) == 'succeeded'
        assert financial_store.read(conn, 'obex')['revision_id'] != old
        assert financial_store.read(conn, 'obex', revision=old)['document']['policy_version'] == FIRST_SLICE_POLICY
        assert financial_backfill.run(conn, 'obex', pnl.as_of, pnl.as_of)['reused'] == 1
    with ConnectionPool(database, min_size=1, max_size=2) as pool:
        with TestClient(create_app(reader=PostgresReader(pool, {}, [], 0))) as client:
            selected = client.get('/v3/reporting/statements', params={'prime': 'obex', 'year': pnl.as_of.year, 'revision': original['selection']})
            assert selected.status_code == 200, selected.text
            assert selected.json()['report_hash'] == original['report_hash']
            assert selected.json()['lines'] == original['lines']
            portfolio = client.get('/v3/reporting/portfolio', params={'prime': 'obex', 'month': pnl.as_of.strftime('%Y-%m'), 'revision': old})
            assert portfolio.status_code == 200, portfolio.text
            assert portfolio.json()['policy_version'] == FIRST_SLICE_POLICY
