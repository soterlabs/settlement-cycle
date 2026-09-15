from dataclasses import replace
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from settle.compute import Sources, compute_monthly_pnl
from settle.compute._helpers import apr_daily, apy_to_apr
from settle.compute.sky_revenue import BASE_RATE_OVER_SSR
from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.extract import rpc

from ..fixtures.mock_sources import (
    MockBalanceSource,
    MockBlockResolver,
    MockConvertToAssetsSource,
    MockDebtSource,
    MockPositionBalanceSource,
    MockSSRSource,
)


def test_partial_calculation_bounds_rates_flows_and_valuation(monkeypatch):
    prime = load_prime_by_id("obex")
    venue = prime.venues[0]
    cutoff = date(2026, 3, 15)
    opening = date(2026, 2, 28)
    checks, price_days, finalized_days = [], [], []

    class Resolver(MockBlockResolver):
        def finalized_block_at_or_before(self, chain, anchor):
            finalized_days.append(anchor.date())
            return self.block_at_or_before(chain, anchor)

        def block_at_or_before(self, chain, anchor):
            self.calls.append((chain, anchor))
            return anchor.date().toordinal()

        def validate_finalized_boundary(self, chain, block, anchor):
            checks.append((chain, block, anchor.date()))

    class Balances(MockBalanceSource):
        def directed_inflow_timeseries(self, chain, token, from_addr, to_addr, start, pin_block):
            self.directed_calls.append((chain, token, from_addr, to_addr, start, pin_block))
            if from_addr != bytes(20):
                return pd.DataFrame(columns=["block_date", "daily_inflow", "cum_inflow"])
            # A later deposit must not enter the as-of calculation.
            rows = [{"block_date": d, "daily_inflow": n, "cum_inflow": cumulative}
                    for d, n, cumulative in [(date(2026, 3, 5), 50, 50),
                                            (date(2026, 3, 20), 999, 1049)]
                    if d.toordinal() <= pin_block]
            return pd.DataFrame(rows)

    class Positions(MockPositionBalanceSource):
        def balance_at(self, chain, token, holder, block):
            self.calls.append((chain, token, holder, block))
            if token != venue.token.address.value:
                return 0
            return (100 if block < date(2026, 3, 5).toordinal() else 150) * 10**6

    class Prices(MockConvertToAssetsSource):
        def convert_to_assets(self, chain, vault, shares, block):
            day = date.fromordinal(block)
            price_days.append(day)
            assert opening <= day <= cutoff
            return 10**6 if day == opening else 10**6 + day.day * 1000

    resolver, balances = Resolver(), Balances()
    monkeypatch.setattr(rpc, "ilk_rate", lambda *args: 10**27)
    sources = Sources(
        block_resolver=resolver, balance=balances, position_balance=Positions(),
        convert_to_assets=Prices(),
        debt=MockDebtSource(pd.DataFrame({"block_date": [opening], "daily_dart": [1000], "cum_debt": [1000]})),
        ssr=MockSSRSource(pd.DataFrame({
            "effective_date": [opening, date(2026, 3, 10), date(2026, 3, 20)],
            "ssr_apy": [Decimal("0.04"), Decimal("0.05"), Decimal("0.90")],
        })),
    )
    result = compute_monthly_pnl(prime, Month(2026, 3), as_of=cutoff, sources=sources)
    assert result.period.start == date(2026, 3, 1)
    assert result.period.end == cutoff
    assert {day for _, _, day in checks} == {opening, cutoff}
    assert set(finalized_days) == {opening, cutoff}
    assert all(block == day.toordinal() for _, block, day in checks)
    assert all(opening <= anchor.date() <= cutoff for _, anchor in resolver.calls)
    assert all(call[-1] == cutoff.toordinal() for call in balances.directed_calls)
    assert set(price_days) == {opening, date(2026, 3, 5), cutoff}
    v = result.venue_breakdown[0]
    assert v.value_som == Decimal(100)
    assert v.value_eom == Decimal("152.25")
    assert v.period_inflow == Decimal("50.25")
    assert v.revenue == Decimal(2)
    expected = Decimal(1000) * (
        apr_daily(apy_to_apr(Decimal("0.04")) + BASE_RATE_OVER_SSR, 9)
        + apr_daily(apy_to_apr(Decimal("0.05")) + BASE_RATE_OVER_SSR, 6)
    )
    assert abs(result.sky_revenue - expected) < Decimal("1e-20")
    assert len(result.sky_revenue_daily) == 15


def test_auto_as_of_rejects_uncertified_resolver_before_extraction():
    prime = replace(load_prime_by_id("obex"), sources={})
    with pytest.raises(ValueError, match="finality-aware"):
        compute_monthly_pnl(prime, Month(2026, 3), as_of=date(2026, 3, 15),
                            sources=Sources(block_resolver=MockBlockResolver()))


def test_as_of_rejects_unfinalized_pins_before_calculating():
    from settle.domain.primes import Chain
    from settle.extract.hypersync import HyperSyncError

    class LaggingResolver(MockBlockResolver):
        def validate_finalized_boundary(self, *args):
            raise HyperSyncError("Cutoff is not finalized")

    with pytest.raises(HyperSyncError, match="not finalized"):
        compute_monthly_pnl(
            load_prime_by_id("obex"), Month(2026, 3), as_of=date(2026, 3, 15),
            sources=Sources(block_resolver=LaggingResolver()),
            pin_blocks_eom={Chain.ETHEREUM: 200}, pin_blocks_som={Chain.ETHEREUM: 100},
        )
