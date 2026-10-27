"""Real request/payment histories must affect daily CoF, including mid-month gaps."""
import json
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest

from settle.compute.monthly_pnl import _sde_asset_value_timeseries
from settle.domain.config import load_prime_by_id
from settle.domain.period import Month, Period
from settle.domain.sde import InFlightRedemption
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize import redemption_settlements as redemption
from settle.normalize.sources.hypersync_balances import _addr_topic

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def buidl():
    prime = load_prime_by_id('grove', config_dir=ROOT / 'config')
    venue = next(v for v in prime.venues if v.id == 'E10')
    events = json.loads((ROOT / 'tests/fixtures/sde_inflight/buidl_events.json').read_text())['events']
    return prime, venue, events


def test_historical_windows_and_weekend_are_discovered_without_date_configuration(buidl):
    _, venue, events = buidl
    policy = redemption.policy_for('grove', 'E10')
    policy['history_start'] = policy['cof_history_start']
    expected = {
        5: {'2026-05-18': D('24987500'), '2026-05-19': D('24987500')},
        8: {'2026-08-24': D('49975000'), '2026-08-31': D('24986500.50')},
        9: {**{f'2026-09-{d:02}': D('24987500') for d in range(4, 8)},
            '2026-09-16': D('199900000'), '2026-09-21': D('24986500.50')},
    }
    for month, pending in expected.items():
        period = Period.from_month(Month(2026, month), {venue.chain: 30000000})
        result = redemption.match_redemptions(venue, period, policy, events, track_pending=True)
        actual = {str(r['block_date']): r['pending_value_usd'] for r in result.pending_daily if r['pending_value_usd']}
        assert actual == pending
        assert result.revenue_adjustment == (D('-12504.626548') if month == 9 else 0)
        assert all(D(r['amount']) < 5 for r in result.unmatched_cash)  # visible issuer dust
        # Duplicate ingestion and out-of-order retrieval cannot double-count.
        assert redemption.match_redemptions(venue, period, policy, list(reversed(events + events)), track_pending=True) == result


def test_future_cash_cannot_erase_pending_value_at_daily_cutoff(buidl):
    _, venue, events = buidl
    policy = redemption.policy_for('grove', 'E10')
    policy['history_start'] = policy['cof_history_start']
    period = Period(date(2026, 9, 4), date(2026, 9, 7), {venue.chain: 30000000})
    with_future = redemption.match_redemptions(venue, period, policy, events, track_pending=True)
    without_future = redemption.match_redemptions(venue, period, policy,
        [e for e in events if e['date'] <= '2026-09-07'], track_pending=True)
    assert with_future == without_future
    assert {r['pending_value_usd'] for r in with_future.pending_daily} == {D('24987500')}


def test_real_ledger_feeds_sde_deduction_and_serializable_daily_diagnostics(buidl, monkeypatch):
    prime, venue, events = buidl
    period = Period(date(2026, 9, 4), date(2026, 9, 8), {venue.chain: 30000000})
    rows = []
    for e in events:
        rows.append(LogRow(e['block'], e['log_index'],
            int(pd.Timestamp(e['date'], tz='UTC').timestamp()), e['token'], TRANSFER_TOPIC0,
            _addr_topic(bytes.fromhex(e['sender'][2:])), _addr_topic(bytes.fromhex(e['recipient'][2:])),
            None, '0x' + format(int(D(e['amount']) * 10**6), '064x'), e['tx']))
    def logs(chain, selections, start, pin, end, *, transactions):
        assert start == date(2025, 5, 14)  # fixed full history, not rolling T+N
        assert len(selections) == 2 and transactions is True
        return rows
    monkeypatch.setattr(redemption, 'window_logs', logs)
    class Balances:
        def cumulative_balance_timeseries(self, **kwargs):
            return pd.DataFrame({'block_date': [prime.start_date], 'cum_balance': [D('100000000')]})
    class Blocks:
        def block_at_or_before(self, *args):
            return 30000000
    result = _sde_asset_value_timeseries(prime, venue, period,
        balance_source=Balances(), block_resolver=Blocks(), nav_at_block=lambda block: D(1))
    assert list(result['cum_value']) == [D('124987500')] * 4 + [D('100000000')]
    assert list(result['pending_value']) == [D('24987500')] * 4 + [D(0)]
    assert list(result['wallet_value']) == [D('100000000')] * 5
    # An old manual correction must never stack on top of automatically found exposure.
    with pytest.raises(ValueError, match='double-count'):
        _sde_asset_value_timeseries(prime, venue, period, balance_source=Balances(),
            block_resolver=Blocks(), nav_at_block=lambda block: D(1),
            in_flight_redemptions=(InFlightRedemption(date(2026,9,4),date(2026,9,8),D('24987500')),))


def test_historical_pending_scan_propagates_transport_failure(buidl, monkeypatch):
    prime, venue, _ = buidl
    def failed(*args, **kwargs):
        raise ConnectionError('history unavailable')
    monkeypatch.setattr(redemption, 'window_logs', failed)
    with pytest.raises(ConnectionError, match='history unavailable'):
        redemption.pending_redemption_values(prime, venue,
            Period.from_month(Month(2026, 5), {venue.chain: 30000000}))


