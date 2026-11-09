"""Grove remaining September items: principal, exempt cash allocation and fee residue."""
import importlib.util
import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest

from settle.compute.prime_agent_revenue import _time_weighted_notional
from settle.domain import Chain, Month, Period
from settle.domain.config import load_prime_by_id
from settle.domain.monthly_pnl import MonthlyPnL
from settle.extract import basin as rpc
from settle.load.cof_attribution import compute_sheet_rows
from settle.load.provenance import render_provenance
from settle.normalize.basin import basin_cash_by_venue, get_basin_idle_usds
from settle.normalize.redemption_settlements import policy_for
from settle.revenue.monthly import _decode
from settle.revenue.verification import canonical
from tests.unit.test_basin_idle import snapshot
from tests.unit.test_monthly_from_revenue import example

ROOT = Path(__file__).resolve().parents[2]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_warehouse_schedule_matches_pinned_funding_and_preserves_august():
    module = script('audit_grove_september_2026')
    evidence = json.loads(module.EVIDENCE.read_text())
    result = module.audit(evidence)['warehouse']
    assert result['net_funding'] == '71250000'
    assert result['closing'] == '375250001'
    assert result['average_principal'] == '349916701'
    assert [row['date'] for row in result['transactions']] == ['2026-09-10', '2026-09-11', '2026-09-12']
    venue = next(v for v in load_prime_by_id('grove').venues if v.id == 'E42')
    assert _time_weighted_notional(venue.notional_principal_usd, Period.from_month(Month(2026, 8))) == D('304000001')


def test_pinned_warehouse_dates_cannot_silently_shift():
    module = script('audit_grove_september_2026')
    evidence = json.loads(module.EVIDENCE.read_text())
    first = next(iter(evidence['ethereum_block_dates'].values()))
    first['date'] = '2026-09-11'
    with pytest.raises(ValueError, match='date is not pinned'):
        module.audit(evidence)


def test_basin_attribution_reuses_dated_owned_and_capped_cash(monkeypatch):
    prime = load_prime_by_id('grove')
    period = Period(date(2026, 8, 31), date(2026, 9, 2), {Chain.ETHEREUM: 200})
    pocket = next(v for v in prime.venues if v.id == 'E41').holder_override.hex
    resolver = Mock()
    resolver.block_at_or_before.side_effect = [101, 102]
    monkeypatch.setattr(rpc, 'ilk_debt', lambda ilk, block: D(80) if block == 101 else D(300))

    def cash(basin, holder, block):
        if basin == prime.basin_idle_usds.basins[0]:
            return snapshot(200 if block == 101 else 80, 1, 4, pocket)
        return snapshot(100, holder='unrepresented-other-pocket')

    monkeypatch.setattr(rpc, 'idle_usds', cash)
    daily = get_basin_idle_usds(prime, period, block_resolver=resolver)
    assert daily.cum_balance.tolist() == [D(0), D(80), D(120)]
    assert daily.iloc[0].idle_by_holder == {}
    for _, row in daily.iterrows():
        assert sum(row.idle_by_holder.values(), D(0)) == row.cum_balance
    allocation = basin_cash_by_venue(prime, period, daily)
    expected = (D(80) * D(50) / D(150) + D(20)) / D(3)
    assert abs(allocation['E41']['idle_exempt'] - expected) < D('1e-24')
    assert set(allocation) == {'E41'}


def reporting_payload(exemption):
    def venue(vid, value, revenue):
        return dict(venue_id=vid, label=vid, value_som=str(value), value_eom=str(value),
                    tw_avg_value_usd=str(value), actual_revenue=str(revenue), revenue=str(revenue))
    return {
        'period': {'start': '2026-09-01', 'end': '2026-09-30', 'n_days': 30},
        'results': {'sky_revenue': '100', 'sky_revenue_gross': '120',
                    'prime_agent_revenue': '900', 'agent_rate': '0'},
        'venue_breakdown': [venue('E41', 100, 0), venue('E42', 1000, 1000)],
        'basin_cash_reporting': {'E41': {'idle_exempt': str(exemption), 'other_owners': '0'}},
    }


