"""Real PostgreSQL: atomic monthly revisions, isolation and read-only HTTP."""

import copy

import psycopg
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from settle.api.app import PostgresReader, create_app
from settle.revenue import allocations
from tests.integration.test_input_cache_postgres import database  # noqa: F401
from tests.unit.test_monthly_allocation_api import documents


def test_monthly_publication_revisions_isolation_and_http(database, monkeypatch):  # noqa: F811
    def forbidden(*args, **kwargs):
        raise AssertionError("Monthly API must not call upstream providers")

    monkeypatch.setattr("requests.Session.send", forbidden)
    docs = documents()
    with psycopg.connect(database, autocommit=True) as conn:
        allocations.apply_schema(conn)
        with conn.transaction():
            revisions = {d["prime"]: allocations.publish(conn, d) for d in docs}
        # Idempotent retries do not create a new latest revision.
        assert allocations.publish(conn, docs[0]) == revisions[docs[0]["prime"]]
        assert conn.execute("SELECT COUNT(*) FROM allocation_monthly_results").fetchone()[0] == 2
        changed = copy.deepcopy(docs[0])
        changed["funding_policy"] += "; revised operator note"
        with conn.transaction():
            revision2 = allocations.publish(conn, changed)
        assert revision2 != revisions[changed["prime"]]
        assert allocations.read(conn, changed["prime"], "2026-08")["revision_id"] == revision2
        allocations.publish(conn, docs[0])
        assert allocations.read(conn, changed["prime"], "2026-08")["revision_id"] == revision2
        assert allocations.read(conn, "spark", "2026-07") is None
        assert allocations.read(conn, "spark", "2026-08", revision=revisions["grove"]) is None
    with ConnectionPool(database, min_size=1, max_size=2) as pool:
        client = TestClient(create_app(PostgresReader(pool, {}, [], 0)))
        response = client.get("/v1/revenue/spark/monthly/2026-08/allocations?venue=S1")
        assert response.status_code == 200
        row = response.json()["allocations"][0]
        assert row["borrowing_cost_usd"] is None and row["net_apy"] is None
        old = client.get(
            "/v1/revenue/grove/monthly/2026-08/allocations?revision=" + revisions["grove"]
        )
        assert old.status_code == 200 and old.json()["revision_id"] == revisions["grove"]
        full = client.get("/v1/revenue/grove/monthly/2026-08/allocations")
        assert full.status_code == 200
        assert (
            client.get(
                "/v1/revenue/grove/monthly/2026-08/allocations",
                headers={"If-None-Match": full.headers["etag"]},
            ).status_code
            == 304
        )


def test_failed_publication_transaction_keeps_previous_snapshot(database):  # noqa: F811
    doc = documents()[0]
    with psycopg.connect(database, autocommit=True) as conn:
        allocations.apply_schema(conn)
        original = allocations.publish(conn, doc)
        try:
            with conn.transaction():
                replacement = {**doc, "funding_policy": "replacement"}
                allocations.publish(conn, replacement)
                raise RuntimeError("interrupted import")
        except RuntimeError:
            pass
        assert allocations.read(conn, doc["prime"], "2026-08")["revision_id"] == original
