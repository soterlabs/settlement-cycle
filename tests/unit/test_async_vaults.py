"""Issuance, custody and cash settlement must have distinct accounting effects."""
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest

from settle.compute.monthly_pnl import _sde_asset_value_timeseries
from settle.domain import Address, Period
from settle.domain.config import load_prime_by_id
from settle.normalize import async_vaults as av
from settle.normalize import positions

ROOT = Path(__file__).resolve().parents[2]
MANAGER = Address.from_str('0x' + '12' * 20)


def state(shares=0, price=2 * 10**18, pending=0, cancelled=0, *,
          pending_redeem=0, cancelled_redeem=0, cash=0):
    return '0x' + ''.join(f'{n:064x}' for n in
        (shares, cash, price, 10**18, pending, pending_redeem, cancelled, cancelled_redeem, 0, 0))


@pytest.fixture
def vault():
    prime = load_prime_by_id('grove', config_dir=ROOT / 'config')
    return prime, next(v for v in prime.venues if v.id == 'E9')


def rpc_states(monkeypatch, states):
    from settle.extract import rpc
    monkeypatch.setattr(rpc, 'is_contract_deployed', lambda *args: True)
    calls = []
    def call(chain, contract, data, block):
        calls.append((contract, data, block))
        if data == av.SEL_ASYNC_MANAGER:
            return '0x' + MANAGER.value.hex().zfill(64)
        if data.startswith(av.SEL_INVESTMENTS):
            assert contract == MANAGER
            return states[block]
        raise AssertionError(('Unexpected read', data))
    monkeypatch.setattr(rpc, 'eth_call', call)
    return calls


def test_pending_and_cancelled_deposit_cash_do_not_enter_sde(vault, monkeypatch):
    prime, venue = vault
    rpc_states(monkeypatch, {1: state(pending=500_000_000, cancelled=200_000_000)})
    assert av.async_vault_exposure(prime, venue, 1).value(D(2)) == 0
    assert av.issued_deposit(prime, venue, 1).capital_usd == 0


def test_partial_collection_changes_custody_without_changing_economic_value(vault, monkeypatch):
    prime, venue = vault
    rpc_states(monkeypatch, {1: state(100_000_000), 2: state(60_000_000), 3: state()})
    wallet = {1: D(0), 2: D(40), 3: D(100)}
    monkeypatch.setattr(positions, 'get_position_balance', lambda p, v, b, **kw: wallet[b])
    monkeypatch.setattr(positions, 'get_unit_price', lambda *args, **kw: D('2.2'))
    assert [positions.get_position_value(prime, venue, b) for b in wallet] == [D(220)] * 3
    assert av.deposit_capital_adjustment(prime, venue, 1, 2) == D(-80)
    assert av.deposit_capital_adjustment(prime, venue, 2, 3) == D(-120)


def test_month_boundary_recognizes_capital_once_and_preserves_yield(vault, monkeypatch):
    prime, venue = vault
    rpc_states(monkeypatch, {0: state(), 1: state(100_000_000), 2: state()})
    # Issuance in month 1: closing NAV 220 minus issued principal 200 = 20 yield.
    first_inflow = av.deposit_capital_adjustment(prime, venue, 0, 1)
    assert D(220) - first_inflow == 20
    # Collection in month 2: event cash amount 200 is offset by opening basis.
    second_inflow = D(200) + av.deposit_capital_adjustment(prime, venue, 1, 2)
    assert second_inflow == 0
    assert D(230) - D(220) - second_inflow == 10
    # Issuance and full collection in one month retain existing capital totals.
    assert av.deposit_capital_adjustment(prime, venue, 0, 2) == 0


def test_sde_combines_both_directions_before_cap(vault, monkeypatch):
    prime, venue = vault
    rpc_states(monkeypatch, {1: state(100_000_000, pending_redeem=30_000_000)})
    class Balances:
        def cumulative_balance_timeseries(self, **kwargs):
            return pd.DataFrame({'block_date': [prime.start_date], 'cum_balance': [D(10)]})
    class Blocks:
        def block_at_or_before(self, *args):
            return 1
    day = date(2026, 1, 31)
    row = _sde_asset_value_timeseries(prime, venue, Period(day, day, {venue.chain: 1}),
        balance_source=Balances(), block_resolver=Blocks(), nav_at_block=lambda b: D(2),
        cap_usd=D(250)).iloc[0]
    assert row['wallet_value'] == 20
    assert row['issued_deposit_value'] == 200
    assert row['pending_redemption_value'] == 60
    assert row['uncapped_value'] == 280
    assert row['cum_value'] == 250


