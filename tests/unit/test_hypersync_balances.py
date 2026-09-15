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


def test_start_includes_all_blocks_sharing_midnight_timestamp(monkeypatch):
    from settle.extract import hypersync
    midnight = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp())
    monkeypatch.setattr(hypersync, "find_block_at_or_before",
                        lambda chain, timestamp: 100 if timestamp < midnight else 104)
    rows = [_xfer(100, 0, midnight - 1, _A, _H, 900 * 10**_DEC)]
    rows += [_xfer(b, 0, midnight, _A, _H, 10**_DEC) for b in range(101, 105)]
    source = HyperSyncBalanceSource(
        fetch_logs=lambda chain, sel, start, end: [r for r in rows if start <= r.block_number <= end],
        decimals_of=lambda *args: _DEC,
    )
    frame = source.cumulative_balance_timeseries("arbitrum", _TOKEN, _H, date(2026, 8, 1), 105)
    assert frame["daily_net"].tolist() == [Decimal("4")]


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


def test_inflow_by_counterparty_ignores_self_transfer():
    U = 10**_DEC
    rows = [
        _xfer(10, 0, _ts(2025, 11, 18), _A, _H, 100 * U),   # +100 from A (real inflow)
        # holder→holder: no external counterparty, nets to 0. Dedups to one
        # row matching both selections; must NOT show up as a spurious inflow
        # attributed to the holder itself.
        _xfer(11, 0, _ts(2025, 11, 18), _H, _H, 5 * U),
        _xfer(11, 0, _ts(2025, 11, 18), _H, _H, 5 * U),     # dup (both selections)
    ]
    df = _src(rows).inflow_by_counterparty("ethereum", _TOKEN, _H, date(2025, 11, 1), 25_000_000)
    by_cp = {row.counterparty: row.signed_amount for row in df.itertuples()}
    assert by_cp == {_A: Decimal("100")}                    # holder not a counterparty


def test_directed_flow_reuses_complete_holder_logs_without_live_query():
    rows = [_xfer(10, 0, _ts(2025, 11, 18), _A, _H, 40 * 10**_DEC),
            _xfer(11, 0, _ts(2025, 11, 18), _B, _H, 50 * 10**_DEC),
            _xfer(12, 0, _ts(2025, 11, 18), _H, _A, 30 * 10**_DEC)]
    def covered(chain, selections, start, end):
        return rows if selections[0]["topics"][1] == [_topic(_H)] else None
    def no_live(*args):
        raise AssertionError("A fully covered superset should need no new scan")
    source = HyperSyncBalanceSource(fetch_logs=no_live, covered_logs=covered,
                resolve_start_block=lambda *a: 0, decimals_of=lambda *a: _DEC)
    result = source.directed_inflow_timeseries("base", _TOKEN, _A, _H, date(2025, 11, 1), 100)
    assert result["cum_inflow"].tolist() == [Decimal(40)]


def test_empty_covered_range_is_not_a_cache_miss():
    def no_live(*args):
        raise AssertionError("An empty fully covered stream proves zero events")
    source = HyperSyncBalanceSource(fetch_logs=no_live, covered_logs=lambda *a: [],
                resolve_start_block=lambda *a: 0, decimals_of=lambda *a: _DEC)
    assert source.directed_inflow_timeseries("base", _TOKEN, _A, _H, date(2025, 11, 1), 100).empty


@pytest.mark.parametrize("method", [
    "cumulative_balance_timeseries", "directed_inflow_timeseries", "inflow_by_counterparty",
])
def test_predeployment_history_needs_no_decimals(method):
    def unavailable_decimals(*args):
        raise AssertionError("Predeployment decimals() must not be requested")
    source = HyperSyncBalanceSource(
        fetch_logs=lambda *args: [], resolve_start_block=lambda *args: 0,
        decimals_of=unavailable_decimals,
    )
    holders = (_A, _H) if method == "directed_inflow_timeseries" else (_H,)
    frame = getattr(source, method)("ethereum", _TOKEN, *holders, date(2026, 1, 1), 100)
    assert frame.empty
    assert list(frame.columns) == {
        "cumulative_balance_timeseries": ["block_date", "daily_net", "cum_balance"],
        "directed_inflow_timeseries": ["block_date", "daily_inflow", "cum_inflow"],
        "inflow_by_counterparty": ["block_date", "counterparty", "signed_amount"],
    }[method]


@pytest.mark.parametrize("method", [
    "cumulative_balance_timeseries", "directed_inflow_timeseries", "inflow_by_counterparty",
])
@pytest.mark.parametrize("failure", ["logs", "decimals"])
def test_provider_failure_is_not_an_empty_history(method, failure):
    from settle.extract.hypersync import HyperSyncError
    from settle.extract.rpc import RPCError

    def fetch(*args):
        if failure == "logs":
            raise HyperSyncError("unavailable logs")
        return [_xfer(10, 0, _ts(2026, 1, 2), _A, _H, 10**_DEC)]

    def decimals(*args):
        raise RPCError("unavailable metadata")

    source = HyperSyncBalanceSource(fetch_logs=fetch, resolve_start_block=lambda *args: 0,
                                    decimals_of=decimals)
    holders = (_A, _H) if method == "directed_inflow_timeseries" else (_H,)
    with pytest.raises(HyperSyncError if failure == "logs" else RPCError):
        getattr(source, method)("ethereum", _TOKEN, *holders, date(2026, 1, 1), 100)


def test_spark_s65_january_share_flows_before_deployment():
    from settle.domain.config import load_prime_by_id
    from settle.domain.period import Period
    from settle.domain.primes import Chain
    from settle.normalize.positions import _shares_to_usd_inflow_timeseries

    prime = load_prime_by_id("spark")
    venue = next(v for v in prime.venues if v.id == "S65")
    period = Period(date(2026, 1, 1), date(2026, 1, 31), {Chain.ETHEREUM: 24358292})

    def no_metadata_or_price(*args):
        raise AssertionError("No metadata or prices needed before deployment")

    source = HyperSyncBalanceSource(
        fetch_logs=lambda *args: [], resolve_start_block=lambda *args: 0,
        decimals_of=no_metadata_or_price,
    )
    frame = _shares_to_usd_inflow_timeseries(
        prime, venue, period, balance_source=source, block_resolver=None,
        price_at_block=no_metadata_or_price, period_only=True,
    )
    assert frame.empty
