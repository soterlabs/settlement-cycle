"""Unit tests for `settle.extract.postgres_store` — encode/decode roundtrip
and graceful degradation when Postgres is unavailable.

These tests deliberately don't touch a real Postgres. The encode/decode pair
is exercised exhaustively; the connection layer is exercised via the
``DATABASE_URL`` -unset path so they run in any CI."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from settle.extract import postgres_store
from settle.extract.postgres_store import decode_payload, encode_payload

# --- encode_payload / decode_payload roundtrip ------------------------------

@pytest.mark.parametrize("v", [None, True, False, 0, 1, -1, 1.5, "", "hello"])
def test_primitives_roundtrip(v):
    assert decode_payload(encode_payload(v)) == v


def test_decimal_roundtrip():
    v = Decimal("1017.20391300")
    out = encode_payload(v)
    assert out == {"$decimal": "1017.20391300"}
    decoded = decode_payload(out)
    assert decoded == v
    assert isinstance(decoded, Decimal)


def test_bytes_roundtrip():
    v = b"\x00\x01\xff"
    out = encode_payload(v)
    assert out == {"$bytes": "0001ff"}
    assert decode_payload(out) == v


def test_datetime_roundtrip():
    dt = datetime(2026, 5, 12, 14, 30, 0, tzinfo=UTC)
    out = encode_payload(dt)
    assert out == {"$datetime": "2026-05-12T14:30:00+00:00"}
    assert decode_payload(out) == dt


def test_date_roundtrip_distinct_from_datetime():
    """``date`` is encoded separately from ``datetime`` so naive dates don't
    silently widen into datetimes at midnight."""
    d = date(2026, 5, 12)
    out = encode_payload(d)
    assert out == {"$date": "2026-05-12"}
    decoded = decode_payload(out)
    assert decoded == d
    assert not isinstance(decoded, datetime)


def test_tuple_roundtrip():
    """Tuples must round-trip as tuples (not lists) — some callers depend on
    immutable / hashable returns."""
    v = (1, "two", Decimal("3.5"))
    decoded = decode_payload(encode_payload(v))
    assert decoded == v
    assert isinstance(decoded, tuple)


def test_dune_row_shape_roundtrip():
    """Mirrors a Dune query result row: list[dict] with mixed primitive +
    Decimal + date values. This is the most common payload shape."""
    rows = [
        {"date": date(2026, 1, 1), "block": 24136052, "value": Decimal("1.05")},
        {"date": date(2026, 2, 1), "block": 24358292, "value": Decimal("1.10")},
    ]
    assert decode_payload(encode_payload(rows)) == rows


def test_nested_dict_roundtrip():
    payload = {
        "chain": "ethereum",
        "venue": {"id": "E7", "decimals": 6, "nav": Decimal("1017.20")},
        "snapshot_at": datetime(2026, 5, 12, tzinfo=UTC),
        "tags": ["rwa", "clo"],
    }
    assert decode_payload(encode_payload(payload)) == payload


def test_encode_rejects_unknown_type():
    """Silent fallback (e.g. ``repr(obj)``) would lose data on round-trip —
    encoder must raise so unknown payloads become a code-time error, not a
    silent corruption when reading back from Postgres."""
    class Custom:
        pass
    with pytest.raises(TypeError, match="unsupported type"):
        encode_payload(Custom())


def test_bool_encodes_as_bool_not_int():
    """``bool`` is a subclass of ``int`` in Python — encoder must preserve
    the distinction so JSON round-trips ``True`` rather than ``1``."""
    assert encode_payload(True) is True
    assert encode_payload(False) is False
    assert decode_payload(True) is True


# --- Graceful degradation when DATABASE_URL is unset ------------------------

def test_get_returns_MISS_when_no_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    postgres_store._reset_for_tests()
    assert postgres_store.get("test.source", "deadbeef") is postgres_store.MISS


def test_put_is_noop_when_no_database_url(monkeypatch):
    """Should not raise; the pipeline keeps working on pickle-only."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    postgres_store._reset_for_tests()
    postgres_store.put("test.source", "deadbeef", {"a": 1}, Decimal("3.14"))


def test_is_enabled_false_when_no_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    postgres_store._reset_for_tests()
    assert postgres_store.is_enabled() is False


def test_apply_schema_raises_when_postgres_unavailable(monkeypatch):
    """Schema application must fail-fast — unlike get/put, callers want to
    know immediately if the DB is unreachable rather than silently no-op."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    postgres_store._reset_for_tests()
    with pytest.raises(RuntimeError, match="Postgres unavailable"):
        postgres_store.apply_schema("SELECT 1;")
