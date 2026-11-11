"""Scoped report finalization preserves ordinary inputs and applies true-ups once."""
import importlib.util
import json
from pathlib import Path

import openpyxl
import pytest

from settle.load import writer

ROOT = Path(__file__).resolve().parents[2]


def test_scoped_finalization_renders_correct_instructions_without_revaluing_positions(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    spec = importlib.util.spec_from_file_location('october_finalizer', ROOT / 'scripts/finalize_october_retained_close.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Local human-reviewed scan/evidence availability is tested by its own gate.
    # This test isolates rendering and financial propagation, not scan approval.
    monkeypatch.setattr(module, 'check_review', lambda name: 'synthetic-review')
    monkeypatch.setattr(writer, 'require_asset_controls', lambda *a, **kw: {'status': 'skipped', 'test_only': True})
    output = tmp_path / 'new-close'
    result = module.finalize(output)
    assert result['status'] == 'reports_rendered_daily_controls_deferred'
    expected = json.loads((ROOT / 'reconciliation/2026-10/prime_msc_preview.json').read_text())
    actual = json.loads((output / 'prime_msc_preview.json').read_text())
    for old, new in zip(expected['rows'], actual['rows'], strict=True):
        assert old['prime'] == new['prime']
        assert old['msc_mint'] == new['msc_mint']
        assert old['msc_send'] == new['msc_send']
        prime = new['prime']
        source = json.loads((module.RETAINED / prime / 'provenance.json').read_text())
        target = output / 'settlements' / prime / '2026-10'
        written = json.loads((target / 'provenance.json').read_text())
        for field in ['results', 'venue_breakdown', 'sky_revenue_daily']:
            assert source[field] == written[field]
        workbook = openpyxl.load_workbook(target / f'{prime}_settlement_october_2026.xlsx', data_only=True)
        try:
            values = dict(workbook['MSC instructions'].values)
            assert str(values['Mint USDS (whole tokens)']) == new['msc_mint']
            assert str(values['Send USDS (whole tokens)']) == new['msc_send']
            assert values['Independent daily asset controls'] == 'skipped'
        finally:
            workbook.close()
    # Reusing a destination must not overwrite even this explicitly scoped close.
    with pytest.raises(ValueError, match='new staging'):
        module.finalize(output)