def test_cancelled_redemption_keeps_value_until_wallet_return(vault, monkeypatch):
    prime, venue = vault
    rpc_states(monkeypatch, {
        1: state(pending_redeem=100_000_000),
        2: state(cancelled_redeem=100_000_000),
        3: state(),
    })
    wallet = {1: D(0), 2: D(0), 3: D(100)}
    monkeypatch.setattr(positions, 'get_position_balance', lambda p, v, b, **kw: wallet[b])
    monkeypatch.setattr(positions, 'get_unit_price', lambda *args, **kw: D('1.1'))
    assert [positions.get_position_value(prime, venue, block) for block in wallet] == [D(110)] * 3


def test_priced_redemption_cash_is_independent_of_nav_and_transfer_views(vault, monkeypatch):
    prime, venue = vault
    calls = rpc_states(monkeypatch, {1: state(cash=100_000_000)})
    monkeypatch.setattr(positions, 'get_position_balance', lambda *args, **kwargs: D(0))
    monkeypatch.setattr(positions, 'get_unit_price', lambda *args, **kw: pytest.fail('Cash needs no NAV'))
    assert positions.get_position_value(prime, venue, 1) == 100
    exposure = av.async_vault_exposure(prime, venue, 1)
    assert exposure.value(D('1.1')) == exposure.value(D('0.9')) == 100
    assert {data[:10] for _, data, _ in calls} == {av.SEL_ASYNC_MANAGER, av.SEL_INVESTMENTS}


def test_partial_redemption_cash_and_cancelled_shares_do_not_double_count(vault, monkeypatch):
    prime, venue = vault
    rpc_states(monkeypatch, {
        1: state(pending_redeem=40_000_000, cash=60_000_000),
        2: state(cancelled_redeem=40_000_000, cash=60_000_000),
        3: state(cancelled_redeem=40_000_000, cash=30_000_000),
        4: state(cash=30_000_000),
        5: state(),
    })
    wallet = {1: D(0), 2: D(0), 3: D(0), 4: D(40), 5: D(40)}
    paid_cash = {1: D(0), 2: D(0), 3: D(30), 4: D(30), 5: D(60)}
    monkeypatch.setattr(positions, 'get_position_balance', lambda p, v, b, **kw: wallet[b])
    monkeypatch.setattr(positions, 'get_unit_price', lambda *args, **kw: D('1.1'))
    assert [positions.get_position_value(prime, venue, block) + paid_cash[block]
            for block in wallet] == [D(104)] * 5


def test_fixed_cash_and_cancellation_remain_in_daily_sde_with_empty_wallet(vault, monkeypatch):
    prime, venue = vault
    rpc_states(monkeypatch, {1: state(cash=100_000_000, cancelled_redeem=20_000_000)})
    class Balances:
        def cumulative_balance_timeseries(self, **kwargs):
            return pd.DataFrame({'block_date': [prime.start_date], 'cum_balance': [D(0)]})
    class Blocks:
        def block_at_or_before(self, *args):
            return 1
    day = date(2026, 1, 31)
    row = _sde_asset_value_timeseries(prime, venue, Period(day, day, {venue.chain: 1}),
        balance_source=Balances(), block_resolver=Blocks(), nav_at_block=lambda b: D('1.1'),
        cap_usd=D(110)).iloc[0]
    assert row['uncapped_value'] == row['pending_redemption_value'] == 122
    assert row['cum_value'] == 110


def test_collection_in_new_month_does_not_double_average_holdings_or_capital(vault, monkeypatch):
    from settle.compute.prime_agent_revenue import VenueRevenueInputs, compute_venue_revenue
    prime, venue = vault
    states = {day: state() for day in range(1, 29)}
    states[0] = state(100_000_000, price=10**18)
    rpc_states(monkeypatch, states)
    class Blocks:
        def block_at_or_before(self, chain, anchor):
            return anchor.day
    period = Period(date(2026, 2, 1), date(2026, 2, 28), {venue.chain: 28})
    events = pd.DataFrame({'block_date': [date(2026, 1, 15), date(2026, 2, 1)],
                          'daily_inflow': [D(250), D(100)]})
    flows = av.async_capital_inflow_timeseries(prime, venue, period, events,
        opening_block=0, block_resolver=Blocks())
    inflow = sum(flows.loc[flows['block_date'] >= period.start, 'daily_inflow'], D(0))
    result = compute_venue_revenue(period, VenueRevenueInputs(venue, D(100), D(100), flows,
        erc4626_period_inflow=inflow))
    assert result.tw_avg_value == 100
    assert result.period_inflow == result.actual_revenue == 0


