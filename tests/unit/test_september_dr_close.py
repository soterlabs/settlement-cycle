"""September payment corrections must never contaminate normal accrual."""
import copy
import logging
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.load import dr_rewards
from settle.load.dr_snapshot import snapshot_rows
from settle.load.settlement_adjustments import apply_settlement_adjustments
from settle.load.summary import render_summary

ROOT = Path(__file__).resolve().parents[2]


def test_documented_codes_and_intentionally_unresolved(caplog):
    owner, unpaid = dr_rewards._ref_code_map()
    for code in ('1020', '1997', '1998', '1999'):
        assert owner[code] == 'skybase'
    for code in ('99', '10000', '10001', '-999999', '123', '232', '234', '3003', '3123'):
        assert code in unpaid and code not in owner
    with caplog.at_level(logging.ERROR):
        dr_rewards.load_dr('skybase', '2026-09')
    assert not caplog.records


def test_september_venue_accrual_and_grove_emitted_split():
    rows = snapshot_rows(ROOT, '2026-09')[1:]
    owner, unpaid = dr_rewards._ref_code_map()
    expected = {'1997': '15124.246928', '1998': '5262.771706', '1999': '20.649651'}
    for code, amount in expected.items():
        assert sum((r[1] for r in rows if r[0] == code), D(0)).quantize(D('.000001')) == D(amount)
    farms = {r[0]: r[1] for r in rows if r[2].endswith('/ USDS-GROVE')}
    for code, amount, prime in [('1', '27321.822149', 'skybase'),
                                ('1002', '124.967515', 'skybase'),
                                ('2009', '549.811731', 'grove'),
                                ('-999999', '292.160634', None)]:
        assert farms[code].quantize(D('.000001')) == D(amount)
        assert owner.get(code) == prime
    assert '-999999' in unpaid
    for prime in ('skybase', 'grove'):
        dr = dr_rewards.load_dr(prime, '2026-09')
        assert dr['total'] == sum((r[1] for r in rows if owner.get(r[0]) == prime), D(0))


def _provenance(month='2026-09', prime='skybase'):
    return {'prime_id': prime, 'month': month,
            'period': {'start': month + '-01', 'end': month + '-30', 'n_days': 30},
            'results': {'prime_agent_revenue': '100', 'agent_rate': '20',
                        'distribution_rewards': '300', 'chronicle_points': '0',
                        'gar': '0', 'sky_revenue': '50', 'sde_revenue': '10'},
            'venue_breakdown': []}


def test_four_exact_trueups_are_separate_idempotent_and_not_revenue():
    prov = _provenance()
    results = copy.deepcopy(prov['results'])
    apply_settlement_adjustments(prov)
    expected = ['27740.235315', '34229.172646', '758.752668', '61966.169912']
    assert [r['amount'] for r in prov['settlement_adjustments']] == expected
    assert prov['settlement_payment']['prior_period_adjustments'] == '124694.330541'
    assert D(prov['settlement_payment']['total']) == D('125074.330541')
    first = copy.deepcopy(prov)
    apply_settlement_adjustments(prov)
    assert prov == first
    assert prov['results'] == results
    summary = render_summary(prov)
    for entry in prov['settlement_adjustments']:
        assert summary.count(entry['label']) == 1
        assert f"{D(entry['amount']):,.6f}" in summary


@pytest.mark.parametrize('month', ['2026-01', '2026-07', '2026-08', '2026-10'])
def test_historical_and_later_months_have_no_trueups(month):
    prov = _provenance(month)
    apply_settlement_adjustments(prov)
    assert prov['settlement_adjustments'] == []
    assert prov['settlement_payment']['prior_period_adjustments'] == '0'


@pytest.mark.parametrize('prime', ['spark', 'grove', 'keel', 'obex', 'osero'])
def test_no_other_prime_gets_skybase_trueups(prime):
    prov = _provenance(prime=prime)
    apply_settlement_adjustments(prov)
    assert not prov['settlement_adjustments']


def test_snapshot_rejects_modified_input(tmp_path):
    import shutil
    dest = tmp_path / 'data/distribution_rewards/2026-09'
    shutil.copytree(ROOT / 'data/distribution_rewards/2026-09', dest)
    with (dest / 'accrual.csv').open('a') as f:
        f.write('corruption\n')
    with pytest.raises(ValueError, match='checksum'):
        snapshot_rows(tmp_path, '2026-09')


def test_dr_only_rerun_preserves_history_and_never_duplicates_trueups(tmp_path, monkeypatch):
    import json

    from settle.load import writer

    monkeypatch.setattr(writer, '_REPO_ROOT', tmp_path)
    monkeypatch.setattr(writer, '_build_canonical_xlsx', lambda *a: None)
    prior = tmp_path / 'settlements/skybase/2026-08/provenance.json'
    prior.parent.mkdir(parents=True)
    prior.write_text(json.dumps(_provenance('2026-08')))
    before = prior.read_bytes()
    target = tmp_path / 'settlements/skybase/2026-09/provenance.json'
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(_provenance()))
    writer.refresh_dr_only('skybase', months={'2026-09'})
    once = json.loads(target.read_text())
    writer.refresh_dr_only('skybase', months={'2026-09'})
    twice = json.loads(target.read_text())
    assert once == twice
    assert prior.read_bytes() == before
    assert len(twice['settlement_adjustments']) == 4
    assert D(twice['results']['distribution_rewards']) == dr_rewards.load_dr('skybase', '2026-09')['total']
    assert twice['settlement_payment']['prior_period_adjustments'] == '124694.330541'


def test_xlsx_keeps_four_distinct_payment_lines():
    import runpy

    import openpyxl

    write = runpy.run_path(str(ROOT / 'scripts/build_settlement_xlsx.py'))['_write_summary']
    prov = _provenance()
    apply_settlement_adjustments(prov)
    wb = openpyxl.Workbook()
    write(wb.active, prov, [])
    cells = list(wb.active.values)
    for entry in prov['settlement_adjustments']:
        matching = [r for r in cells if r[0] == f"{entry['label']} — {entry['earned_period']}"]
        assert len(matching) == 1
        assert D(str(matching[0][1])) == D(entry['amount'])


def test_historical_payment_does_not_reduce_current_sky_net_revenue():
    from settle.compute.sky_total_accrual import SkyTotalAccrualMonthly, render_summary

    close = SkyTotalAccrualMonthly('2026-09', [], D('1000'), D('50'))
    snr = close.sky_net_revenue
    close.prior_period_payments = {'skybase': D('124694.330541')}
    assert close.sky_net_revenue == snr
    assert '124,694.330541' in render_summary(close)
