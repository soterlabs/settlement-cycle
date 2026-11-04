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


def test_savings_expense_is_published_once_and_pins_replay(database, monkeypatch):  # noqa: F811
    monkeypatch.setattr("settle.api.revenue.today", lambda: date(2026, 11, 2))

    def forbidden(*args, **kwargs):
        raise AssertionError("Reporting reads cannot invoke providers")

    monkeypatch.setattr("requests.Session.send", forbidden)
    original = supplement("2026-10-31")["document"]
    original["savings_interest"] = {
        v: {
            "venue": v,
            "asset": "USDC",
            "status": "available",
            "value": "3",
            "interest_units": "3",
            "opening_claim_units": "100",
            "closing_claim_units": "203",
            "deposits_units": "100",
            "withdrawals_units": "0",
            "share_rounding_units": "0",
            "recognition": "accrual",
            "quality": "supported",
        }
        for v in ("S56", "S57", "S58", "S59", "S60", "S63")
    }
    assets = dict(
        zip(
            ("S56", "S57", "S58", "S59", "S60", "S63"),
            ("USDC", "USDT", "WETH", "PYUSD", "USDC", "USDG"),
            strict=True,
        )
    )
    for v, row in original["savings_interest"].items():
        row["asset"] = assets[v]
    original["savings_interest"]["S58"]["daily_translation"] = [
        {"date": f"2026-10-{i:02}", "interest_units": "3" if i == 31 else "0", "usd_price": "1"}
        for i in range(1, 32)
    ]
    original["savings_interest"]["S58"]["quality"] = "estimate"
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        spark_reporting_store.publish(
            conn, original, code_version="test", configuration_version="test"
        )
    with ConnectionPool(database, min_size=1, max_size=2) as pool:
        with TestClient(create_app(reader=PostgresReader(pool, {}, [], 0))) as client:
            query = {"prime": "spark", "month": "2026-10"}
            r = client.get("/v3/reporting/savings", params=query)
            assert r.status_code == 200, r.text
            doc = r.json()
            assert doc["metrics"]["funding.savings"]["value"] == "18"
            assert not doc["principal_included_in_expense"]
            annual = client.get(
                "/v3/reporting/statements?prime=spark&year=2026&cutoff=2026-10-31"
            ).json()
            rows = {r["id"]: r for r in annual["lines"]}
            assert rows["statement.funding"]["values"]["2026-10"]["known_value"] == "18"
            assert rows["demand.distribution_rewards"]["values"]["2026-10"]["value"] is None
            assert all(
                r["metric_id"] in rows for r in annual["statement_rows"] if r["kind"] != "section"
            )
            pinned = client.get(
                "/v3/reporting/savings", params={**query, "revision": doc["selection"]}
            ).json()
            assert pinned["report_hash"] == doc["report_hash"]
    with psycopg.connect(database, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM revenue_results").fetchone()[0] == 0