def test_daily_capital_tracks_issuance_and_partial_claims_within_period(vault, monkeypatch):
    from settle.compute.prime_agent_revenue import VenueRevenueInputs, compute_venue_revenue
    prime, venue = vault
    rpc_states(monkeypatch, {0: state(), 1: state(),
        2: state(100_000_000, price=10**18), 3: state(60_000_000, price=10**18), 4: state()})
    class Blocks:
        def block_at_or_before(self, chain, anchor):
            return anchor.day
    period = Period(date(2026, 2, 1), date(2026, 2, 4), {venue.chain: 4})
    events = pd.DataFrame({'block_date': [date(2026, 2, 3), date(2026, 2, 4)],
                          'daily_inflow': [D(40), D(60)]})
    flows = av.async_capital_inflow_timeseries(prime, venue, period, events,
        opening_block=0, block_resolver=Blocks())
    assert list(flows['daily_inflow']) == [D(0), D(100), D(0), D(0)]
    result = compute_venue_revenue(period, VenueRevenueInputs(venue, D(0), D(100), flows,
        erc4626_period_inflow=sum(flows['daily_inflow'], D(0))))
    assert result.actual_revenue == 0
    assert result.tw_avg_value == 75


def test_empty_event_history_still_recognizes_issued_deposit(vault, monkeypatch):
    prime, venue = vault
    rpc_states(monkeypatch, {0: state(), 1: state(100_000_000, price=10**18)})
    class Blocks:
        def block_at_or_before(self, *args):
            return 1
    day = date(2026, 2, 1)
    flows = av.async_capital_inflow_timeseries(prime, venue, Period(day, day, {venue.chain: 1}),
        pd.DataFrame(), opening_block=0, block_resolver=Blocks())
    assert list(flows['daily_inflow']) == [D(100)]


def test_daily_capital_never_reads_state_after_the_closing_pin(vault, monkeypatch):
    prime, venue = vault
    calls = rpc_states(monkeypatch, {0: state(), 1: state(cash=100_000_000)})
    class Blocks:
        def block_at_or_before(self, *args):
            return 2  # Resolver can see a block beyond this report's cutoff.
    day = date(2026, 2, 1)
    flows = av.async_capital_inflow_timeseries(prime, venue, Period(day, day, {venue.chain: 1}),
        pd.DataFrame(), opening_block=0, block_resolver=Blocks())
    assert list(flows['daily_inflow']) == [D(0)]
    class Balances:
        def cumulative_balance_timeseries(self, **kwargs):
            return pd.DataFrame({'block_date': [prime.start_date], 'cum_balance': [D(0)]})
    row = _sde_asset_value_timeseries(prime, venue, Period(day, day, {venue.chain: 1}),
        balance_source=Balances(), block_resolver=Blocks(),
        nav_at_block=lambda b: pytest.fail('Fixed cash needs no NAV')).iloc[0]
    assert row['cum_value'] == 100
    assert {block for _, _, block in calls} == {0, 1}


def test_unfiltered_investment_state_is_used_instead_of_transfer_permission_views(vault, monkeypatch):
    prime, venue = vault
    calls = rpc_states(monkeypatch, {1: state(10_000_000)})
    assert av.issued_deposit(prime, venue, 1) == av.IssuedDeposit(D(10), D(20))
    assert [data[:10] for _, data, _ in calls] == [av.SEL_ASYNC_MANAGER, av.SEL_INVESTMENTS]


def test_holder_override_and_historical_manager_are_resolved(vault, monkeypatch):
    prime, venue = vault
    holder = Address.from_str('0x' + '34' * 20)
    venue = replace(venue, holder_override=holder)
    calls = rpc_states(monkeypatch, {1: state(1_000_000)})
    av.issued_deposit(prime, venue, 1)
    assert calls[-1][1].endswith(holder.value.hex().zfill(64))


@pytest.mark.parametrize('raw', ['0x', '0x00', state(1, price=0), '0x' + 'ff' * 320])
def test_missing_or_malformed_state_stops_financial_computation(vault, monkeypatch, raw):
    prime, venue = vault
    rpc_states(monkeypatch, {1: raw})
    with pytest.raises(RuntimeError, match='refusing to assume zero'):
        av.issued_deposit(prime, venue, 1)


def test_rpc_failure_is_not_treated_as_zero(vault, monkeypatch):
    prime, venue = vault
    from settle.extract import rpc
    def fail(*args):
        raise ConnectionError('archive unavailable')
    monkeypatch.setattr(rpc, 'is_contract_deployed', fail)
    with pytest.raises(RuntimeError, match='refusing to assume zero'):
        av.issued_deposit(prime, venue, 1)