@pytest.mark.parametrize('exemption,expected_weighted_value', [(100, 0), (40, 60), (0, 100)])
def test_basin_report_excludes_only_exempt_cash_and_preserves_headlines(exemption, expected_weighted_value):
    prov = reporting_payload(exemption)
    rows, totals = compute_sheet_rows(prov, 'grove')
    basin = next(r for r in rows if r['venue_id'] == 'E41')
    assert basin['avg_value'] == D(expected_weighted_value)
    if expected_weighted_value == 0:
        assert basin['cof_alloc'] == basin['profit_to_grove'] == D(0)
    else:
        assert basin['cof_alloc'] > 0
    assert totals['cof_total'] == totals['sum_p2s'] == D(100)
    assert totals['sum_p2g'] == D(900)
    assert sum(r['cof_alloc'] for r in rows) == D(100)


def test_old_reports_do_not_invent_a_basin_exemption():
    prov = reporting_payload(100)
    del prov['basin_cash_reporting']
    rows, _ = compute_sheet_rows(prov, 'grove')
    assert next(r for r in rows if r['venue_id'] == 'E41')['cof_alloc'] > 0


@pytest.mark.parametrize('exemption', ['-1', 'NaN', 'Infinity'])
def test_invalid_basin_deduction_is_not_published(exemption):
    with pytest.raises(ValueError, match='Invalid Basin'):
        compute_sheet_rows(reporting_payload(exemption), 'grove')


def test_basin_metadata_survives_result_reuse_and_provenance():
    _, pnl, _ = example()
    pnl = replace(pnl, basin_cash_reporting={'E41': {'idle_exempt': D('38.25'), 'other_owners': D('0')}})
    decoded = _decode(MonthlyPnL, canonical(pnl))
    assert decoded.basin_cash_reporting == {'E41': {'idle_exempt': D('38.25'), 'other_owners': D('0')}}
    assert render_provenance(decoded)['basin_cash_reporting'] == {'E41': {'idle_exempt': '38.25', 'other_owners': '0'}}
    legacy = canonical(pnl)
    del legacy['basin_cash_reporting']
    assert _decode(MonthlyPnL, legacy).basin_cash_reporting == {}
    del legacy['sky_revenue']
    with pytest.raises(ValueError, match='schema'):
        _decode(MonthlyPnL, legacy)


def test_missing_basin_days_are_not_silently_averaged():
    prime = load_prime_by_id('grove')
    period = Period.from_month(Month(2026, 9))
    partial = pd.DataFrame([{'block_date': period.start, 'cum_balance': D(1), 'idle_by_holder': {}}])
    with pytest.raises(ValueError, match='Incomplete'):
        basin_cash_by_venue(prime, period, partial)


def test_buidl_fee_replay_only_adds_remaining_cost():
    module = script('reconcile_grove_buidl_fees')
    original_policy = policy_for('grove', 'E10')
    result = module.reconcile()
    assert result['actual_cost'] == '162506.171229'
    assert result['already_recorded_fee_credit'] == '162505.35'
    assert result['additional_credit_unrounded'] == '0.821229'
    assert result['capital_restoration_debit'] == '4001'
    assert result['capital_corrections']['2026-05']['restored_sky_income'] == '3000'
    assert result['capital_corrections']['2026-08']['restored_sky_income'] == '1001'
    assert result['net_sky_adjustment_unrounded'] == '4000.178771'
    assert result['proposed_sky_adj'] == '4000.18'
    assert result['months']['2026-05']['additional_credit'] == '-0.000555'
    small = [r for r in result['settlements'] if r['cash']['date'] == '2026-08-31']
    assert len(small) == 1 and small[0]['revenue_adjustment_usd'] == '-0.821502'
    assert all(r['cash']['date'] <= '2026-08-31' for r in result['settlements'])
    assert result['unsettled_at_cutoff'][0]['shares'] == '24999000'
    assert policy_for('grove', 'E10') == original_policy


