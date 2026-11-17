import gzip
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from settle.revenue import anchorage_principal as ap

FIXTURE = Path(__file__).parents[1] / "fixtures/spark_anchorage_loan_routes.json.gz"


def events():
    with gzip.open(FIXTURE, "rt") as f:
        return json.load(f)


def compact(rows):
    return [
        dict(
            block=r["block"],
            log_index=r["log_index"],
            tx_hash=r["tx_hash"],
            sender="0x" + r["topic1"][-40:],
            recipient="0x" + r["topic2"][-40:],
            units=str(int(r["data"], 16)),
        )
        for r in rows
    ]


@pytest.mark.parametrize(
    "month,expected",
    [
        (1, 150000000),
        (2, 150000000),
        (3, 150000000),
        (4, 150000000),
        (5, 250000000),
        (6, 260000000),
        (7, 260000000),
        (8, 210000000),
        (9, 210000000),
        (10, 210000000),
    ],
)
def test_historical_principal(month, expected):
    rows = [
        r
        for r in events()["events"]
        if datetime.fromtimestamp(r["timestamp"], UTC) < datetime(2026, month + 1, 1, tzinfo=UTC)
    ]
    block = rows[-1]["block"]
    proof = ap.reconstruct(compact(rows), block)
    assert proof["value"] == str(expected)
    assert proof["quality"] == "estimate"


def test_exact_returns_and_routes():
    proof = ap.reconstruct(compact(events()["events"]), events()["pin"]["block"])
    assert sorted(proof["route_principal_units"].values()) == ["0", "210000000000000"]
    assert ap.reconstruct([], 0)["value"] == "0"
    # Loan funding ledger does not count a duplicate ALM advance or expense payments.
    assert len(proof["events"]) == 26


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "amount", "future"])
def test_unknown_or_changed_events_fail_closed(mutation):
    rows = compact(events()["events"])
    if mutation == "missing":
        rows.pop()
    elif mutation == "duplicate":
        rows.append(deepcopy(rows[-1]))
    elif mutation == "amount":
        rows[-1]["units"] = "1"
    else:
        row = deepcopy(rows[-1])
        row["tx_hash"] = "0x" + "f" * 64
        rows.append(row)
    with pytest.raises(ValueError, match="unreviewed"):
        ap.reconstruct(rows, events()["pin"]["block"])


def test_capture_and_tampered_claim(monkeypatch):
    raw = events()

    def query(chain, filters, start, end, **kwargs):
        assert chain == "ethereum" and start == 0 and end == raw["pin"]["block"]
        assert all(f["address"] == [ap.TOKEN] for f in filters)
        return SimpleNamespace(
            rows=[
                SimpleNamespace(
                    address=r["address"],
                    topic0=r["topic0"],
                    topic1=r["topic1"],
                    topic2=r["topic2"],
                    data=r["data"],
                    block_number=r["block"],
                    log_index=r["log_index"],
                    transaction_hash=r["tx_hash"],
                )
                for r in raw["events"]
            ]
        )

    monkeypatch.setattr("settle.extract.hypersync.query_logs", query)
    proof = ap.collect(raw["pin"]["block"])
    doc = dict(
        anchorage_principal=proof,
        pins={"ethereum": raw["pin"]},
        positions={"S23": dict(status="available", value="210000000", quality="estimate")},
    )
    ap.validate(doc)
    doc["positions"]["S23"]["value"] = "211000000"
    with pytest.raises(ValueError, match="position differs"):
        ap.validate(doc)
    doc["positions"]["S23"]["value"] = "210000000"
    doc["anchorage_principal"]["assumptions"] = []
    with pytest.raises(ValueError, match="proof differs"):
        ap.validate(doc)
