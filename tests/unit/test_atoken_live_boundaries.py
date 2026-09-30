"""Synthetic future-period accounting regressions; no historical replay."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from settle.compute.monthly_pnl import Sources
from settle.domain.config import load_prime_by_id
from settle.normalize.positions import _atoken_index_weighted_inflow
from settle.normalize.sources.hypersync_balances import HyperSyncBalanceSource
from settle.normalize.venue_sources import for_venue


@pytest.mark.parametrize('before,after,expected_capital', [
    (0, 1000000, 1000000),       # first deposit
    (1000000, 1500000, 500000),  # additional deposit / direct receipt
    (1000000, 500000, -500000),  # partial withdrawal / outgoing transfer
    (1000000, 0, -1000000),     # full exit
])
def test_live_boundaries_separate_post_event_yield(before, after, expected_capital):
    prime = load_prime_by_id('osero')
    venue = next(v for v in prime.venues if v.id == 'O1')
    scale = 10 ** venue.token.decimals
    # Event at block 10; a further 0.01% accrues by block 20. All values
    # are exact raw integers, independently of the accounting helper.
    balances = {0: before*scale, 9: before*scale, 10: after*scale,
                20: after*scale + after*scale//10000}
    shares = {0: before*scale, 9: before*scale, 10: after*scale, 20: after*scale}
    source = HyperSyncBalanceSource(fetch_logs=lambda *args: [SimpleNamespace(block_number=10, log_index=0)])
    routed = for_venue(Sources(balance=source), venue)
    def events(*args):
        return [(b-1, b, date(2026, 10, 2)) for b in routed.atoken_event_blocks(*args)]
    frame = _atoken_index_weighted_inflow(
        prime, venue, 0, 20, period_end_date=date(2026, 10, 31),
        balance_at=lambda c,t,h,b: balances[b], scaled_balance_at=lambda c,t,h,b: shares[b] if b in shares else before*scale,
        transfer_event_blocks=events,
    )
    capital = frame.iloc[-1]['cum_inflow']
    revenue = Decimal(balances[20]-balances[0])/scale - capital
    assert capital == Decimal(expected_capital)
    assert revenue == Decimal(after)/10000


def test_multiple_same_day_transfers_and_no_event_period():
    prime = load_prime_by_id('osero')
    venue = next(v for v in prime.venues if v.id == 'O1')
    scale = 10 ** venue.token.decimals
    # Deposit 100 at index 1; transfer out 50 shares at index 1.1;
    # end with 50 shares at index 1.2. Capital is 100 - 55 = 45,
    # and earned interest is 100*.1 + 50*.1 = 15.
    balances = {0:0, 9:0, 10:100, 19:110, 20:55, 30:60}
    shares = {0:0, 9:0, 10:100, 19:100, 20:50, 30:50}
    source = HyperSyncBalanceSource(fetch_logs=lambda *args: [
        SimpleNamespace(block_number=10, log_index=0),
        SimpleNamespace(block_number=20, log_index=0),
        SimpleNamespace(block_number=20, log_index=1),
    ])
    routed = for_venue(Sources(balance=source), venue)
    def events(*args):
        return [(b-1, b, date(2026, 10, 2)) for b in routed.atoken_event_blocks(*args)]
    frame = _atoken_index_weighted_inflow(
        prime, venue, 0, 30, period_end_date=date(2026, 10, 31),
        balance_at=lambda c,t,h,b: balances[b]*scale,
        scaled_balance_at=lambda c,t,h,b: shares[b]*scale,
        transfer_event_blocks=events,
    )
    assert len(frame) == 1
    assert frame.iloc[0]['cum_inflow'] == Decimal(45)
    assert Decimal(balances[30])-frame.iloc[0]['cum_inflow'] == Decimal(15)
    # The same callback yields no boundaries in the subsequent quiet period.
    frame = _atoken_index_weighted_inflow(
        prime, venue, 20, 30, period_end_date=date(2026, 10, 31),
        balance_at=lambda c,t,h,b: balances[b]*scale,
        scaled_balance_at=lambda c,t,h,b: shares[b]*scale,
        transfer_event_blocks=events,
    )
    assert frame.iloc[-1]['cum_inflow'] == 0
