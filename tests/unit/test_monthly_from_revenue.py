from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import pytest
import yaml

from settle.compute.sky_revenue import compute_sky_revenue_daily
from settle.domain.config import load_prime_by_id
from settle.domain.monthly_pnl import MonthlyPnL, SDEDailyBreakdown, VenueRevenue
from settle.domain.period import Month, Period
from settle.domain.subsidy import ReferenceRateHistory, SubsidyConfig
from settle.revenue import monthly, store
from settle.revenue.reference_rates import CALENDAR, effective_day
from settle.revenue.verification import canonical, digest

D = Decimal
VERSIONS = store.Versions('code', 'config', 'manual')
MONTH = Month(2026, 9)
TODAY = date(2026, 10, 1)


def example(*, subsidy=False, basin=False):
    prime = replace(load_prime_by_id('grove'),
                    subsidy=SubsidyConfig(enabled=subsidy, ref_rate_kind='sofr'))
    period = Period.from_month(MONTH, {c: 200 for c in prime.chains})
    dates = [period.start + timedelta(days=i) for i in range(period.n_days)]
    # Changing debt crosses the subsidised tranche, then becomes fully idle.
    debt = pd.DataFrame({'block_date': dates,
                         'cum_debt': [D('800000000') if i < 15 else D('1400000000')
                                      for i in range(30)]})
    idle = pd.DataFrame({'block_date': dates,
                         'cum_balance': [D('50000000')]*29 + [D('1500000000')]})
    sde = pd.DataFrame({'block_date': dates, 'cum_value': [D('10000000')]*30})
    ssr = pd.DataFrame({'effective_date': dates, 'ssr_apy': [D('.04')]*15 + [D('.035')]*15})
    provenance = {'manual_input_revision': VERSIONS.inputs}
    history = None
    if subsidy:
        calendar = yaml.safe_load(CALENDAR.read_text())
        effective = sorted({effective_day(d, calendar) for d in dates})
        observations = [{'effective_date': str(d), 'apr': '.038'} for d in effective]
        content = {'calendar_version': digest(calendar),
                   'series': {'sofr': {'source': 'fixture', 'observations': observations}},
                   'carry_forward_dates': {str(d): str(effective_day(d, calendar)) for d in dates
                                           if effective_day(d, calendar) != d}}
        provenance['reference_rates'] = {**content, 'snapshot_id': digest(content),
                                         'coverage_complete': True}
        history = ReferenceRateHistory(pd.DataFrame([
            {'effective_date': d, 'ref_rate_apr': D('.038')} for d in effective]), 'sofr')
    basin_idle = pd.DataFrame({"block_date": dates, "cum_balance": [D("10000000")]*30,
                               "ilk_debt": [D("20000000")]*30}) if basin else None
    interest, daily, summary = compute_sky_revenue_daily(
        period, debt, idle, ssr, subsidy_config=prime.subsidy,
        ref_rate_history=history, sde_asset_value=sde, basin_idle_usds=basin_idle)
    rows = canonical(daily.to_dict('records'))
    vr = VenueRevenue('E1', 'Test allocation', D('100'), D('200'), D('50'), D('40'),
                      actual_revenue=D('50'), sd_revenue=D('10'),
                      susds_spread_reimbursement=D('2'))
    sky = interest + D('10') - D('2')
    pnl = MonthlyPnL(prime.id, MONTH, period, sky, D('3'), D('40'), D('43') - sky,
                     [vr], {c: 100 for c in prime.chains}, sde_revenue=D('10'),
                     susds_spread_reimbursement=D('2'), sky_revenue_daily=rows,
                     sky_revenue_gross=sum(daily['daily_sky_rev_gross']), subsidy_summary=summary,
                     sde_daily_breakdown=[SDEDailyBreakdown(
                         'E1', 'Test allocation', D('100'), None, None, None,
                         [{'block_date': period.start, 'cum_value': D('100'),
                           'uncapped_value': D('200')}])])
    record = dict(prime=prime.id, cutoff=str(period.end),
                  opening_pins=canonical(pnl.pin_blocks_som), closing_pins=canonical(period.pin_blocks),
                  code_version=VERSIONS.code, configuration_version=VERSIONS.configuration,
                  input_revision='resolved', result=canonical(pnl), input_provenance=provenance)
    rehash(record)
    return prime, pnl, record