def test_centrifuge_probe_failure_is_not_zero_pending(buidl, monkeypatch):
    from settle.extract import rpc
    from settle.normalize.positions import _centrifuge_in_flight_shares
    prime, _, _ = buidl
    venue = next(v for v in prime.venues if v.id == 'E9')
    def failed(*args, **kwargs):
        raise ConnectionError('archive unavailable')
    monkeypatch.setattr(rpc, 'is_contract_deployed', failed)
    with pytest.raises(RuntimeError, match='deployment probe failed'):
        _centrifuge_in_flight_shares(prime, venue, 25000000)


def test_recorded_centrifuge_requests_match_every_sampled_pending_balance():
    evidence = json.loads((ROOT / 'tests/fixtures/sde_inflight/centrifuge_history.json').read_text())
    for row in evidence['snapshots']:
        expected = sum((D(w['shares']) for w in evidence['windows'][row['asset']]
            if w['request_at'][:10] <= row['date'] < w['cash_at'][:10]), D(0))
        actual = (D(row['pending_raw']) + D(row['claimable_raw'])) / D(10**6)
        assert abs(actual - expected) < D('0.00001')
        if row['asset'] == 'jaaa':
            held = D(row['balance_raw']) * D(row['nav_raw']) / D(10**12)
            assert held > D('325000000')  # January claims never eroded the SDE cap.
    historical = [w for w in evidence['windows']['jtrsy'] if w['days'] and
                  '2026-01-01' <= w['request_at'][:10] < '2026-08-01']
    assert len(historical) == 14 and sum(w['days'] for w in historical) == 21


def test_pending_centrifuge_shares_are_used_even_with_zero_wallet_balance(buidl, monkeypatch):
    from settle.normalize import positions
    prime, _, _ = buidl
    venue = next(v for v in prime.venues if v.id == 'E9')
    class Balances:
        def cumulative_balance_timeseries(self, **kwargs):
            return pd.DataFrame({'block_date': [prime.start_date], 'cum_balance': [D(0)]})
    class Blocks:
        def block_at_or_before(self, *args):
            return 123
    monkeypatch.setattr(positions, '_centrifuge_in_flight_shares', lambda *args: D('22429188.808014'))
    day = date(2026, 8, 31)
    result = _sde_asset_value_timeseries(prime, venue, Period(day, day, {venue.chain: 123}),
        balance_source=Balances(), block_resolver=Blocks(), nav_at_block=lambda block: D('1.114619'))
    assert result.iloc[0]['cum_value'] == D('22429188.808014') * D('1.114619')
    assert result.iloc[0]['wallet_value'] == 0
    assert result.iloc[0]['cum_value'] == result.iloc[0]['pending_value']


def test_reconciliation_preserves_rates_and_prior_credit_without_network(monkeypatch):
    import runpy

    import requests

    def forbidden(*args, **kwargs):
        pytest.fail('Historical reconciliation must use pinned evidence, not fetch or publish')
    monkeypatch.setattr(requests.sessions.Session, 'send', forbidden)
    module = runpy.run_path(str(ROOT / 'scripts/reconcile_sde_inflight.py'))
    actual = module['reconcile']()
    recorded = json.loads((ROOT / 'reconciliation/2026-10/sde_inflight_cof.json').read_text())
    assert actual == recorded
    assert actual['status'] == 'proposal_for_review_not_booked'
    assert D(actual['months']['2026-03']['additional_credit']) < 0  # include JAAA's extra relief
    assert actual['months']['2026-08']['already_recorded_credit'] == '2508.55'
    for month, values in actual['months'].items():
        gross = sum((D(r['credit_to_grove']) for r in actual['daily'] if r['date'].startswith(month)), D(0))
        assert gross == D(values['gross_credit'])
        assert D(values['additional_credit']) == gross - D(values['already_recorded_credit'])
    assert '2026-08-31' not in [r['date'] for r in actual['daily'] if 'E9' in r['deduction_changes_by_venue']]


def test_shared_daily_charge_applies_subsidy_cap_after_combining_pending_claims():
    from settle.compute.sky_revenue import daily_borrow_charge
    # $40 moves from full-rate debt to subsidised debt when both venues'
    # pending claims are restored. A single averaged rate would be wrong.
    before = daily_borrow_charge(D(140), D('.02'), D('.01'), D(100))
    after = daily_borrow_charge(D(80), D('.02'), D('.01'), D(100))
    assert before - after == D('1.0')
    assert daily_borrow_charge(D(-1), D('.02')) == 0


def test_pending_diagnostics_survive_daily_snapshot_reuse_and_workbook_render():
    import runpy

    from openpyxl import Workbook

    from settle.domain.monthly_pnl import SDEDailyBreakdown
    from settle.revenue.monthly import _decode
    from settle.revenue.verification import canonical

    breakdown = SDEDailyBreakdown('E9', 'JTRSY', None, None, None, None, [
        {'block_date': date(2026, 5, 1), 'wallet_value': D('100000000'),
         'pending_value': D('50005659.905719'), 'uncapped_value': D('150005659.905719'),
         'cum_value': D('150005659.905719')},
    ])
    wire = canonical(breakdown)
    assert _decode(SDEDailyBreakdown, wire) == breakdown
    module = runpy.run_path(str(ROOT / 'scripts/build_settlement_xlsx.py'))
    wb = Workbook()
    module['_write_sde_daily'](wb.active, {'sde_daily_breakdown': [wire]})
    values = list(wb.active.values)
    assert any(row[:5] == ('Date', 'Held NAV', 'Pending NAV', 'Economic NAV', 'SDE deduction') for row in values)
    assert any(row[0] == '2026-05-01' and row[2] == float(D('50005659.905719')) for row in values)
