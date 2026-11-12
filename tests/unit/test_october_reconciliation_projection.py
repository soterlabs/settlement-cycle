"""Historical adjustments must replace existing October credits exactly once."""
import copy
import json
import sys
from decimal import Decimal as D
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from replay_october_with_reconciliation import project  # noqa: E402


def test_replacements_preserve_ordinary_components_and_are_idempotent():
    preview = json.loads((ROOT / 'reconciliation/2026-10/prime_msc_preview.json').read_text())
    cfg = yaml.safe_load((ROOT / 'config/sky_total.yaml').read_text())
    original = copy.deepcopy(cfg)
    data = json.loads((ROOT / 'reconciliation/2026-01_to_2026-09/data.json').read_text())
    replacements = data['proposed_combined_october_adjustments']
    first = project(preview, cfg, replacements)
    assert cfg == original
    cfg['msc_preview']['2026-10'].update(replacements)
    assert project(preview, cfg, replacements) == first
    rows = {r['prime']: r for r in first}
    for old in preview['rows']:
        row = rows[old['prime']]
        assert D(row['sky_ordinary']) == D(old['sky_ordinary_total'])
        assert D(row['supply_ordinary']) == D(old['ordinary_supply_net'])
        assert D(row['demand_ordinary']) == D(old['demand_side_revenue'])
    assert D(rows['spark']['sky_adj']) == D('536094.04')
    assert D(rows['grove']['sky_adj']) == D('1030726.24')
    assert D(rows['osero']['send_credit']) == D('5514')
    assert rows['osero']['mint_change'] == rows['osero']['send_change'] == '0'
    assert D(rows['obex']['sv_adj']) == D('-273508.90')