def rehash(record):
    record['result_hash'] = digest(record['result'])
    record['revision_id'] = digest({k: record[k] for k in (
        'prime', 'cutoff', 'opening_pins', 'closing_pins', 'code_version',
        'configuration_version', 'input_revision')})


@pytest.mark.parametrize('subsidy', [False, True])
def test_reuses_supply_recalculates_interest_and_keeps_sky_claim(subsidy, monkeypatch):
    prime, expected, record = example(subsidy=subsidy)
    monkeypatch.setattr(monthly, 'compute_gar', lambda *_: (D('7'), 'monthly consolidation'))
    result, sources = monthly.finalize(record, prime, MONTH, VERSIONS, today=TODAY)
    assert result.venue_breakdown == expected.venue_breakdown
    assert result.sky_revenue == expected.sky_revenue
    assert result.sky_revenue_daily == expected.sky_revenue_daily
    assert result.sde_daily_breakdown == expected.sde_daily_breakdown
    assert result.monthly_pnl == expected.monthly_pnl + D('7')
    assert D(sources['borrowing_costs_recalculated_usd']) == expected.sky_revenue - D('8')
    from settle.load.provenance import render_provenance
    assert render_provenance(result, sources=sources)['sources']['daily_revenue_revision'] == record['revision_id']


@pytest.mark.parametrize('mutation,match', [
    (lambda r: r.update(cutoff='2026-09-29'), 'month-end'),
    (lambda r: r.update(code_version='old'), 'version'),
    (lambda r: r.update(configuration_version='old'), 'version'),
    (lambda r: r['input_provenance'].update(manual_input_revision='old'), 'version'),
    (lambda r: r.update(result_hash='corrupt'), 'hash'),
    (lambda r: r.update(revision_id='corrupt'), 'hash'),
])
def test_rejects_unusable_revision(mutation, match):
    prime, _, record = example()
    mutation(record)
    with pytest.raises(ValueError, match=match):
        monthly.finalize(record, prime, MONTH, VERSIONS, today=TODAY)


@pytest.mark.parametrize('mutation,match', [
    (lambda p: p['sky_revenue_daily'].pop(), 'coverage'),
    (lambda p: p['sky_revenue_daily'].append(p['sky_revenue_daily'][-1]), 'coverage'),
    (lambda p: p['sky_revenue_daily'][0].update(cum_debt='900000000'), 'reconcile'),
    (lambda p: p['sky_revenue_daily'][0].update(daily_sky_rev='0'), 'reconcile'),
    (lambda p: p['sky_revenue_daily'][0].update(ssr_apy=.02), 'reconcile'),
    (lambda p: p['pin_blocks_som'].update(ethereum=999), 'pins'),
    (lambda p: p.pop('sky_revenue_gross'), 'schema'),
    (lambda p: p.update(sky_revenue_gross='NaN'), 'non-finite'),
])
def test_rejects_incomplete_or_inconsistent_financial_inputs(mutation, match):
    prime, _, record = example()
    mutation(record['result'])
    rehash(record)
    with pytest.raises(ValueError, match=match):
        monthly.finalize(record, prime, MONTH, VERSIONS, today=TODAY)


def test_open_month_and_missing_record():
    prime, _, record = example()
    with pytest.raises(ValueError, match='complete in UTC'):
        monthly.finalize(record, prime, MONTH, VERSIONS, today=MONTH.last_day)
    with pytest.raises(ValueError, match='missing'):
        monthly.finalize(None, prime, MONTH, VERSIONS, today=TODAY)


def test_reference_snapshot_required_and_hash_verified():
    prime, _, record = example(subsidy=True)
    corrupted = deepcopy(record)
    corrupted['input_provenance']['reference_rates']['series']['sofr']['observations'][0]['apr'] = '.99'
    with pytest.raises(ValueError, match='reference-rate snapshot'):
        monthly.finalize(corrupted, prime, MONTH, VERSIONS, today=TODAY)
    saved = record['input_provenance']['reference_rates']
    saved['series']['sofr']['observations'].pop(0)
    saved['snapshot_id'] = digest({k: saved[k] for k in (
        'calendar_version', 'series', 'carry_forward_dates')})
    with pytest.raises(ValueError, match='missing reference'):
        monthly.finalize(record, prime, MONTH, VERSIONS, today=TODAY)


