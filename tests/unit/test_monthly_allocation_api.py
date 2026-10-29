"""Monthly publication/HTTP contract, including unknown funding and small losses."""

import copy
import gzip
import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from settle.api.app import create_app
from settle.revenue.allocations import build_documents, month_bounds


def inputs():
    root = Path(__file__).resolve().parents[2]
    readiness = json.loads(
        gzip.decompress(
            (root / "reconciliation/allocation_capital_readiness_2026_08.json.gz").read_bytes()
        )
    )
    yields = json.loads((root / "reconciliation/allocation_yields_2026_08.json").read_text())
    return readiness, yields


def documents():
    return build_documents(
        *inputs(), source_hashes={"capital_artifact": "a" * 64, "yield_artifact": "b" * 64}
    )


class Reader:
    def __init__(self):
        self.documents = {d["prime"]: {**d, "revision_id": "c" * 64} for d in documents()}
        self.calls = []

    def monthly_allocations(self, prime, month, *, revision=None):
        self.calls.append((prime, month, revision))
        if month != "2026-08" or revision not in (None, "c" * 64):
            return None
        return copy.deepcopy(self.documents.get(prime))


def test_idle_non_usds_cost_is_positive_and_net_yield_negative_not_zero():
    grove = next(d for d in documents() if d["prime"] == "grove")
    row = next(r for r in grove["allocations"] if r["venue_id"] == "E15")
    assert Decimal(row["borrowing_cost_usd"]) > 0
    assert Decimal(row["net_monthly_return"]) < 0
    assert Decimal(row["net_apy"]) < 0
    assert row["net_apy"] != "-0.00"


def test_monthly_amounts_and_annualized_yields_are_distinct_and_lossless():
    reader = Reader()
    response = TestClient(create_app(reader)).get(
        "/v1/revenue/spark/monthly/2026-08/allocations?venue=S18"
    )
    assert response.status_code == 200
    doc = response.json()
    assert doc["provisional"] and doc["cadence"] == "monthly"
    assert doc["count"] == 1
    row = doc["allocations"][0]
    assert Decimal(row["gross_monthly_return"]) != Decimal(row["gross_apy"])
    assert Decimal(row["gross_monthly_return"]) == Decimal(row["gross_revenue_usd"]) / Decimal(
        row["average_yield_exposure_usd"]
    )
    assert isinstance(row["borrowing_cost_usd"], str)
    assert len(row["borrowing_cost_usd"]) > 12
    assert (
        TestClient(create_app(reader))
        .get("/v1/revenue/grove/monthly/2026-08/allocations")
        .json()["count"]
        > 1
    )


def test_savings_null_cost_and_net_are_not_replaced_by_zero():
    response = TestClient(create_app(Reader())).get(
        "/v1/revenue/spark/monthly/2026-08/allocations?venue=S1"
    )
    row = response.json()["allocations"][0]
    assert row["borrowing_cost_usd"] is None
    assert row["net_monthly_return"] is None and row["net_apy"] is None
    assert row["borrowing_cost_status"] == "savings_split_pending"
    assert row["gross_apy"] is not None


def test_invalid_month_prime_revision_and_venue():
    r = Reader()
    client = TestClient(create_app(r))
    for path in ["2026-8", "2026-13", "0000-01", "9999-12"]:
        assert client.get(f"/v1/revenue/grove/monthly/{path}/allocations").status_code == 422
    assert client.get("/v1/revenue/unknown/monthly/2026-08/allocations").status_code == 404
    assert (
        client.get("/v1/revenue/grove/monthly/2026-08/allocations?revision=bad").status_code == 422
    )
    assert not r.calls
    assert (
        client.get("/v1/revenue/grove/monthly/2026-08/allocations?venue=missing").status_code == 404
    )
    missing = client.get("/v1/revenue/grove/monthly/2026-07/allocations")
    assert missing.status_code == 404 and missing.headers["cache-control"] == "no-store"
    assert (
        client.get("/v1/revenue/grove/monthly/2026-08/allocations?revision=" + "d" * 64).status_code
        == 404
    )


def test_etag_covers_selected_month_revision_and_rows():
    r = Reader()
    c = TestClient(create_app(r))
    path = "/v1/revenue/grove/monthly/2026-08/allocations"
    first = c.get(path)
    assert first.headers["cache-control"] == "public, max-age=300"
    assert c.get(path, headers={"If-None-Match": first.headers["etag"]}).status_code == 304
    r.documents["grove"]["revision_id"] = "d" * 64
    assert c.get(path, headers={"If-None-Match": first.headers["etag"]}).status_code == 200


def test_failure_is_503_without_exposing_connection_information():
    r = Reader()

    def fail(*args, **kwargs):
        raise RuntimeError("private database credentials")

    r.monthly_allocations = fail
    response = TestClient(create_app(r)).get("/v1/revenue/grove/monthly/2026-08/allocations")
    assert response.status_code == 503
    assert "private" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_publication_rejects_mixed_snapshot_missing_venues_and_nonfinite_values():
    readiness, yields = inputs()
    hashes = {"artifact": "a" * 64}
    yields["results"][0]["allocations"][0]["cost_of_funds"] = "99"
    with pytest.raises(ValueError, match="different borrowing"):
        build_documents(readiness, yields, source_hashes=hashes)
    readiness, yields = inputs()
    yields["results"][0]["allocations"].pop()
    with pytest.raises(ValueError, match="coverage differs"):
        build_documents(readiness, yields, source_hashes=hashes)
    readiness, yields = inputs()
    yields["results"][0]["allocations"][0]["gross_apy"] = "NaN"
    with pytest.raises(ValueError, match="Invalid allocation decimal"):
        build_documents(readiness, yields, source_hashes=hashes)


def test_duplicate_venues_and_partial_month_rejected():
    readiness, yields = inputs()
    readiness["results"][0]["allocations"].append(readiness["results"][0]["allocations"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        build_documents(readiness, yields, source_hashes={"artifact": "a" * 64})
    readiness, yields = inputs()
    readiness["results"][0]["end"] = "2026-08-30"
    with pytest.raises(ValueError, match="complete calendar month"):
        build_documents(readiness, yields, source_hashes={"artifact": "a" * 64})
    assert str(month_bounds("2028-02")[1]) == "2028-02-29"


def test_inconsistent_yield_rejected_even_if_cost_snapshot_matches():
    readiness, yields = inputs()
    row = next(r for p in yields["results"] for r in p["allocations"] if r["venue"] == "S18")
    row["gross_apy"] = "0.9"
    with pytest.raises(ValueError, match="revenue and exposure"):
        build_documents(readiness, yields, source_hashes={"artifact": "a" * 64})
