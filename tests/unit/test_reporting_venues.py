from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest
import yaml
from fastapi.testclient import TestClient

from settle.api.app import create_app
from settle.compute import Sources, compute_monthly_pnl
from settle.domain.config import _settlement_enabled, load_prime_by_id
from settle.domain.period import Month
from settle.domain.primes import Chain
from settle.load.provenance import render_provenance
from settle.load.summary import render_summary
from tests.fixtures.mock_sources import (
    MockBalanceSource,
    MockBlockResolver,
    MockConvertToAssetsSource,
    MockDebtSource,
    MockPositionBalanceSource,
    MockSSRSource,
)


def test_all_configured_venues_have_explicit_boolean():
    for prime in ('spark', 'grove', 'obex', 'osero', 'keel', 'skybase'):
        raw = yaml.safe_load((Path(__file__).resolve().parents[2] / 'config' / f'{prime}.yaml').read_text())
        for venue in raw.get('venues', []):
            assert type(venue['settlement_enabled']) is bool, (prime, venue['id'])
        load_prime_by_id(prime)


def test_legacy_flag_is_supported_and_conflicts_are_rejected():
    assert not _settlement_enabled({'display_only': True})
    assert _settlement_enabled({'settlement_enabled': True, 'display_only': False})
    for cfg in ({'settlement_enabled': 'false'}, {'display_only': 1},
                {'settlement_enabled': True, 'display_only': True}):
        with pytest.raises(ValueError):
            _settlement_enabled(cfg)


def test_reporting_revenue_is_computed_but_does_not_change_msc(monkeypatch):
    # Isolate the orchestration with a flat 100-share 4626 position gaining $5.
    prime = load_prime_by_id('obex')
    venue = replace(prime.venues[0], event_source='dune')
    prime = replace(prime, venues=[venue])
    class Prices(MockConvertToAssetsSource):
        def convert_to_assets(self, chain, vault, shares, block):
            return 1_000_000 if block == 100 else 1_050_000
    sources = Sources(
        block_resolver=MockBlockResolver(default=200),
        debt=MockDebtSource(pd.DataFrame({'block_date': [date(2025, 1, 1)], 'daily_dart': [0], 'cum_debt': [0]})),
        balance=MockBalanceSource(), ssr=MockSSRSource(pd.DataFrame({'effective_date': [date(2025, 1, 1)], 'ssr_apy': [0.04]})),
        position_balance=MockPositionBalanceSource(raw_balance=100_000_000), convert_to_assets=Prices(),
    )
    # No treasury balances; only this vault holds shares.
    monkeypatch.setattr(sources.position_balance, 'balance_at', lambda chain, token, holder, block: 100_000_000 if token == venue.token.address.value else 0)
    kwargs = dict(sources=sources, pin_blocks_som={Chain.ETHEREUM: 100}, pin_blocks_eom={Chain.ETHEREUM: 200})
    settled = compute_monthly_pnl(prime, Month(2026, 3), **kwargs)
    reported = compute_monthly_pnl(replace(prime, venues=[replace(venue, settlement_enabled=False)]), Month(2026, 3), **kwargs)
    assert settled.prime_agent_revenue == 5
    assert reported.prime_agent_revenue == 0
    assert reported.sky_revenue == settled.sky_revenue
    assert reported.agent_rate == settled.agent_rate
    assert reported.venue_breakdown == []
    assert reported.reporting_only_breakdown[0].revenue == 5
    assert not reported.reporting_only_breakdown[0].settlement_enabled
    prov = render_provenance(reported)
    assert D(prov['results']['reporting_only_revenue']) == 5
    assert D(prov['reporting_only_breakdown'][0]['revenue']) == 5
    assert 'Reporting-only holdings' in render_summary(prov)
    assert 'Revenue | Receipts' in render_summary(prov)


def test_api_inventory_has_explicit_flags_without_provider_calls(monkeypatch):
    monkeypatch.setattr('requests.Session.send', lambda *a, **kw: pytest.fail('Network call in API'))
    client = TestClient(create_app(reader=object()))
    response = client.get('/v1/venues/grove')
    assert response.status_code == 200
    venues = {v['venue_id']: v for v in response.json()['venues']}
    assert not venues['E25']['settlement_enabled']
    assert venues['E1']['settlement_enabled']
    assert client.get('/v1/venues/unknown').status_code == 404


@pytest.mark.parametrize('activate_usdg', [False, True])
def test_robinhood_reporting_chain_is_pinned_only_after_launch(activate_usdg):
    prime = load_prime_by_id('spark')
    prime = replace(prime, venues=[replace(v, skip=not activate_usdg) if v.id == 'S69' else v
                                   for v in prime.venues])
    assert Chain.ROBINHOOD not in prime.chains_at(date(2026, 1, 31))
    assert Chain.ROBINHOOD not in prime.chains_at(date(2026, 6, 18))
    assert Chain.ROBINHOOD in prime.chains_at(date(2026, 6, 19))
    assert Chain.ROBINHOOD in prime.chains_at(date(2026, 6, 30))


def test_workbook_reports_income_and_prepaid_balance():
    from openpyxl import Workbook

    from scripts.build_settlement_xlsx import _write_reporting_holdings
    workbook = Workbook()
    row = {'venue_id': 'S67', 'label': 'RLUSD incentives', 'value_som': '0', 'value_eom': '-55',
           'revenue': '45', 'incentive_accrual': {'period_receipts': '100'}}
    _write_reporting_holdings(workbook.active, {'reporting_only_breakdown': [row]}, {'venues': []})
    values = list(workbook.active.values)
    data = next(v for v in values if v[0] == 'S67')
    assert workbook.active.title == 'Reporting-only holdings'
    assert data[6] == -55
    assert data[9:11] == (45, 100)
