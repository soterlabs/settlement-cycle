from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from settle.compute import Sources, compute_monthly_pnl
from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.domain.primes import Chain
from settle.extract import rpc
from settle.load.provenance import render_provenance
from settle.normalize.prices import UnsupportedPricingError
from tests.fixtures.mock_sources import (
    MockBalanceSource,
    MockBlockResolver,
    MockConvertToAssetsSource,
    MockDebtSource,
    MockPositionBalanceSource,
    MockSSRSource,
)


def calculate(monkeypatch, price_at, *, empty=False):
    venue = next(v for v in load_prime_by_id('spark').venues if v.id == 'S58')
    prime = replace(load_prime_by_id('obex'), venues=[venue])
    monkeypatch.setattr(rpc, 'total_assets_of',
                        lambda chain, vault, block: 0 if empty else (2 if block == 100 else 3)*10**18)
    sources = Sources(
        block_resolver=MockBlockResolver(default=200),
        debt=MockDebtSource(pd.DataFrame({'block_date': [date(2025, 1, 1)], 'daily_dart': [0], 'cum_debt': [0]})),
        balance=MockBalanceSource(),
        ssr=MockSSRSource(pd.DataFrame({'effective_date': [date(2026, 3, 1)], 'ssr_apy': [0]})),
        position_balance=MockPositionBalanceSource(raw_balance=0),
        convert_to_assets=MockConvertToAssetsSource(raw_assets=10**6),
        nav_oracle_resolver=lambda kind: SimpleNamespace(nav_at=price_at),
    )
    return compute_monthly_pnl(prime, Month(2026, 3), sources=sources,
                               pin_blocks_som={Chain.ETHEREUM: 100},
                               pin_blocks_eom={Chain.ETHEREUM: 200})


def test_speth_is_reported_in_usd_at_each_boundary_and_excluded_from_msc(monkeypatch):
    calls = []
    def quote(chain, feed, block):
        calls.append((chain, feed.hex(), block))
        return Decimal(2000 if block == 100 else 3000)
    pnl = calculate(monkeypatch, quote)
    assert pnl.venue_breakdown == []
    assert pnl.prime_agent_revenue == pnl.sky_revenue == pnl.monthly_pnl == 0
    row, = pnl.reporting_only_breakdown
    assert row.venue_id == 'S58'
    assert (row.value_som, row.value_eom) == (4000, 9000)
    assert row.revenue == 0
    assert not row.settlement_enabled and row.cof_excluded
    assert [c[2] for c in calls] == [100, 200]
    assert all(c[:2] == ('ethereum', '5f4ec3df9cbd43714fe2740f5e3616155c5b8419') for c in calls)
    assert render_provenance(pnl)['reporting_only_breakdown'][0]['value_eom'] == '9000'


def test_nonzero_speth_requires_a_valid_price(monkeypatch):
    def unavailable(*args):
        raise ValueError('price unavailable')
    with pytest.raises(UnsupportedPricingError, match='All NAV oracles failed'):
        calculate(monkeypatch, unavailable)


def test_empty_speth_does_not_require_a_price_before_launch(monkeypatch):
    def forbidden(*args):
        pytest.fail('No price is needed for zero assets')
    assert calculate(monkeypatch, forbidden, empty=True).reporting_only_breakdown == []
