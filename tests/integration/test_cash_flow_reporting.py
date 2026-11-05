"""Real Postgres publication/replay with no provider calls or settlement writes."""

from copy import deepcopy
from datetime import date

import psycopg
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from settle.api.app import PostgresReader, create_app
from settle.revenue import store as revenue_store
from settle.revenue.cash_flow import store
from settle.revenue.verification import digest
from tests.integration.test_input_cache_postgres import database  # noqa: F401
from tests.unit.test_cash_flow import document


def test_cash_flow_publications_are_immutable_scoped_and_reporting_only(database, monkeypatch):  # noqa: F811
    monkeypatch.setattr("settle.api.revenue.today", lambda: date(2026, 11, 5))

    def forbidden(*args, **kwargs):
        raise AssertionError("An API read attempted an upstream request")

    monkeypatch.setattr("requests.Session.send", forbidden)
    events = [{"id": "ethereum:tx:1:cash", "line_id": "unclassified", "amount_usd": "5"}]
    original = document()
    original.update(cash_event_digest=digest(events), cash_event_count=1)
    with psycopg.connect(database, autocommit=True) as conn:
        revenue_store.apply_schema(conn)
        store.apply_schema(conn)
        first = store.publish(conn, original, events, "reviewed-code")
        assert store.publish(conn, original, events, "reviewed-code") == first
        assert (
            conn.execute("SELECT count(*) FROM reporting_cash_flow_publications").fetchone()[0] == 1
        )
        assert conn.execute("SELECT count(*) FROM revenue_results").fetchone()[0] == 0
    with ConnectionPool(database, min_size=1, max_size=2) as pool:
        with TestClient(create_app(reader=PostgresReader(pool, {}, [], 0))) as client:
            response = client.get("/v3/reporting/cash-flow?prime=spark&year=2026")
            assert response.status_code == 200, response.text
            selected = response.json()["selection"]
            with psycopg.connect(database, autocommit=True) as conn:
                changed = deepcopy(original)
                changed["coverage"]["classification_complete"] = False
                assert store.publish(conn, changed, events, "reviewed-code") != first
            pinned = client.get(
                "/v3/reporting/cash-flow",
                params={"prime": "spark", "year": 2026, "revision": selected},
            )
            assert pinned.json() == response.json()
            assert (
                client.get("/v3/reporting/cash-flow?prime=spark&year=2026").json()["selection"]
                != selected
            )
            params = {
                "prime": "spark",
                "month": "2026-10",
                "revision": first,
                "line": "unclassified",
                "limit": 1,
            }
            evidence = client.get("/v3/reporting/cash-flow/events", params=params)
            assert evidence.status_code == 200, evidence.text
            assert evidence.json()["events"] == events
            assert evidence.json()["next_offset"] is None
            params["offset"] = 1
            assert (
                client.get("/v3/reporting/cash-flow/events", params=params).json()["events"] == []
            )
            params["month"] = "2026-09"
            assert client.get("/v3/reporting/cash-flow/events", params=params).status_code == 404
    with psycopg.connect(database, autocommit=True) as conn:
        assert conn.execute("SELECT count(*) FROM revenue_results").fetchone()[0] == 0


def test_month_projection_reads_one_eligible_revision_per_month(database):  # noqa: F811
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        ids = {}
        for day in ["2026-10-31", "2026-11-01", "2026-11-03"]:
            ids[day] = store.publish(conn, document(day), [], "code")
        historical = store.year_rows(conn, "spark", 2026, as_of=date(2026, 11, 2))
        assert {r["cutoff"] for r in historical} == {"2026-10-31", "2026-11-01"}
        latest = store.year_rows(conn, "spark", 2026, as_of=date(2026, 11, 4))
        assert {r["cutoff"] for r in latest} == {"2026-10-31", "2026-11-03"}
        pinned = store.year_rows(conn, "spark", 2026, [ids["2026-11-01"]], as_of=date(2026, 11, 4))
        assert [r["cutoff"] for r in pinned] == ["2026-11-01"]
