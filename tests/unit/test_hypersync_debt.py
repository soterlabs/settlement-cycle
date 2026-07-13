"""Unit tests for the HyperSync-direct ``IDebtSource`` (no network)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from settle.normalize.sources.hypersync_debt import (
    HyperSyncDebtSource,
    HyperSyncError,
    _decode_dart,
)

_WAD = 10**18
_ILK = bytes.fromhex("414c4c4f4341544f522d535041524b2d41".ljust(64, "0"))  # ALLOCATOR-SPARK-A


def _ts(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, 12, tzinfo=timezone.utc).timestamp())


def _raw_lognote_data(dart: int) -> str:
    """Build a raw LogNote `data` hex: ABI `bytes` (offset+length) wrapping the
    224-byte note payload (calldata[0:224]) with `dart` at payload offset 164."""
    sel = "76088703"
    ilk = "414c4c4f4341544f522d535041524b2d41".ljust(64, "0")
    filler = "00" * 32  # u
    dink = "00" * 32
    d = dart & ((1 << 256) - 1)  # two's-complement encode
    dart_word = format(d, "064x")
    # payload: sel(4) ilk(32) u(32) v(32) w(32) dink(32) dart(32) = 196 bytes
    payload = sel + ilk + filler + filler + filler + dink + dart_word
    payload = payload.ljust(224 * 2, "0")  # note copies 224 bytes, zero-padded
    offset_word = format(0x20, "064x")
    length_word = format(224, "064x")
    return "0x" + offset_word + length_word + payload


class _Resp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status
        self.ok = 200 <= status < 300
        self.text = json.dumps(payload)

    def json(self):
        return self._p


class _Post:
    """Replays a queue of HyperSync response pages; records request bodies."""

    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = []

    def __call__(self, url, json, headers, timeout):  # mirrors requests.post
        self.calls.append({"url": url, "body": json, "headers": headers})
        return _Resp(self._pages.pop(0) if self._pages else {"data": [], "next_block": None})


@pytest.fixture(autouse=True)
def _token_env(monkeypatch):
    monkeypatch.setenv("ENVIO_API_TOKEN", "test-token")
    monkeypatch.delenv("HYPERSYNC_URL", raising=False)
    monkeypatch.delenv("HYPERSYNC_START_BLOCK", raising=False)
    # The source routes through hypersync_store; an ambient DATABASE_URL
    # would make unit tests read/write a real Postgres. Force pass-through.
    monkeypatch.delenv("DATABASE_URL", raising=False)


# -- dart decode ----------------------------------------------------------

@pytest.mark.parametrize("dart", [50_000_000 * _WAD, -5_000_000 * _WAD, 0, 12345])
def test_decode_dart_from_raw_log_data(dart):
    assert _decode_dart(_raw_lognote_data(dart)) == Decimal(dart)


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("ENVIO_API_TOKEN", raising=False)
    with pytest.raises(HyperSyncError, match="ENVIO_API_TOKEN"):
        HyperSyncDebtSource(post=_Post([])).debt_timeseries(_ILK, date(2024, 1, 1), 100)


# -- query shape ----------------------------------------------------------

def test_query_filters_topic0_selectors_and_ilk():
    post = _Post([{"data": [], "next_block": None}])
    HyperSyncDebtSource(post=post).debt_timeseries(_ILK, date(2024, 11, 1), 25_000_000)

    body = post.calls[0]["body"]
    sel = body["logs"][0]
    assert sel["address"] == ["0x35d1b3f3d7966a1dfe207aa4514c12a259a0492b"]
    # topic0 = {frob, grab}; topic1 = ilk
    assert sel["topics"][0] == [
        "0x7608870300000000000000000000000000000000000000000000000000000000",
        "0x7bab3f4000000000000000000000000000000000000000000000000000000000",
    ]
    assert sel["topics"][1] == ["0x" + _ILK.hex()]
    assert body["to_block"] == 25_000_001            # pin_block + 1 (exclusive)
    assert post.calls[0]["headers"]["Authorization"] == "Bearer test-token"


# -- aggregation + pagination --------------------------------------------

def test_daily_aggregation_and_cumsum():
    page = {
        "data": [
            {
                "blocks": [
                    {"number": 10, "timestamp": _ts(2025, 11, 18)},
                    {"number": 20, "timestamp": _ts(2025, 11, 19)},
                ],
                "logs": [
                    {"block_number": 10, "data": _raw_lognote_data(50_000_000 * _WAD)},
                    {"block_number": 10, "data": _raw_lognote_data(10_000_000 * _WAD)},
                    {"block_number": 20, "data": _raw_lognote_data(-5_000_000 * _WAD)},
                ],
            }
        ],
        "next_block": 21,
    }
    df = HyperSyncDebtSource(post=_Post([page])).debt_timeseries(_ILK, date(2025, 11, 1), 20)

    assert list(df.columns) == ["block_date", "daily_dart", "cum_debt"]
    assert df["block_date"].tolist() == [date(2025, 11, 18), date(2025, 11, 19)]
    assert df["daily_dart"].tolist() == [Decimal("60000000"), Decimal("-5000000")]
    assert df["cum_debt"].tolist() == [Decimal("60000000"), Decimal("55000000")]
    assert all(isinstance(v, Decimal) for v in df["cum_debt"])


def test_pagination_follows_next_block_and_joins_timestamps():
    # hex-string numerics (as the real JSON API returns them) + two pages.
    p1 = {
        "data": [{"blocks": [{"number": "0x64", "timestamp": hex(_ts(2025, 12, 1))}],
                  "logs": [{"block_number": "0x64", "data": _raw_lognote_data(1_000_000 * _WAD)}]}],
        "next_block": "0x1f5",  # 501
    }
    p2 = {
        "data": [{"blocks": [{"number": 600, "timestamp": _ts(2025, 12, 2)}],
                  "logs": [{"block_number": 600, "data": _raw_lognote_data(2_000_000 * _WAD)}]}],
        "next_block": 601,
    }
    post = _Post([p1, p2])
    df = HyperSyncDebtSource(post=post).debt_timeseries(_ILK, date(2025, 12, 1), 600)

    assert len(post.calls) == 2
    assert post.calls[1]["body"]["from_block"] == 501     # resumed at next_block
    assert df["cum_debt"].tolist() == [Decimal("1000000"), Decimal("3000000")]
    assert df["block_date"].tolist() == [date(2025, 12, 1), date(2025, 12, 2)]


def test_empty_returns_typed_frame():
    df = HyperSyncDebtSource(post=_Post([{"data": [], "next_block": None}])).debt_timeseries(
        _ILK, date(2024, 1, 1), 100
    )
    assert df.empty
    assert list(df.columns) == ["block_date", "daily_dart", "cum_debt"]


def test_http_error_raises():
    class _ErrPost:
        def __call__(self, url, json, headers, timeout):
            return _Resp({"error": "unauthorized"}, status=401)

    with pytest.raises(HyperSyncError, match="401"):
        HyperSyncDebtSource(post=_ErrPost()).debt_timeseries(_ILK, date(2024, 1, 1), 100)


def test_start_date_filter_matches_dune_semantics():
    """Dune's SQL enforces ``block_date >= start_date`` per call; the
    HyperSync source must apply the same per-call filter (never a
    process-wide env knob) — otherwise two primes sharing the process
    silently under/over-count each other's history."""
    page = {
        "data": [
            {
                "blocks": [
                    {"number": 10, "timestamp": _ts(2025, 10, 30)},   # pre-start
                    {"number": 20, "timestamp": _ts(2025, 11, 2)},
                ],
                "logs": [
                    {"block_number": 10, "data": _raw_lognote_data(7_000_000 * _WAD)},
                    {"block_number": 20, "data": _raw_lognote_data(3_000_000 * _WAD)},
                ],
            }
        ],
        "next_block": 21,
    }
    df = HyperSyncDebtSource(post=_Post([page])).debt_timeseries(_ILK, date(2025, 11, 1), 20)
    # The 2025-10-30 dart is excluded; cum starts from the in-range event.
    assert df["block_date"].tolist() == [date(2025, 11, 2)]
    assert df["cum_debt"].tolist() == [Decimal("3000000")]


def test_small_darts_stay_exact_python_ints():
    """Aggregation must not round-trip through numpy int64 (silent wraparound
    past 2^63): 40 darts of 0.5e18 wad each must sum exactly, and the sum
    type must be an exact Decimal derived from Python ints."""
    n = 40
    page = {
        "data": [
            {
                "blocks": [{"number": 10, "timestamp": _ts(2025, 11, 18)}],
                "logs": [
                    {"block_number": 10, "log_index": i,
                     "data": _raw_lognote_data(_WAD // 2)}
                    for i in range(n)
                ],
            }
        ],
        "next_block": 11,
    }
    df = HyperSyncDebtSource(post=_Post([page])).debt_timeseries(_ILK, date(2025, 11, 1), 10)
    assert df["cum_debt"].tolist() == [Decimal(n) / 2]
