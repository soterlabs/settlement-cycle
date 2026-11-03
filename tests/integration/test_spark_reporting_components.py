from datetime import date

import psycopg
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from settle.api.app import PostgresReader, create_app
from settle.revenue import spark_reporting_store, store
from tests.integration.test_input_cache_postgres import database  # noqa: F401
from tests.unit.test_spark_onchain import supplement


def test_independent_reporting_history_and_endpoints_never_write_settlement_rows(database, monkeypatch):  # noqa: F811
    from settle.api import revenue
    monkeypatch.setattr(revenue, 'today', lambda: date(2026, 10, 27))
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        original = supplement()['document']
        first = spark_reporting_store.publish(conn, original, code_version='code', configuration_version='config')
        assert spark_reporting_store.publish(conn, original, code_version='code', configuration_version='config') == first
        assert conn.execute('SELECT count(*) FROM reporting_spark_components').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM revenue_results').fetchone()[0] == 0
    with ConnectionPool(database, min_size=1, max_size=2) as pool:
        with TestClient(create_app(reader=PostgresReader(pool, {}, [], 0))) as client:
            response = client.get('/v3/reporting/fees?prime=spark&month=2026-10')
            assert response.status_code == 200, response.text
            assert response.json()['metrics']['fees.curator']['value'] == '12'
            annual = client.get('/v3/reporting/statements?prime=spark&year=2026').json()
            with psycopg.connect(database, autocommit=True) as conn:
                changed = supplement(fees='5')['document']
                spark_reporting_store.publish(conn, changed, code_version='code', configuration_version='config')
            latest = client.get('/v3/reporting/fees?prime=spark&month=2026-10').json()
            assert latest['metrics']['fees.curator']['value'] == '30'
            retained = client.get('/v3/reporting/fees', params={
                'prime': 'spark', 'month': '2026-10', 'revision': annual['selection']}).json()
            assert retained['metrics']['fees.curator']['value'] == '12'
            native = client.get('/v3/reporting/allocations?prime=spark&month=2026-10&venue=S14').json()
            assert native['allocations'][0]['native_revenue_usd']['value'] == '10'
            assert native['allocations'][0]['native_revenue_usd']['quality'] == 'estimate'
    with psycopg.connect(database, autocommit=True) as conn:
        assert conn.execute('SELECT count(*) FROM revenue_results').fetchone()[0] == 0
