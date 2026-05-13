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


# --- Auto-reconnect on stale/broken connection ------------------------------
#
# These tests pin the new ``_is_healthy`` + ``_drop_conn`` behaviour added
# to fix the "connection is closed" log-spam we hit during the long-running
# Spark settlement: a single cached conn would server-idle-timeout, then
# every subsequent ``get``/``put`` would log a warning forever. New
# behaviour: detect closed/broken on next ``_get_conn``, drop, reconnect.

class _FakeConn:
    """Minimal stand-in for ``psycopg.Connection`` — exposes the ``.closed``
    and ``.broken`` flags that ``_is_healthy`` inspects, plus a ``close()``
    that ``_drop_conn`` calls."""

    def __init__(self, *, closed: bool = False, broken: bool = False) -> None:
        self.closed = closed
        self.broken = broken
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


def test_is_healthy_none_is_unhealthy():
    assert postgres_store._is_healthy(None) is False


def test_is_healthy_alive_conn_is_healthy():
    assert postgres_store._is_healthy(_FakeConn()) is True


def test_is_healthy_closed_conn_is_unhealthy():
    """Client-side close → ``.closed=True`` → must reconnect on next call."""
    assert postgres_store._is_healthy(_FakeConn(closed=True)) is False


def test_is_healthy_broken_conn_is_unhealthy():
    """Server-side hang-up (idle timeout, restart) sets ``.broken=True``."""
    assert postgres_store._is_healthy(_FakeConn(broken=True)) is False


def test_is_healthy_swallows_attribute_errors():
    """A pathological object that raises on attribute access shouldn't crash
    the health check — we just consider it unhealthy and reconnect."""
    class _Pathological:
        @property
        def broken(self):
            raise RuntimeError("oops")
    assert postgres_store._is_healthy(_Pathological()) is False


def test_drop_conn_closes_and_clears_state(monkeypatch):
    """``_drop_conn`` should call ``close()`` on the cached conn and clear
    ``_state["conn"]`` so the next ``_get_conn`` reconnects."""
    fake = _FakeConn()
    postgres_store._reset_for_tests()
    postgres_store._state["conn"] = fake
    postgres_store._state["disabled"] = False

    postgres_store._drop_conn("test")

    assert fake.close_calls == 1
    assert postgres_store._state["conn"] is None
    # ``disabled`` is left untouched — a transient drop should let the next
    # call retry connecting.
    assert postgres_store._state["disabled"] is False


def test_drop_conn_tolerates_close_error(monkeypatch):
    """If the underlying conn raises on ``close()`` (e.g. already closed by
    libpq), ``_drop_conn`` should still clear the cache rather than
    propagating the exception — the alternative is a permanently-broken
    cache entry."""
    class _NoisyCloseConn(_FakeConn):
        def close(self) -> None:
            super().close()
            raise RuntimeError("already closed by libpq")

    fake = _NoisyCloseConn()
    postgres_store._reset_for_tests()
    postgres_store._state["conn"] = fake

    postgres_store._drop_conn("test")  # should not raise

    assert postgres_store._state["conn"] is None


def test_get_conn_reconnects_after_drop(monkeypatch):
    """End-to-end: a broken conn → ``_get_conn`` should detect, drop, and
    call ``psycopg.connect`` again. Uses a stub ``psycopg`` so the test
    runs without a real Postgres."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/")
    postgres_store._reset_for_tests()

    # Stub ``psycopg.connect`` to count calls and return a fresh fake.
    call_count = {"n": 0}
    new_conn = _FakeConn()

    class _StubPsycopg:
        @staticmethod
        def connect(url, autocommit=False):
            call_count["n"] += 1
            return new_conn

    import sys
    monkeypatch.setitem(sys.modules, "psycopg", _StubPsycopg)

    # Inject a broken cached conn so the fast path falls through and
    # ``_get_conn`` decides to reconnect.
    broken = _FakeConn(broken=True)
    postgres_store._state["conn"] = broken
    postgres_store._state["disabled"] = False

    out = postgres_store._get_conn()

    # 1) The broken conn was closed (cleanup), 2) ``psycopg.connect`` was
    # called exactly once to reconnect, 3) the new conn is now cached.
    assert broken.close_calls == 1
    assert call_count["n"] == 1
    assert out is new_conn
    assert postgres_store._state["conn"] is new_conn

    postgres_store._reset_for_tests()


def test_get_conn_does_not_permanently_disable_on_connect_failure(monkeypatch):
    """Pre-PR: a single ``psycopg.connect`` failure would set
    ``disabled=True`` permanently — every subsequent call returned None
    even after the network recovered. Post-PR: a connect failure leaves
    ``disabled=None`` so the NEXT call retries."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/")
    postgres_store._reset_for_tests()

    class _FailingPsycopg:
        @staticmethod
        def connect(url, autocommit=False):
            raise RuntimeError("simulated network blip")

    import sys
    monkeypatch.setitem(sys.modules, "psycopg", _FailingPsycopg)

    assert postgres_store._get_conn() is None
    # Critical post-PR behaviour: transient failures must NOT flip
    # ``disabled`` permanently. The previous attempt left it at None /
    # False so the next call retries.
    assert postgres_store._state["disabled"] is not True

    postgres_store._reset_for_tests()
