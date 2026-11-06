"""BUIDL's matched receivable must have the same lifetime in every path."""
import json
from datetime import UTC, date, datetime, time
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest

from settle.compute.monthly_pnl import _sde_asset_value_timeseries
from settle.compute.prime_agent_revenue import VenueRevenueInputs, compute_venue_revenue
from settle.domain.config import load_prime_by_id
from settle.domain.period import Period
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize import async_vaults as av
from settle.normalize import positions
from settle.normalize import redemption_settlements as redemption
from settle.normalize.sources.hypersync_balances import _addr_topic

ROOT = Path(__file__).resolve().parents[2]


class Blocks:
    """Synthetic closing pins; actual event blocks remain below these pins."""
    @staticmethod
    def pin(day):
        return 30_000_000 + day.toordinal()

    def block_at_or_before(self, chain, anchor):
        return self.pin(anchor.date())


@pytest.fixture
def buidl(monkeypatch):
    prime = load_prime_by_id('grove', config_dir=ROOT / 'config')
    venue = next(v for v in prime.venues if v.id == 'E10')
    events = json.loads((ROOT / 'tests/fixtures/sde_inflight/buidl_events.json').read_text())['events']
    rows = [LogRow(e['block'], e['log_index'],
        int(datetime.combine(date.fromisoformat(e['date']), time.min, tzinfo=UTC).timestamp()),
        e['token'], TRANSFER_TOPIC0, _addr_topic(bytes.fromhex(e['sender'][2:])),
        _addr_topic(bytes.fromhex(e['recipient'][2:])), None,
        '0x' + format(int(D(e['amount']) * 10**6), '064x'), e['tx']) for e in events]
    monkeypatch.setattr(redemption, 'window_logs', lambda *args, **kwargs: rows)
    from settle.extract import rpc
    def timestamp(chain, block):
        if block in {25_883_246, 25_883_247}:
            day = date(2026, 9, 1)
        else:
            day = (date.fromordinal(block - 30_000_000) if block >= 30_000_000
                   else date.fromisoformat(next(e['date'] for e in events if e['block'] == block)))
        return int(datetime.combine(day, time.max, tzinfo=UTC).timestamp())
    monkeypatch.setattr(rpc, 'block_timestamp', timestamp)
    return prime, venue


def period(venue, start, end):
    return Period(start, end, {venue.chain: Blocks.pin(end)})


def test_position_and_sde_include_identical_weekend_cash_claim(buidl, monkeypatch):
    prime, venue = buidl
    day = date(2026, 9, 4)
    monkeypatch.setattr(positions, 'get_position_balance', lambda *a, **kw: D(100_000_000))
    monkeypatch.setattr(positions, 'get_unit_price', lambda *a, **kw: D('.9995'))
    class Balances:
        def cumulative_balance_timeseries(self, **kwargs):
            return pd.DataFrame({'block_date': [prime.start_date], 'cum_balance': [D(100_000_000)]})
    rows = _sde_asset_value_timeseries(prime, venue, period(venue, day, date(2026, 9, 7)),
        balance_source=Balances(), block_resolver=Blocks(), nav_at_block=lambda b: D('.9995'))
    for row in rows.to_dict('records'):
        assert row['pending_redemption_value'] == D('24987500')
        assert positions.get_position_value(prime, venue, Blocks.pin(row['block_date'])) == row['uncapped_value']
        assert row['uncapped_value'] == D('124937500')


def test_cash_only_position_remains_until_payment_and_needs_no_nav(buidl, monkeypatch):
    prime, venue = buidl
    monkeypatch.setattr(positions, 'get_position_balance', lambda *a, **kw: D(0))
    monkeypatch.setattr(positions, 'get_unit_price', lambda *a, **kw: pytest.fail('Cash needs no NAV'))
    assert positions.get_position_value(prime, venue, Blocks.pin(date(2026, 9, 7))) == D('24987500')
    assert positions.get_position_value(prime, venue, Blocks.pin(date(2026, 9, 8))) == 0


def test_later_same_day_cash_cannot_erase_an_earlier_claim(buidl):
    prime, venue = buidl
    # September 1 receipt at 25,883,247 settles the Aug 31 request. The
    # preceding same-day block must still own the entire receivable.
    before = av.async_vault_exposure(prime, venue, 25_883_247 - 1)
    after = av.async_vault_exposure(prime, venue, 25_883_247)
    assert before.redemption_cash == D('24986500.50')
    assert after.redemption_cash == 0


