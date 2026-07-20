"""Unit tests for the Envio ``IDebtSource`` and the comparison harness merge.

No network: a fake ``post`` returns canned paginated GraphQL responses, so we
assert the source produces the exact ``debt_timeseries.sql`` contract
(normalised Art in wad, daily-aggregated, cumulative) and that its filters are
wired correctly.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from settle.normalize.sources.envio_debt import EnvioDebtSource, EnvioError

_WAD = 10**18


def _ts(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, 12, 0, tzinfo=timezone.utc).timestamp())


class _FakeResp:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class _FakePost:
    """Records calls and replays a queue of GraphQL pages (list of row-lists)."""

    def __init__(self, pages: list[list[dict]], entity: str = "VatDebtEvent"):
        self._pages = list(pages)
        self._entity = entity
        self.calls: list[dict] = []

    def __call__(self, url, json, headers, timeout):  # mirrors requests.post kwargs
        self.calls.append({"url": url, "json": json, "headers": headers})
        rows = self._pages.pop(0) if self._pages else []
        return _FakeResp({"data": {self._entity: rows}})


@pytest.fixture(autouse=True)
def _envio_env(monkeypatch):
    monkeypatch.setenv("ENVIO_GRAPHQL_URL", "http://localhost:8080/v1/graphql")
    monkeypatch.delenv("ENVIO_GRAPHQL_TOKEN", raising=False)
    monkeypatch.delenv("ENVIO_HASURA_ADMIN_SECRET", raising=False)
    monkeypatch.delenv("ENVIO_DEBT_ENTITY", raising=False)


def test_missing_url_raises(monkeypatch):
    monkeypatch.delenv("ENVIO_GRAPHQL_URL", raising=False)
    with pytest.raises(EnvioError, match="ENVIO_GRAPHQL_URL"):
        EnvioDebtSource(post=_FakePost([[]])).debt_timeseries(b"\x00" * 32, date(2025, 11, 18), 100)


def test_daily_aggregation_and_cumsum():
    # Two events same day, one the next day, incl. a negative (repay) dart.
    rows = [
        {"dart": str(50_000_000 * _WAD), "blockNumber": 10, "blockTimestamp": _ts(2025, 11, 18)},
        {"dart": str(10_000_000 * _WAD), "blockNumber": 11, "blockTimestamp": _ts(2025, 11, 18)},
        {"dart": str(-5_000_000 * _WAD), "blockNumber": 20, "blockTimestamp": _ts(2025, 11, 19)},
    ]
    src = EnvioDebtSource(post=_FakePost([rows]))
    df = src.debt_timeseries(b"\xaa" * 32, date(2025, 11, 1), 24_971_074)

    assert list(df.columns) == ["block_date", "daily_dart", "cum_debt"]
    assert df["block_date"].tolist() == [date(2025, 11, 18), date(2025, 11, 19)]
    # Day 1: 50M + 10M = 60M; Day 2: -5M
    assert df["daily_dart"].tolist() == [Decimal("60000000"), Decimal("-5000000")]
    # Cumulative: 60M, then 55M
    assert df["cum_debt"].tolist() == [Decimal("60000000"), Decimal("55000000")]
    # Decimals carried, not floats.
    assert all(isinstance(v, Decimal) for v in df["cum_debt"])


def test_filters_and_ilk_encoding():
    ilk = bytes.fromhex("414c4c4f4341544f522d535041524b2d41".ljust(64, "0"))
    fake = _FakePost([[]])
    EnvioDebtSource(post=fake).debt_timeseries(ilk, date(2026, 1, 1), 25_000_000)

    vars_ = fake.calls[0]["json"]["variables"]
    assert vars_["ilk"] == "0x" + ilk.hex()               # 0x-prefixed lower-case hex
    assert vars_["pin"] == 25_000_000                     # block_number <= pin
    assert vars_["startTs"] == int(
        datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    )                                                     # start-of-day UTC


def test_pagination_walks_all_pages():
    page1 = [
        {"dart": str(_WAD), "blockNumber": i, "blockTimestamp": _ts(2025, 11, 18)}
        for i in range(1000)  # exactly _PAGE_SIZE → triggers a second fetch
    ]
    page2 = [{"dart": str(_WAD), "blockNumber": 2000, "blockTimestamp": _ts(2025, 11, 18)}]
    fake = _FakePost([page1, page2])
    df = EnvioDebtSource(post=fake).debt_timeseries(b"\x01" * 32, date(2025, 11, 1), 9_999_999)

    assert len(fake.calls) == 2
    assert fake.calls[1]["json"]["variables"]["offset"] == 1000
    # 1001 events, each 1 wad, summed to 1001 wad / 1e18 on a single day.
    assert df["cum_debt"].iloc[-1] == Decimal("1001")


def test_empty_returns_typed_empty_frame():
    df = EnvioDebtSource(post=_FakePost([[]])).debt_timeseries(b"\x02" * 32, date(2025, 1, 1), 1)
    assert df.empty
    assert list(df.columns) == ["block_date", "daily_dart", "cum_debt"]


def test_matches_dune_shaped_frame():
    """The whole point: Envio output is comparable row-for-row with a
    Dune-shaped frame (what scripts/compare_debt_sources.py diffs)."""
    rows = [
        {"dart": str(50_000_000 * _WAD), "blockNumber": 10, "blockTimestamp": _ts(2025, 11, 18)},
        {"dart": str(30_000_000 * _WAD), "blockNumber": 30, "blockTimestamp": _ts(2025, 12, 1)},
    ]
    envio = EnvioDebtSource(post=_FakePost([rows])).debt_timeseries(b"\xaa" * 32, date(2025, 11, 1), 10**9)

    dune_like = pd.DataFrame({
        "block_date": [date(2025, 11, 18), date(2025, 12, 1)],
        "daily_dart": [Decimal("50000000"), Decimal("30000000")],
        "cum_debt": [Decimal("50000000"), Decimal("80000000")],
    })
    pd.testing.assert_frame_equal(
        envio.reset_index(drop=True), dune_like, check_dtype=False
    )
