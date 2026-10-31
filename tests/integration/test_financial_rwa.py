from dataclasses import dataclass, field, replace

import psycopg
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from settle.api.app import PostgresReader, create_app
from settle.revenue import financial_store, store
from settle.revenue.financial import OFFCHAIN_POLICY
from settle.revenue.financial_rwa import SPECS
from tests.integration.test_financial_reporting import FinancialPnl
from tests.integration.test_input_cache_postgres import database  # noqa: F401
from tests.integration.test_revenue_store import example


@dataclass
class RwaPnl(FinancialPnl):
    venue_views: dict = field(default_factory=dict)


def test_api_reads_only_reviewed_rows_from_the_pinned_raw_revision(database, monkeypatch):  # noqa: F811
    def forbidden(*args, **kwargs):
        raise AssertionError('Reporting reads cannot invoke providers')
    monkeypatch.setattr('requests.Session.send', forbidden)
    rows = [dict(venue_id=venue, label=label, pricing_category='E', sd_share='0.3' if venue == 'E7' else '0',
        actual_revenue='10' if venue == 'E7' else '20', revenue='7' if venue == 'E7' else '20',
        sd_revenue='3' if venue == 'E7' else '0', external_revenue='0',
        value_som='1000', value_eom='1010') for venue, _, _, label in SPECS]
    rows.append(dict(venue_id='E10', revenue='999999'))
    base = example()
    pnl = RwaPnl('grove', base.period, base.pin_blocks_som, venue_breakdown=rows)
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        versions = store.Versions('rwa-test', 'rwa-config', '0')
        original = store.publish(conn, pnl, versions)
        assert financial_store.publish_revision(conn, 'grove', original) == 'succeeded'
        baseline = store.read(conn, 'grove')
        selected = financial_store.year_rows(conn, 'grove', pnl.as_of.year, include_rwa=True)
        assert len(selected[0]['rwa_source_rows']) == 2
        assert selected[0]['rwa_source_hash'] == baseline['result_hash']
        assert 'rwa_source_rows' not in financial_store.year_rows(conn, 'grove', pnl.as_of.year)[0]
    with ConnectionPool(database, min_size=1, max_size=2) as pool:
        with TestClient(create_app(reader=PostgresReader(pool, {}, [], 0))) as client:
            query = {'prime': 'grove', 'year': pnl.as_of.year}
            response = client.get('/v3/reporting/statements', params=query)
            assert response.status_code == 200, response.text
            annual = response.json()
            assert annual['policy_version'] == OFFCHAIN_POLICY
            def amount(document, key):
                return next(row for row in document['lines'] if row['id'] == key)['values'][f'{pnl.as_of:%Y-%m}']['value']
            assert amount(annual, 'investment.rwa.stac') == '7'
            assert amount(annual, 'investment.rwa.jaaa') == '20'
            assert len(annual['offchain_positions']) == 2
            assert next(line for line in annual['lines'] if line['id'] == 'investment.offchain.gaclo')['values']['2026-01']['value'] == '402939.81094444456'
            assert len(annual['selection']) <= 4096
            with psycopg.connect(database, autocommit=True) as conn:
                assert store.read(conn, 'grove') == baseline  # Read-only projection.
                corrected = replace(pnl, venue_views={'reporting': [dict(venue_id='E7', enabled=False, status='disabled')]})
                newer = store.publish(conn, corrected, replace(versions, inputs='disabled'))
                assert financial_store.publish_revision(conn, 'grove', newer) == 'succeeded'
            current = client.get('/v3/reporting/statements', params=query).json()
            assert amount(current, 'investment.rwa.stac') is None
            pinned = client.get('/v3/reporting/statements', params={**query, 'revision': annual['selection']}).json()
            assert pinned['report_hash'] == annual['report_hash']
            assert amount(pinned, 'investment.rwa.stac') == '7'