def test_share_exit_keeps_capital_in_average_holdings_through_weekend(buidl):
    prime, venue = buidl
    start, end = date(2026, 9, 4), date(2026, 9, 8)
    report = period(venue, start, end)
    raw = pd.DataFrame({'block_date': [start], 'daily_inflow': [D('-24987500')],
                        'cum_inflow': [D('-24987500')]})
    flows = av.async_capital_inflow_timeseries(prime, venue, report, raw,
        opening_block=Blocks.pin(date(2026, 9, 3)), block_resolver=Blocks())
    assert list(flows['daily_inflow']) == [D(0)] * 4 + [D('-24987500')]
    ledger = redemption.redemption_settlements(prime, venue, report, track_pending=True)
    result = compute_venue_revenue(report, VenueRevenueInputs(venue, D('24987500'), D(0), flows,
        fee_inflow_timeseries=raw, redemption_revenue_adjustment=ledger.revenue_adjustment))
    assert result.tw_avg_value == D('19990000')
    assert result.actual_revenue == D('-.419927')  # only actual cash variance, once


def test_month_boundary_retains_claim_without_rebooking_principal_or_fees(buidl):
    prime, venue = buidl
    # Aug 31 basis is $1 for revenue; the existing expected payout/SDE mark
    # is .9995. Preserve both the old cash-realization date and CoF basis.
    august = period(venue, date(2026, 8, 31), date(2026, 8, 31))
    raw = pd.DataFrame({'block_date': [august.start], 'daily_inflow': [D('-24999000')],
                        'cum_inflow': [D('-24999000')]})
    aug_ledger = redemption.redemption_settlements(prime, venue, august, track_pending=True)
    raw = redemption.restore_redemption_capital(raw, aug_ledger)
    assert sum(raw['daily_inflow'], D(0)) == D(-25_000_000)  # includes the verified 1,000-share exit
    aug_flows = av.async_capital_inflow_timeseries(prime, venue, august, raw,
        opening_block=Blocks.pin(date(2026, 8, 30)), block_resolver=Blocks())
    aug = compute_venue_revenue(august, VenueRevenueInputs(venue, D(25_000_000),
        D('24986500.50'), aug_flows, fee_inflow_timeseries=raw))
    assert aug.actual_revenue == 0  # pre-recognition fees remain unbooked
    assert aug.tw_avg_value == D('24986500.50')
    september = period(venue, date(2026, 9, 1), date(2026, 9, 1))
    raw_sep = pd.DataFrame({'block_date': [september.start], 'daily_inflow': [D('-24987500')],
                            'cum_inflow': [D('-24987500')]})
    sep_flows = av.async_capital_inflow_timeseries(prime, venue, september, raw_sep,
        opening_block=Blocks.pin(august.end), block_resolver=Blocks())
    ledger = redemption.redemption_settlements(prime, venue, september, track_pending=True)
    sep = compute_venue_revenue(september, VenueRevenueInputs(venue, D('49974000.50'), D(0),
        sep_flows, fee_inflow_timeseries=raw_sep, redemption_revenue_adjustment=ledger.revenue_adjustment))
    assert sep.tw_avg_value == 0
    assert sep.actual_revenue == D('-12500.177970')
    assert sep.actual_revenue == ledger.revenue_adjustment


def test_subscription_fee_detection_uses_original_events_not_cash_reclassification(buidl):
    _, venue = buidl
    day = date(2026, 9, 1)
    report = period(venue, day, day)
    # Economic flows can offset to a round number; fee detection must still
    # see the original 49,985,000-share subscription (priced at .9995).
    raw = pd.DataFrame({'block_date': [day], 'daily_inflow': [D('49985000') * D('.9995')],
                        'cum_inflow': [D('49985000') * D('.9995')]})
    economic = pd.DataFrame({'block_date': [day], 'daily_inflow': [D(50_000_000)],
                             'cum_inflow': [D(50_000_000)]})
    result = compute_venue_revenue(report, VenueRevenueInputs(venue, D(0), D(50_000_000),
        economic, fee_inflow_timeseries=raw))
    assert result.actual_revenue == D(-15_000)
    # A pending-claim reclassification matching the fee signature is not a
    # subscription; an empty source event stream must leave fees at zero.
    no_events = raw.iloc[:0]
    result = compute_venue_revenue(report, VenueRevenueInputs(venue, D(0),
        raw.iloc[0]['daily_inflow'], raw, fee_inflow_timeseries=no_events))
    assert result.actual_revenue == 0


def test_failed_claim_history_stops_position_valuation(buidl, monkeypatch):
    prime, venue = buidl
    def fail(*args, **kwargs):
        raise ConnectionError('claim history unavailable')
    monkeypatch.setattr(redemption, 'window_logs', fail)
    with pytest.raises(ConnectionError, match='claim history unavailable'):
        av.async_vault_exposure(prime, venue, Blocks.pin(date(2026, 9, 7)))