def test_buidl_duplicate_evidence_and_future_cash_do_not_repeat_credit():
    module = script('reconcile_grove_buidl_fees')
    events = json.loads(module.EVENTS.read_text())['events']
    prior = json.loads(module.PRIOR.read_text())['grove']
    expected = module.reconcile_events(events, prior)
    assert module.reconcile_events(events + events, prior) == expected
    past = [e for e in events if e['date'] <= '2026-08-31']
    assert module.reconcile_events(past, prior) == expected
    malformed = deepcopy(prior)
    malformed['buidl_realized_fee_credit_usd'] = '165013.90'  # Includes CoF, which is not a fee credit.
    with pytest.raises(ValueError, match='do not reconcile'):
        module.reconcile_events(events, malformed)


@pytest.mark.parametrize('exempt,expected_base', [(50, 0), (20, 30)])
def test_other_basin_owners_cash_is_neither_grove_capital_nor_a_debt_credit(exempt, expected_base):
    prov = reporting_payload(exempt)
    prov['basin_cash_reporting']['E41']['other_owners'] = '50'
    rows, totals = compute_sheet_rows(prov, 'grove')
    row = next(row for row in rows if row['venue_id'] == 'E41')
    assert row['avg_value'] == D(expected_base)
    assert row['deduction_avg'] == D(exempt)
    assert totals['cof_total'] == totals['sum_p2s'] == D(100)
    assert totals['sum_p2g'] == D(900)


def test_historical_buidl_cash_cannot_be_reassigned_or_ignored():
    module = script('reconcile_grove_buidl_fees')
    events = json.loads(module.EVENTS.read_text())['events']
    prior = json.loads(module.PRIOR.read_text())['grove']
    bad = deepcopy(events)
    bad[0]['recipient'] = '0x' + '12' * 20
    with pytest.raises(ValueError, match='counterparty'):
        module.reconcile_events(bad, prior)
    dust = next(deepcopy(e) for e in events if e['kind'] == 'cash' and D(e['amount']) < D(1))
    dust.update(date='2026-08-30', amount='0.001', tx='0x'+'12'*32, block=25870000, log_index=0)
    with pytest.raises(ValueError, match='Unmatched historical'):
        module.reconcile_events([*events, dust], prior)


def test_buidl_capital_debit_matches_the_production_capital_repair():
    from settle.normalize.redemption_settlements import (
        match_redemptions,
        restore_redemption_capital,
    )
    module = script('reconcile_grove_buidl_fees')
    events = json.loads(module.EVENTS.read_text())['events']
    prime = load_prime_by_id('grove')
    venue = next(v for v in prime.venues if v.id == 'E10')
    policy = policy_for('grove', 'E10')
    policy['history_start'] = policy['cof_history_start']
    for month, expected in [('2026-05', D(3000)), ('2026-08', D(1001))]:
        period = Period.from_month(Month.parse(month), {Chain.ETHEREUM: module.CLOSING_BLOCK})
        baseline = module.published_baseline(month)
        ledger = match_redemptions(venue, period, policy, events, track_pending=True)
        original = baseline['capital_inflow']
        flows = pd.DataFrame([{'block_date': period.end, 'daily_inflow': original, 'cum_inflow': original}])
        fixed = restore_redemption_capital(flows, ledger)
        assert original - fixed.daily_inflow.sum() == expected


