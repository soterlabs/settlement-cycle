"""Unit tests for `settle.extract.postgres_store` — encode/decode roundtrip
and graceful degradation when Postgres is unavailable.

These tests deliberately don't touch a real Postgres. The encode/decode pair
is exercised exhaustively; the connection layer is exercised via the
``DATABASE_URL`` -unset path so they run in any CI."""

from __future__ import annotations

import math
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


def test_address_roundtrip():
    """Domain ``Address`` round-trips as a readable hex string in JSONB."""
    from settle.domain.primes import Address
    addr = Address(bytes.fromhex("9d77e4ca90e25114afb24df908f5918f572d958b"))
    encoded = encode_payload(addr)
    assert encoded == {"$address": "9d77e4ca90e25114afb24df908f5918f572d958b"}
    decoded = decode_payload(encoded)
    assert isinstance(decoded, Address)
    assert decoded == addr


def test_frozen_dataclass_roundtrip_via_pickle_fallback():
    """Custom frozen dataclasses (e.g. ``V3PoolState`` from uniswap_v3.slot0)
    ride the generic pickle envelope. Lossless but opaque in SQL —
    acceptable for low-volume domain types. Uses the real production
    dataclass so the test exercises the actual cache shape, not a synthetic
    locally-defined class (which pickle can't serialise anyway)."""
    from settle.domain.primes import Address
    from settle.extract.uniswap_v3 import V3PoolState

    state = V3PoolState(
        sqrt_price_x96=79_222_002_826_459_735_285_203_905_516,
        current_tick=-2,
        token0=Address(bytes.fromhex("9d77e4ca90e25114afb24df908f5918f572d958b")),
        token1=Address(bytes.fromhex("a0b8 6991 c621 8b36 c1d1 9d4a 2e9e b0ce 3606 eb48".replace(" ", ""))),
        fee=500,
        fee_growth_global_0_x128=0,
        fee_growth_global_1_x128=0,
    )
    encoded = encode_payload(state)
    assert isinstance(encoded, dict) and "$pickle" in encoded
    decoded = decode_payload(encoded)
    assert decoded == state
    # Nested Address survives via pickle (not the $address envelope, since
    # the outer object is pickled whole).
    assert isinstance(decoded.token0, Address)


def test_encode_truly_unpicklable_raises():
    """Pickle fallback is the catch-all, but truly unpicklable values
    (lambdas, generators, open files) still raise so the call site sees
    them as a code-time error rather than a silent data loss."""
    with pytest.raises(TypeError, match="cannot encode"):
        encode_payload(lambda x: x)


def test_bool_encodes_as_bool_not_int():
    """``bool`` is a subclass of ``int`` in Python — encoder must preserve
    the distinction so JSON round-trips ``True`` rather than ``1``."""
    assert encode_payload(True) is True
    assert encode_payload(False) is False
    assert decode_payload(True) is True


def test_nan_and_inf_coerce_to_none():
    """JSON has no NaN/Inf; psycopg's Jsonb adapter would reject them.
    Round-trip via None so DataFrames with NaN cells re-materialise as NaN
    (pandas converts None back to NaN inside numeric columns)."""
    assert encode_payload(float("nan")) is None
    assert encode_payload(float("inf")) is None
    assert encode_payload(float("-inf")) is None
    # Regular finite floats pass through unchanged.
    assert encode_payload(1.5) == 1.5


# --- pandas.DataFrame roundtrip ---------------------------------------------

def test_dataframe_roundtrip_basic():
    """Dune-shape DataFrame (string + int columns) round-trips with column
    names + values preserved."""
    import pandas as pd
    df = pd.DataFrame({
        "block_date": ["2025-11-18", "2025-11-19"],
        "cum_balance": [0, 0],
    })
    encoded = encode_payload(df)
    assert isinstance(encoded, dict) and "$dataframe" in encoded
    decoded = decode_payload(encoded)
    assert isinstance(decoded, pd.DataFrame)
    assert list(decoded.columns) == ["block_date", "cum_balance"]
    assert decoded["block_date"].tolist() == ["2025-11-18", "2025-11-19"]
    assert decoded["cum_balance"].tolist() == [0, 0]


def test_empty_dataframe_preserves_columns():
    """Empty DataFrame with named columns must keep those columns on
    reconstruction (``orient='records'`` would drop them — this is why we
    use ``orient='split'`` instead)."""
    import pandas as pd
    df = pd.DataFrame(columns=["a", "b"])
    decoded = decode_payload(encode_payload(df))
    assert isinstance(decoded, pd.DataFrame)
    assert list(decoded.columns) == ["a", "b"]
    assert len(decoded) == 0


def test_fully_empty_dataframe_roundtrip():
    """``pd.DataFrame()`` with no columns and no rows — should not raise."""
    import pandas as pd
    df = pd.DataFrame()
    decoded = decode_payload(encode_payload(df))
    assert isinstance(decoded, pd.DataFrame)
    assert len(decoded) == 0


def test_dataframe_with_decimal_column_roundtrip():
    """Decimal cells (Dune numeric columns) must survive the round-trip
    without coercion to float."""
    import pandas as pd
    df = pd.DataFrame({"v": [Decimal("1.5"), Decimal("2.75")]})
    decoded = decode_payload(encode_payload(df))
    assert decoded["v"].tolist() == [Decimal("1.5"), Decimal("2.75")]


def test_dataframe_with_nan_roundtrip():
    """NaN cells encode as None and re-materialise as NaN on reconstruction."""
    import pandas as pd
    df = pd.DataFrame({"x": [1.0, float("nan"), 3.0]})
    decoded = decode_payload(encode_payload(df))
    vals = decoded["x"].tolist()
    assert vals[0] == 1.0
    assert math.isnan(vals[1])  # type: ignore[arg-type]
    assert vals[2] == 3.0


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