def test_undeployed_and_non_async_vaults_have_no_issued_deposit(vault, monkeypatch):
    prime, venue = vault
    from settle.extract import rpc
    monkeypatch.setattr(rpc, 'is_contract_deployed', lambda *args: False)
    monkeypatch.setattr(rpc, 'eth_call', lambda *args: pytest.fail('Unexpected RPC'))
    assert av.issued_deposit(prime, venue, 1) == av.IssuedDeposit()
    assert av.issued_deposit(prime, replace(venue, centrifuge_vault=None), 1) == av.IssuedDeposit()


def test_jtrsy_uses_the_same_adapter_for_spark_and_grove(vault):
    _, grove_venue = vault
    spark = load_prime_by_id('spark', config_dir=ROOT / 'config')
    spark_venue = next(v for v in spark.venues if v.id == 'S20')
    assert spark_venue.centrifuge_vault == grove_venue.centrifuge_vault
    assert spark_venue.underlying == grove_venue.underlying


def test_cost_basis_uses_asset_decimals_and_integer_rounding():
    assert av.decode_issued_deposit(state(1_000_001, price=1234567890123456789), 6, 18) == (
        av.IssuedDeposit(D('1.000001'), D('1.234569124691346912')))


def test_pinned_historical_state_agrees_with_events_and_cap():
    evidence = json.loads((ROOT / 'tests/fixtures/sde_inflight/async_deposits.json').read_text())
    for row in evidence['snapshots']:
        dep = av.decode_issued_deposit(row['investment_state'], 6, 6)
        assert dep.shares == D(row['max_mint_raw']) / 10**6
        events = evidence['events']['grove_' + row['asset']]
        raw = sum(e['shares_raw'] * (1 if e['type'] == 'DepositClaimable' else -1)
                  for e in events if e['date'] <= row['date'])
        assert abs(dep.shares - D(raw) / 10**6) < D('.00001')
        if row['asset'] == 'jaaa':
            assert D(row['wallet_raw']) / 10**6 * D(row['nav_raw']) / 10**18 > D(325_000_000)
    assert evidence['events']['spark_jaaa'] == evidence['events']['spark_jtrsy'] == []
    for receipt in evidence['mint_receipts']:
        if receipt['asset'] == 'jtrsy':
            assert receipt['mint_transfers']  # physical issuance in the allocation transaction


def test_deposit_replay_uses_frozen_rates_and_keeps_redemption_baseline(monkeypatch):
    import runpy

    import requests

    def forbidden(*args, **kwargs):
        pytest.fail('The reconciliation must be offline')
    monkeypatch.setattr(requests.sessions.Session, 'send', forbidden)
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    module = runpy.run_path(str(ROOT / 'scripts/reconcile_async_deposits.py'))
    result = module['reconcile_deposits']()
    saved = json.loads((ROOT / 'reconciliation/2026-10/async_deposit_cof.json').read_text())
    assert result == saved
    assert result['sky_adj'] == '-35889.68'
    assert result['combined_redemption_and_deposit_credit'] == '114419.74'
    assert len(result['daily']) == 13
    assert result['spark_impact'] == result['jaaa_cof_impact'] == result['buidl_deposit_impact'] == '0'
    march_11 = next(row for row in result['daily'] if row['date'] == '2026-03-11')
    assert D(march_11['baseline_utilized']) > D(10**9)  # JAAA cash-day correction retained


def test_new_diagnostics_round_trip_in_snapshots_and_workbook():
    import runpy

    from openpyxl import Workbook

    from settle.domain.monthly_pnl import SDEDailyBreakdown
    from settle.revenue.monthly import _decode
    from settle.revenue.verification import canonical
    breakdown = SDEDailyBreakdown('E9', 'JTRSY', None, None, None, None, [{
        'block_date': date(2026, 1, 31), 'wallet_value': D(0), 'pending_value': D(220),
        'issued_deposit_value': D(200), 'pending_redemption_value': D(20),
        'uncapped_value': D(220), 'cum_value': D(220)}])
    wire = canonical(breakdown)
    assert _decode(SDEDailyBreakdown, wire) == breakdown
    wb = Workbook()
    module = runpy.run_path(str(ROOT / 'scripts/build_settlement_xlsx.py'))
    module['_write_sde_daily'](wb.active, {'sde_daily_breakdown': [wire]})
    assert any(row[5:] == (200.0, 20.0) for row in wb.active.values)