def test_already_restored_capital_is_not_debited_again(monkeypatch):
    module = script('reconcile_grove_buidl_fees')
    original = module.published_baseline

    def corrected(month):
        row = original(month)
        delta = D(3000) if month == '2026-05' else D(1001)
        return {**row, 'capital_inflow': row['capital_inflow'] - delta, 'income': row['income'] + delta}

    monkeypatch.setattr(module, 'published_baseline', corrected)
    result = module.reconcile()
    assert result['capital_restoration_debit'] == '0'
    assert result['proposed_sky_adj'] == '-0.82'


def test_changed_frozen_income_baseline_stops_reconciliation(tmp_path, monkeypatch):
    module = script('reconcile_grove_buidl_fees')
    folder = tmp_path / 'settlements/grove/2026-05'
    folder.mkdir(parents=True)
    (folder / 'summary.md').write_text('changed historical baseline')
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='baseline changed'):
        module.published_baseline('2026-05')


def test_active_basin_empty_data_is_missing_not_zero():
    prime = load_prime_by_id('grove')
    with pytest.raises(ValueError, match='Incomplete daily Basin attribution'):
        basin_cash_by_venue(prime, Period.from_month(Month(2026, 9)), pd.DataFrame())
    assert basin_cash_by_venue(prime, Period.from_month(Month(2026, 8)), pd.DataFrame()) == {}
    assert basin_cash_by_venue(load_prime_by_id('spark'), Period.from_month(Month(2026, 9)), pd.DataFrame()) == {}


@pytest.mark.parametrize('non_exempt', [D(0), D('.01')])
def test_basin_precision_dust_does_not_create_a_charge_but_real_principal_does(non_exempt):
    from datetime import timedelta
    prime = load_prime_by_id('grove')
    period = Period.from_month(Month(2026, 9))
    pocket = next(v for v in prime.venues if v.id == 'E41').holder_override.hex
    raw = D('100000000')
    owned = raw / D(3)
    idle = owned - non_exempt
    daily = pd.DataFrame([
        {'block_date': period.start + timedelta(days=i), 'cum_balance': idle,
         'idle_by_holder': {pocket: idle}, 'other_owners_by_holder': {pocket: raw - owned}}
        for i in range(period.n_days)
    ])
    prov = reporting_payload(0)
    prov['basin_cash_reporting'] = basin_cash_by_venue(prime, period, daily)
    prov['venue_breakdown'][0].update(value_som=str(raw), value_eom=str(raw), tw_avg_value_usd=str(raw))
    rows, totals = compute_sheet_rows(prov, 'grove')
    basin = next(row for row in rows if row['venue_id'] == 'E41')
    assert abs(basin['avg_value'] - non_exempt) < D('1e-18')
    if non_exempt == 0:
        assert basin['avg_value'] == basin['cof_alloc'] == basin['profit_to_grove'] == D(0)
    else:
        assert basin['cof_alloc'] > 0
    assert abs(totals['sum_p2s'] - D(100)) < D('1e-24')


def test_measured_2026_summary_separates_configured_and_further_adjustments():
    module = script('summarize_grove_2026_reconciliation')
    result = module.summarize()
    assert result['whole_year_total'] is None
    assert result['already_configured_cof_total'] == '78530.06'
    assert result['further_measured_total'] == '40111.54'
    assert result['venues']['E10']['further_measured_not_booked'] == '-4000.18'
    assert result['venues']['E9']['further_measured_not_booked'] == '35889.68'
    for vid in ['E41', 'E42', 'E8']:
        assert result['venues'][vid]['further_measured_not_booked'] == '0.00'
    assert sum(D(row['already_configured_in_october_bridge']) for row in result['venues'].values()) == D('78530.06')
    assert sum(D(row['further_measured_not_booked']) for row in result['venues'].values()) == D('40111.54')


def test_venue_summary_does_not_invent_a_nonlinear_cof_split():
    module = script('summarize_grove_2026_reconciliation')
    document = json.loads((ROOT / 'reconciliation/2026-10/sde_inflight_cof.json').read_text())
    joint = next(row for row in document['daily'] if len(row['deduction_changes_by_venue']) > 1)
    joint['corrected_utilized'] = '900000000'
    joint['deduction_changes_by_venue']['E9'] = str(
        D(joint['frozen_utilized']) - D('900000000') - D(joint['deduction_changes_by_venue']['E10']))
    with pytest.raises(ValueError, match='nonlinear attribution'):
        module.redemption_by_venue(document)


