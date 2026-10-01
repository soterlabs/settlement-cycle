import json
from decimal import Decimal
from pathlib import Path

import openpyxl
import yaml

from settle.load import writer
from settle.load.reference_rate_note import reference_rate_note
from settle.revenue.september_close import approved_reference
from tests.unit.test_monthly_from_revenue import example

ROOT = Path(__file__).resolve().parents[2]


def test_writer_and_dr_refresh_preserve_disclosure_without_changing_money(tmp_path, monkeypatch):
    _, pnl, _ = example(subsidy=True)
    rates = approved_reference()
    baseline = writer.write_settlement(pnl, tmp_path / 'baseline')
    folder = tmp_path / 'settlements/grove/2026-09'
    for _ in range(2):
        paths = writer.write_settlement(pnl, folder, reference_rates=rates)
    expected = json.loads(baseline['provenance'].read_text())['results']
    prov = json.loads(paths['provenance'].read_text())
    assert prov['results'] == expected
    assert prov['close_reference_rate_provenance'] == rates
    monkeypatch.setattr(writer, '_REPO_ROOT', tmp_path)
    # Exercise the real workbook subprocess again during DR-only refresh.
    (tmp_path / "scripts").symlink_to(ROOT / "scripts", target_is_directory=True)
    writer.refresh_dr_only('grove', months={'2026-09'})
    assert json.loads(paths['provenance'].read_text())['results'] == expected
    summary = paths['summary'].read_text()
    assert summary.count('**Reference-rate assumption:**') == 1
    assert '3.88%' in summary and '2026-09-29' in summary and '2026-09-30' in summary
    wb = openpyxl.load_workbook(paths['xlsx'], read_only=True, data_only=True)
    try:
        rows = [r for r in wb['Summary'].values if r[0] == 'Reference-rate assumption']
        assert len(rows) == 1
        assert '3.88%' in rows[0][1] and 'not an official observation' in rows[0][1]
    finally:
        wb.close()


def test_status_only_api_reuse_and_official_inputs():
    status = 'operator-authorized September 29 SOFR (3.88%) carried to September 30'
    assert reference_rate_note({'sources': {'reference_rate_status': status}}) == status
    assert reference_rate_note({'sources': {'reference_rate_status': 'official coverage'}}) == ''
    assert reference_rate_note({'close_reference_rate_provenance': {'coverage_complete': True}}) == ''


def test_sky_total_retains_underlying_rate_note(tmp_path):
    from settle.compute.sky_total_accrual import compute_sky_total_accrual, write_sky_total_accrual
    from settle.domain import Month
    from tests.unit.test_sky_total_accrual import _JULY_CFG, _july_repo

    _july_repo(tmp_path)
    before = compute_sky_total_accrual(Month(2026, 7), repo_root=tmp_path, config=_JULY_CFG)
    p = tmp_path / 'settlements/grove/2026-07/provenance.json'
    prov = json.loads(p.read_text())
    prov['sources'] = {'reference_rate_status': 'Operator-authorized test rate assumption'}
    p.write_text(json.dumps(prov))
    after = compute_sky_total_accrual(Month(2026, 7), repo_root=tmp_path, config=_JULY_CFG)
    assert after.sky_net_revenue == before.sky_net_revenue
    paths = write_sky_total_accrual(after, tmp_path / 'sky')
    assert 'grove: Operator-authorized test rate assumption' in paths['summary'].read_text()
    assert json.loads(paths['provenance'].read_text())['reference_rate_notes'] == after.reference_rate_notes


def test_tmf_discloses_inherited_reference_assumption(tmp_path):
    from settle.compute.tmf import TmfPolicy, compute_tmf_monthly, render_summary

    p = tmp_path / 'settlements/sky_total/2026-09/provenance.json'
    p.parent.mkdir(parents=True)
    note = reference_rate_note({'close_reference_rate_provenance': approved_reference()})
    p.write_text(json.dumps({'results': {'sky_net_revenue': '1000000'}, 'reference_rate_notes': [note]}))
    cfg = yaml.safe_load((ROOT / 'config/tmf.yaml').read_text())
    r = compute_tmf_monthly('2026-09', cfg['months']['2026-09'], TmfPolicy.from_config(cfg),
                            activity=None, state={'usds_total_supply': Decimal('6000000000'), 'block': 1},
                            repo_root=tmp_path)
    assert note in render_summary(r)
    assert r.inputs.snr == Decimal('1000000')
