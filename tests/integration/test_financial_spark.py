from dataclasses import dataclass, field, replace

import psycopg
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from settle.api.app import PostgresReader, create_app
from settle.revenue import financial_store, store
from settle.revenue.financial import SPARK_INCOME_POLICY
from tests.integration.test_financial_reporting import FinancialPnl
from tests.integration.test_input_cache_postgres import database  # noqa: F401
from tests.integration.test_revenue_store import example


@dataclass
class SparkPnl(FinancialPnl):
    reporting_only_breakdown: list = field(default_factory=list)
    venue_views: dict = field(default_factory=dict)


def test_report_only_reserve_receipts_are_read_from_exact_pinned_source(database, monkeypatch):  # noqa: F811
    def forbidden(*args, **kwargs):
        raise AssertionError('Reporting reads cannot invoke providers')
    monkeypatch.setattr('requests.Session.send', forbidden)
    base = example()
    rows = [dict(venue_id=f'S{i}', pricing_category='C', sd_share='0', external_revenue='2',
                 revenue='12', value_som='100', value_eom='110') for i in range(1, 6)]
    extras = [dict(venue_id=v, pricing_category='C', sd_share='0', external_revenue='3', revenue='99999')
              for v in ('S70', 'S71')]
    views = {'reporting': [dict(venue_id=v, enabled=True, status='available') for v in ('S70', 'S71')]}
    pnl = SparkPnl('spark', base.period, base.pin_blocks_som, venue_breakdown=rows,
                   reporting_only_breakdown=extras, venue_views=views)
    versions = store.Versions('spark-fees-test',
        'da77310707751cae619941f4a35e937d8f880167b5af57a58ae5db20376c7067', '0')
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        original = store.publish(conn, pnl, versions)
        assert financial_store.publish_revision(conn, 'spark', original) == 'succeeded'
        selected = financial_store.year_rows(conn, 'spark', pnl.as_of.year, include_spark_fees=True)
        assert len(selected[0]['spark_fee_source_rows']) == 2
        assert 'spark_fee_source_rows' not in financial_store.year_rows(conn, 'spark', pnl.as_of.year)[0]
    with ConnectionPool(database, min_size=1, max_size=2) as pool:
        with TestClient(create_app(reader=PostgresReader(pool, {}, [], 0))) as client:
            query = {'prime': 'spark', 'year': pnl.as_of.year}
            response = client.get('/v3/reporting/statements', params=query)
            assert response.status_code == 200, response.text
            annual = response.json()
            assert annual['policy_version'] == SPARK_INCOME_POLICY
            month = f'{pnl.as_of:%Y-%m}'

            def fees(report):
                return next(row for row in report['lines'] if row['id'] == 'fees.sparklend')['values'][month]

            assert fees(annual)['value'] == '16'
            assert fees(annual)['recognition'] == 'receipt_in_kind'
            with psycopg.connect(database, autocommit=True) as conn:
                newer = store.publish(conn, replace(pnl, venue_views={'reporting': [
                    dict(venue_id='S70', enabled=False, status='disabled'), views['reporting'][1]]}),
                    replace(versions, inputs='disabled'))
                assert financial_store.publish_revision(conn, 'spark', newer) == 'succeeded'
            current = client.get('/v3/reporting/statements', params=query).json()
            assert fees(current)['status'] == 'partial' and fees(current)['known_value'] == '13'
            pinned = client.get('/v3/reporting/statements', params={**query, 'revision': annual['selection']}).json()
            assert pinned['report_hash'] == annual['report_hash'] and fees(pinned)['value'] == '16'
