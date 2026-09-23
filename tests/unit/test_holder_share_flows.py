from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pandas as pd
import pytest

from settle.domain.config import load_prime_by_id
from settle.domain.period import Period
from settle.domain.primes import PricingCategory
from settle.normalize.holder_share_flows import holder_share_inflows


def run(rows, opening, closing, *, end=6, venue=None):
    prime = load_prime_by_id('spark')
    venue = venue or next(v for v in prime.venues if v.id == 'S43')
    period = Period(date(2026, 9, 1), date(2026, 9, end), {venue.chain: end})
    reads = []
    def price(block):
        reads.append(block)
        return D('1.108702777084081964')
    frame = pd.DataFrame(rows, columns=['block_date', 'daily_net'])
    source = SimpleNamespace(cumulative_balance_timeseries=lambda **kw: frame)
    resolver = SimpleNamespace(block_at_or_before=lambda chain, anchor: anchor.day)
    out = holder_share_inflows(prime, venue, period, balance_source=source,
        block_resolver=resolver, price_at_block=price,
        opening_shares=D(opening), closing_shares=D(closing))
    return out, reads


def test_s43_september_psm_transfer_retains_appreciation_and_real_exit_day():
    opening, closing = '80894745.637041', '0.637041'
    frame, reads = run([(date(2026, 9, 6), D('-80894745'))], opening, closing)
    outflow = frame.iloc[-1].cum_inflow
    assert outflow == D('-89688228.433008654036879180')
    revenue = D('0.7062891047102813185700555707') - D('89636603.76011395185942167486') - outflow
    assert abs(revenue - D('51625.37918380688773882371')) < D('0.000001')
    later, later_reads = run([(date(2026, 9, 6), D('-80894745'))], opening, closing, end=11)
    assert later.equals(frame)  # never move the outflow to cutoff day
    assert reads == later_reads == [6]


def test_all_transfer_directions_net_in_period_before_pricing():
    rows = [(date(2026, 8, 31), D(100)), (date(2026, 9, 2), D(20)),
            (date(2026, 9, 3), D(-10)), (date(2026, 9, 4), D(5)),
            (date(2026, 9, 5), D(-3)), (date(2026, 9, 7), D(1000))]
    frame, reads = run(rows, '100', '112')
    assert reads == [2, 3, 4, 5]
    assert frame.daily_inflow.tolist() == [D(n)*D('1.108702777084081964') for n in [20,-10,5,-3]]


def test_capture_gap_fails_instead_of_creating_phantom_yield():
    with pytest.raises(ValueError, match='reconciliation failed'):
        run([], '80894745.637041', '0.637041')
    frame, reads = run([], '10', '10')
    assert frame.empty and reads == []


def test_rebasing_tokens_cannot_use_holder_share_accounting():
    prime = load_prime_by_id('spark')
    venue = replace(next(v for v in prime.venues if v.id == 'S43'),
                    pricing_category=PricingCategory.SPARKLEND_SPTOKEN)
    with pytest.raises(ValueError, match='non-rebasing'):
        run([], '10', '10', venue=venue)


def test_spread_reimbursement_stops_on_actual_exit_day():
    from settle.compute.monthly_pnl import _susds_cat_b_spread_reimb
    frame, _ = run([(date(2026, 9, 6), D('-80894745'))],
                   '80894745.637041', '0.637041', end=11)
    value = D('89636603.76011395185942167486')
    period = Period(date(2026, 9, 1), date(2026, 9, 11), {})
    # September's configured BR-SSR spread is 20 bps, accrued on five days.
    expected = value * D('0.002') / D(365) * D(5)
    assert abs(_susds_cat_b_spread_reimb(value, frame, period) - expected) < D('1e-15')


def test_holder_override_and_source_boundaries():
    from settle.domain.primes import Address
    prime = load_prime_by_id('spark')
    venue = replace(next(v for v in prime.venues if v.id == 'S43'),
                    holder_override=Address.from_str('0x'+'12'*20))
    period = Period(date(2026, 9, 1), date(2026, 9, 6), {venue.chain: 502434999})
    calls = []
    def balances(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame(columns=['block_date', 'daily_net'])
    holder_share_inflows(prime, venue, period,
        balance_source=SimpleNamespace(cumulative_balance_timeseries=balances),
        block_resolver=None, price_at_block=None, opening_shares=D(0), closing_shares=D(0))
    assert calls == [dict(chain=venue.chain.value, token=venue.token.address.value,
                          holder=venue.holder_override.value, start=period.start,
                          pin_block=502434999)]