@pytest.mark.parametrize('all_owned', [True, False])
def test_large_basin_share_counts_never_create_negative_other_owner_cash(monkeypatch, all_owned):
    prime = load_prime_by_id('grove')
    pocket = next(v for v in prime.venues if v.id == 'E41').holder_override.hex
    raw = 70078983710674800778744727006
    total = 457119419572939049668858287274274708
    shares = total if all_owned else total - 1
    period = Period(date(2026, 9, 1), date(2026, 9, 1), {Chain.ETHEREUM: 200})
    monkeypatch.setattr(rpc, 'ilk_debt', lambda *args: D('100000000000'))

    def cash(basin, *args):
        if basin == prime.basin_idle_usds.basins[0]:
            return {'balances': {pocket: raw}, 'shares': shares, 'total_shares': total, 'pocket': pocket}
        return snapshot(0, holder='unused')

    monkeypatch.setattr(rpc, 'idle_usds', cash)
    daily = get_basin_idle_usds(prime, period, block_resolver=Mock())
    other = daily.iloc[0]['other_owners_by_holder'][pocket]
    assert other == 0 if all_owned else D(0) < other < D('1e-20')
    assert basin_cash_by_venue(prime, period, daily)['E41']['other_owners'] == other


def test_joint_cof_split_rejects_zero_floor_crossing():
    module = script('summarize_grove_2026_reconciliation')
    document = {'daily': [{'deduction_changes_by_venue': {'E9': '10', 'E10': '10'},
                           'credit_to_grove': '1', 'frozen_charge': '1', 'corrected_charge': '0',
                           'frozen_utilized': '10', 'corrected_utilized': '-10'}]}
    with pytest.raises(ValueError, match='nonlinear attribution'):
        module.redemption_by_venue(document)


def test_cof_split_rejects_inconsistent_principal_changes():
    module = script('summarize_grove_2026_reconciliation')
    document = json.loads((ROOT / 'reconciliation/2026-10/sde_inflight_cof.json').read_text())
    document['daily'][0]['deduction_changes_by_venue'] = {'E9': '1'}
    with pytest.raises(ValueError, match='principal correction'):
        module.redemption_by_venue(document)


def test_venue_credit_offsetting_errors_cannot_hide_behind_a_correct_total():
    module = script('summarize_grove_2026_reconciliation')
    document = json.loads((ROOT / 'reconciliation/2026-10/sde_inflight_cof.json').read_text())
    first = next(row for row in document['daily'] if set(row['deduction_changes_by_venue']) == {'E9'})
    second = next(row for row in document['daily'] if set(row['deduction_changes_by_venue']) == {'E10'})
    first['credit_to_grove'] = str(D(first['credit_to_grove']) + 1)
    second['credit_to_grove'] = str(D(second['credit_to_grove']) - 1)
    with pytest.raises(ValueError, match='recorded charge difference'):
        module.redemption_by_venue(document)


def test_buidl_prior_credit_ledger_cannot_drift_silently(tmp_path, monkeypatch):
    module = script('reconcile_grove_buidl_fees')
    path = tmp_path / 'changed-prior.json'
    prior = json.loads(module.PRIOR.read_text())
    prior['grove']['buidl_realized_fee_credit_usd'] = '162504.35'
    prior['grove']['buidl_realized_fee_credit_by_month_usd']['2026-05'] = '137503.98'
    path.write_text(json.dumps(prior))
    monkeypatch.setattr(module, 'PRIOR', path)
    with pytest.raises(ValueError, match='Prior fee-credit ledger changed'):
        module.reconcile()
