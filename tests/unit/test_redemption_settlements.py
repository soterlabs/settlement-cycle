"""Cash-date realization, independent of request age and monthly boundaries."""
import copy
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest

from settle.compute.prime_agent_revenue import VenueRevenueInputs, compute_venue_revenue
from settle.domain.config import load_prime_by_id
from settle.domain.period import Period
from settle.domain.primes import Chain
from settle.domain.sde import SDEEntry
from settle.normalize import redemption_settlements as module
from settle.revenue.monthly import _decode
from settle.revenue.verification import canonical

ROOT = Path(__file__).resolve().parents[2]


def setup():
    prime = load_prime_by_id('grove', config_dir=ROOT / 'config')
    venue = next(v for v in prime.venues if v.id == 'E10')
    policy = module.policy_for('grove', 'E10')
    events = json.loads((ROOT / 'tests/fixtures/buidl_september_boundary.json').read_text())['events']
    return prime, venue, policy, events


def period(start='2026-09-01', end='2026-09-30', pin=30000000):
    return Period(date.fromisoformat(start), date.fromisoformat(end), {Chain.ETHEREUM: pin})


def test_boundary_receipt_books_full_original_carrying_value_once():
    _, venue, policy, events = setup()
    result = module.match_redemptions(venue, period(), policy, events)
    assert result.revenue_adjustment == D('-12499.846781')
    assert result.revenue_adjustment.quantize(D('.01')) == D('-12499.85')
    assert len(result.settlements) == 1
    match = result.settlements[0]
    assert match['carrying_value_usd'] == '24999000'
    assert match['cash_usd'] == '24986500.153219'
    assert match['requests'][0]['date'] == '2026-08-31'
    assert match['cash']['date'] == '2026-09-01'
    assert match['matching'] == 'explicit_event_link'
    assert not result.outstanding
    assert module.match_redemptions(venue, period('2026-10-01', '2026-10-31'), policy, events).revenue_adjustment == 0
    assert module.match_redemptions(venue, period(), policy, events + events) == result


def test_month_end_finalizes_with_outstanding_claim_then_later_cash():
    _, venue, policy, events = setup()
    # Delay the authoritative cash by six weeks; no rolling T+2/T+3 window.
    events[-1] = {**events[-1], 'date': '2026-10-15', 'block': 27000000}
    september = module.match_redemptions(venue, period(), policy, events)
    assert september.revenue_adjustment == 0
    assert len(september.outstanding) == 1
    assert september.outstanding[0]['carrying_value_usd'] == '24999000'
    october = module.match_redemptions(venue, period('2026-10-01', '2026-10-31'), policy, events)
    assert october.revenue_adjustment == D('-12499.846781')
    assert not october.outstanding


def test_post_haircut_cash_books_only_variance():
    _, venue, policy, events = setup()
    events = [copy.deepcopy(events[1]), copy.deepcopy(events[-1])]
    events[0]['date'] = '2026-09-30'
    events[1]['date'] = '2026-10-05'
    result = module.match_redemptions(venue, period('2026-10-01', '2026-10-31'), policy, events)
    assert result.settlements[0]['carrying_value_usd'] == '24986500.5000'
    assert result.revenue_adjustment == D('-0.346781')


def test_historical_period_never_reads_or_changes_results(monkeypatch):
    prime, venue, _, _ = setup()
    def forbidden(*args, **kwargs):
        pytest.fail('Historical period must not scan redemption logs')
    monkeypatch.setattr(module, 'window_logs', forbidden)
    for month in range(1, 9):
        assert module.redemption_settlements(prime, venue, period(f'2026-{month:02}-01', f'2026-{month:02}-28')) == module.RedemptionLedger()
    assert module.redemption_settlements(prime, next(v for v in prime.venues if v.id == 'E1'), period()) == module.RedemptionLedger()


def test_payout_matching_has_no_time_limit_or_four_request_limit():
    _, venue, policy, events = setup()
    policy = {**policy, 'links': {}}
    request = events[1]
    requests = [{**request, 'tx': f'0x{i:064x}', 'amount': '10000', 'date': '2026-09-01', 'block': 25877252 + i} for i in range(1, 7)]
    cash = {**events[-1], 'amount': '59970', 'date': '2026-11-01', 'block': 28000000}
    result = module.match_redemptions(venue, period('2026-11-01', '2026-11-30'), policy, [*requests, cash])
    assert result.revenue_adjustment == 0
    assert len(result.settlements[0]['requests']) == 6


def test_ambiguous_cash_needs_explicit_link_and_never_fifo_guesses():
    _, venue, policy, events = setup()
    policy = {**policy, 'links': {}}
    a = events[1]
    b = {**a, 'tx': '0x'+'a'*64, 'block': a['block']+1}
    with pytest.raises(ValueError, match='Ambiguous'):
        module.match_redemptions(venue, period(), policy, [a, b, events[-1]])


def test_unknown_material_cash_and_bad_explicit_links_fail():
    _, venue, policy, events = setup()
    with pytest.raises(ValueError, match='missing, already settled, or later'):
        module.match_redemptions(venue, period(), policy, [events[-1]])
    policy = {**policy, 'links': {}}
    with pytest.raises(ValueError, match='Unmatched redemption cash'):
        module.match_redemptions(venue, period(), policy, [{**events[-1], 'amount': '12000'}])


