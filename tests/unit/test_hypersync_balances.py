"""Unit tests for the HyperSync-direct IBalanceSource (no network/DB)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from settle.extract.hypersync import LogRow
from settle.normalize.sources.hypersync_balances import HyperSyncBalanceSource

_DEC = 6
_TOKEN = bytes.fromhex("80ac24aa929eaf5013f6436cda2a7ba190f5cc0b")  # syrupUSDC
_H = bytes.fromhex("b6dd7ae22c9922afee0642f9ac13e58633f715a2")       # OBEX ALM
_A = bytes.fromhex("1111111111111111111111111111111111111111")
_B = bytes.fromhex("2222222222222222222222222222222222222222")
_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def _topic(b: bytes) -> str:
    return "0x" + b.hex().rjust(64, "0")


def _ts(y, m, d) -> int:
    return int(datetime(y, m, d, 12, tzinfo=timezone.utc).timestamp())


def _xfer(block, li, ts, frm, to, value) -> LogRow:
    return LogRow(
        block_number=block, log_index=li, block_time=ts, address="0x" + _TOKEN.hex(),
        topic0=_TRANSFER, topic1=_topic(frm), topic2=_topic(to), topic3=None,
        data="0x" + format(value, "064x"),
    )


def _src(rows):
    return HyperSyncBalanceSource(
        fetch_logs=lambda chain, sel, frm, to: list(rows),
        resolve_start_block=lambda chain, start: 0,
        decimals_of=lambda chain, token, block: _DEC,
    )


def test_cumulative_balance_daily_net_and_cumsum():
    U = 10**_DEC
    rows = [
        _xfer(10, 0, _ts(2025, 11, 18), _A, _H, 100 * U),   # +100 inflow
        _xfer(11, 0, _ts(2025, 11, 18), _H, _B, 30 * U),    # -30 outflow
        _xfer(20, 0, _ts(2025, 11, 19), _A, _H, 50 * U),    # +50 inflow
    ]
    df = _src(rows).cumulative_balance_timeseries("ethereum", _TOKEN, _H, date(2025, 11, 1), 25_000_000)
    assert list(df.columns) == ["block_date", "daily_net", "cum_balance"]
    assert df["daily_net"].tolist() == [Decimal("70"), Decimal("50")]
    assert df["cum_balance"].tolist() == [Decimal("70"), Decimal("120")]
    assert all(isinstance(v, Decimal) for v in df["cum_balance"])


def test_min_transfer_amount_filter():
    U = 10**_DEC
    rows = [
        _xfer(10, 0, _ts(2025, 11, 18), _A, _H, 100 * U),   # kept
        _xfer(10, 1, _ts(2025, 11, 18), _A, _H, 5 * U),     # dropped (< 10)
    ]
    df = _src(rows).cumulative_balance_timeseries(
        "ethereum", _TOKEN, _H, date(2025, 11, 1), 25_000_000, min_transfer_amount=Decimal(10)
    )
    assert df["cum_balance"].tolist() == [Decimal("100")]


def test_start_date_clip():
    U = 10**_DEC
    rows = [
        _xfer(5, 0, _ts(2025, 10, 30), _A, _H, 999 * U),    # before start → dropped
        _xfer(10, 0, _ts(2025, 11, 18), _A, _H, 100 * U),
    ]
    df = _src(rows).cumulative_balance_timeseries("ethereum", _TOKEN, _H, date(2025, 11, 1), 25_000_000)
    assert df["block_date"].tolist() == [date(2025, 11, 18)]
    assert df["cum_balance"].tolist() == [Decimal("100")]


def test_directed_inflow():
    U = 10**_DEC
    rows = [
        _xfer(10, 0, _ts(2025, 11, 18), _A, _B, 40 * U),
        _xfer(20, 0, _ts(2025, 11, 19), _A, _B, 60 * U),
    ]
    df = _src(rows).directed_inflow_timeseries("ethereum", _TOKEN, _A, _B, date(2025, 11, 1), 25_000_000)
    assert list(df.columns) == ["block_date", "daily_inflow", "cum_inflow"]
    assert df["cum_inflow"].tolist() == [Decimal("40"), Decimal("100")]


def test_inflow_by_counterparty_signed_and_grouped():
    U = 10**_DEC
    rows = [
        _xfer(10, 0, _ts(2025, 11, 18), _A, _H, 100 * U),   # +100 from A
        _xfer(11, 0, _ts(2025, 11, 18), _H, _B, 30 * U),    # -30 to B
        _xfer(12, 0, _ts(2025, 11, 18), _A, _H, 10 * U),    # +10 from A (same day/cp → sums)
    ]
    df = _src(rows).inflow_by_counterparty("ethereum", _TOKEN, _H, date(2025, 11, 1), 25_000_000)
    assert list(df.columns) == ["block_date", "counterparty", "signed_amount"]
    by_cp = {row.counterparty: row.signed_amount for row in df.itertuples()}
    assert by_cp[_A] == Decimal("110")     # 100 + 10, netted per counterparty
    assert by_cp[_B] == Decimal("-30")


def test_dedup_self_transfer():
    U = 10**_DEC
    # same (block, log_index) returned twice (matches both from+to selections)
    r = _xfer(10, 0, _ts(2025, 11, 18), _H, _H, 5 * U)
    df = _src([r, r]).cumulative_balance_timeseries("ethereum", _TOKEN, _H, date(2025, 11, 1), 25_000_000)
    # self-transfer nets to 0, counted once
    assert df["daily_net"].tolist() == [Decimal("0")]