def test_cli_refuses_existing_settlement_before_database_access(tmp_path, monkeypatch):
    from settle import cli
    from settle.store import db
    (tmp_path / 'provenance.json').write_text('existing settlement')

    def forbidden():
        raise AssertionError('must refuse before opening database')

    monkeypatch.setattr(db, 'connect', forbidden)
    with pytest.raises(ValueError, match='existing settlements'):
        cli.main(['monthly-from-revenue', '--prime', 'grove', '--month', '2026-09',
                  '--revision', 'explicit', '--output-dir', str(tmp_path)])
    assert (tmp_path / 'provenance.json').read_text() == 'existing settlement'


def test_interest_matches_independent_nominal_apr_arithmetic():
    prime, pnl, record = example()
    rows = deepcopy(pnl.sky_revenue_daily)
    # SSR=0 gives nominal BR=20 bps in September. Non-SDE PSM and AMM/lending
    # balances each deduct a separate amount; negative utilized costs zero.
    expected = D('0')
    for row in rows:
        row.update(ssr_apy=0.0, psm_usds='3000000', curve_idle='4000000',
                   lending_idle='5000000', basin_idle='1000000', basin_ilk_debt='2000000')
        principal = (D(row['cum_debt']) - D(row['alm_usds']) - D(row['sde_av'])
                     - D('13000000'))
        row['utilized'] = str(principal)
        charge = max(D('0'), principal) * (D('.002') / 365)
        row['daily_sky_rev'] = str(charge)
        row['daily_sky_rev_gross'] = str(D(row['cum_debt']) * (D('.002') / 365))
        expected += charge
    sky = expected + pnl.sde_revenue - pnl.susds_spread_reimbursement
    changed = replace(pnl, sky_revenue_daily=rows, sky_revenue=sky,
                      monthly_pnl=pnl.prime_agent_total_revenue - sky,
                      sky_revenue_gross=sum(D(r['daily_sky_rev_gross']) for r in rows))
    assert monthly.validate_interest(changed, prime, record['input_provenance']) == expected


@pytest.mark.parametrize('failure', ['error', 'timeout', 'no_file'])
def test_cli_reports_incomplete_finalization_on_renderer_failure(
        tmp_path, monkeypatch, capsys, failure):
    import subprocess
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    from settle import cli
    from settle.load import writer
    from settle.store import db

    prime, pnl, _ = example()

    @contextmanager
    def connection():
        yield MagicMock()

    def render(*args, **kwargs):
        if failure == 'error':
            raise subprocess.CalledProcessError(1, 'renderer')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired('renderer', 60)
        # A renderer that exits successfully without creating the workbook
        # is also incomplete, and must not produce a successful CLI status.
        return subprocess.CompletedProcess('renderer', 0)

    monkeypatch.setattr(cli, 'load_prime_by_id', lambda _: prime)
    monkeypatch.setattr(db, 'connect', connection)
    monkeypatch.setattr(monthly, 'from_database', lambda *a: (pnl, {}))
    monkeypatch.setattr(writer, 'enrich_with_dr', lambda p: p)
    monkeypatch.setattr(writer.subprocess, 'run', render)
    code = cli.main(['monthly-from-revenue', '--prime', 'grove', '--month', '2026-09',
                     '--revision', 'explicit', '--output-dir', str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 1
    assert 'Finalized' not in captured.out
    assert 'incomplete: missing xlsx' in captured.err
    assert 'new empty output directory' in captured.err
    assert (tmp_path / 'provenance.json').is_file()
    assert (tmp_path / 'summary.md').is_file()


def test_monthly_requires_active_basin_inputs():
    prime, pnl, record = example()
    rows = deepcopy(pnl.sky_revenue_daily)
    del rows[0]['basin_idle']
    changed = replace(pnl, sky_revenue_daily=rows)
    with pytest.raises(ValueError, match='missing Basin idle inputs'):
        monthly.validate_interest(changed, prime, record['input_provenance'])


def test_monthly_rejects_basin_deduction_for_unconfigured_prime():
    prime, pnl, record = example()
    rows = deepcopy(pnl.sky_revenue_daily)
    rows[0].update(basin_idle='1', basin_ilk_debt='2')
    with pytest.raises(ValueError, match='outside configured'):
        monthly.validate_interest(replace(pnl, sky_revenue_daily=rows),
                                  replace(prime, basin_idle_usds=None), record['input_provenance'])