def test_duplicate_conflicting_log_rejected_and_future_block_excluded():
    _, venue, policy, events = setup()
    with pytest.raises(ValueError, match='Conflicting duplicate'):
        module.match_redemptions(venue, period(), policy, [*events, {**events[-1], 'amount': '1'}])
    result = module.match_redemptions(venue, period(pin=25880000), policy, events)
    assert result.revenue_adjustment == 0
    assert len(result.outstanding) == 1


def test_cash_dust_does_not_consume_a_small_test_request():
    _, venue, policy, events = setup()
    policy = {**policy, 'links': {}}
    result = module.match_redemptions(venue, period(), policy,
                                    [{**events[1], 'amount': '1'}, {**events[-1], 'amount': '0.444758'}])
    assert len(result.outstanding) == len(result.unmatched_cash) == 1
    assert result.revenue_adjustment == 0


def test_realization_entirely_to_sky_and_provenance_round_trips():
    _, venue, policy, events = setup()
    ledger = module.match_redemptions(venue, period(), policy, events)
    entry = SDEEntry(prime_id='grove', venue_id='E10', chain='ethereum', kind='fixed',
                     cap_usd=None, pattern=None, start_date=date(2025,10,30), end_date=None,
                     label='BUIDL', source='test')
    inp = VenueRevenueInputs(venue=venue, value_som=D(0), value_eom=D(0),
                             inflow_timeseries=pd.DataFrame(columns=['block_date','daily_inflow','cum_inflow']),
                             sde_entry=entry, redemption_revenue_adjustment=ledger.revenue_adjustment,
                             redemption_settlements=list(ledger.settlements))
    result = compute_venue_revenue(period(), inp)
    assert result.revenue == 0
    assert result.actual_revenue == result.sd_revenue == D('-12499.846781')
    assert result.period_inflow == 0  # USDC receipt remains capital in its own venue.
    assert _decode(type(result), canonical(result)) == result
    with pytest.raises(ValueError, match='revenue override'):
        compute_venue_revenue(period(), replace(inp, venue=replace(venue, fixed_fee_per_capital_event_usd=None), actual_revenue_override=D(0)))


def test_all_september_authoritative_cash_and_daily_cutoffs():
    _, venue, policy, initial = setup()
    live = json.loads((ROOT / 'docs/september-2026-close/buidl-september-ledger-validation.json').read_text())
    keys = ['kind', 'date', 'amount', 'tx', 'block', 'log_index', 'sender', 'recipient', 'token']
    events = {}
    rows = [*initial]
    for settlement in live['ledger']['settlements']:
        rows.extend([settlement['cash'], *settlement['requests']])
    for row in rows:
        events[(row['tx'], row['log_index'])] = {k: row[k] for k in keys}
    result = module.match_redemptions(venue, period(), policy, events.values())
    assert len(result.settlements) == 14
    assert not result.outstanding and not result.unmatched_cash
    assert result.revenue_adjustment == D('-12504.626548')
    assert sum((D(r['revenue_adjustment_usd']) for r in result.settlements[1:]), D(0)) == D('-4.779767')
    for cutoff in ['2026-09-01', '2026-09-10', '2026-09-30']:
        mtd = module.match_redemptions(venue, period(end=cutoff), policy, events.values())
        expected = sum((D(r['revenue_adjustment_usd']) for r in result.settlements if r['cash']['date'] <= cutoff), D(0))
        assert mtd.revenue_adjustment == expected
    assert module.match_redemptions(venue, period('2026-09-22', '2026-09-22'), policy, events.values()).revenue_adjustment == D('-0.420296')


def test_source_uses_only_configured_transfers_and_propagates_failure(monkeypatch):
    prime, venue, _, _ = setup()
    def source(chain, selections, start, pin, end, *, transactions):
        assert chain == 'ethereum'
        assert start == date(2026, 8, 31)  # Fixed opening ledger, including prior month.
        assert end == date(2026, 9, 30) and transactions is True
        assert len(selections) == 2
        assert selections[0]['address'] == [venue.token.address.hex]
        assert selections[1]['address'] == ['0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48']
        raise ConnectionError('Indexer unavailable')
    monkeypatch.setattr(module, 'window_logs', source)
    with pytest.raises(ConnectionError, match='Indexer unavailable'):
        module.redemption_settlements(prime, venue, period())


def test_small_test_redemption_is_capital_not_principal_loss():
    _, venue, policy, events = setup()
    policy = {**policy, 'links': {}}
    request = {**events[0], 'date': '2026-09-21'}
    cash = {**events[2], 'date': '2026-09-21', 'amount': '999.084555'}
    ledger = module.match_redemptions(venue, period(), policy, [request, cash])
    assert ledger.revenue_adjustment == D('-0.415445')
    assert len(ledger.capital_outflows) == 1
    inflows = pd.DataFrame(columns=['block_date', 'daily_inflow', 'cum_inflow'])
    inflows = module.restore_redemption_capital(inflows, ledger)
    assert inflows.iloc[-1]['cum_inflow'] == D('-999.5000')
    entry = SDEEntry(prime_id='grove', venue_id='E10', chain='ethereum', kind='fixed',
                     cap_usd=None, pattern=None, start_date=date(2025,10,30), end_date=None,
                     label='BUIDL', source='test')
    result = compute_venue_revenue(period(), VenueRevenueInputs(
        venue=venue, value_som=D('999.5'), value_eom=D(0), inflow_timeseries=inflows,
        sde_entry=entry, redemption_revenue_adjustment=ledger.revenue_adjustment))
    assert result.actual_revenue == result.sd_revenue == D('-0.415445')
    assert result.revenue == 0
    # The prior-month small request must never modify September capital.
    assert not module.match_redemptions(venue, period(), policy, events).capital_outflows
