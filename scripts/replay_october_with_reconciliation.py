"""Read-only October instruction replay with PR277 replacements, not publication.

Revalidate 186 retained daily CoF rows and the six ordinary October instructions,
then use the production settlement formula with the separately replayed historical
ledger. No canonical artifacts, config, API revision or payment are written.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import socket
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml
from build_jan_sep_2026_reconciliation import calculate
from reconcile_grove_base_cash import replay as grove_replay
from reconcile_usds_bridge_baseline_2026 import replay as bridge_replay
from verify_october_retained_after_pr282 import replay as verify_ordinary

from settle.compute import sky_total_accrual
from settle.domain import Month

ROOT = Path(__file__).resolve().parents[1]


def project(preview, cfg, replacements):
    cfg = copy.deepcopy(cfg)
    # These are replacement totals, not incremental credits. Osero's existing
    # 5,514 and Grove's existing redemption credit must not be applied twice.
    cfg['msc_preview']['2026-10'].update(copy.deepcopy(replacements))
    indexed = {row['prime']: row for row in preview['rows']}

    def components(root, prime, month):
        row = indexed[prime]
        return tuple(D(row[k]) for k in ('sky_ordinary_total', 'demand_side_revenue', 'ordinary_supply_net'))

    with TemporaryDirectory(prefix='october-reconciliation-research-') as directory, \
         patch.object(sky_total_accrual, '_load_prime_components', components), \
         patch.object(sky_total_accrual, '_load_non_msc', lambda *a: (D(0), D(0), ['Non-MSC outside this replay'])):
        root = Path(directory)
        for name in indexed:
            target = root / 'settlements' / name / '2026-10' / 'provenance.json'
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({'scope': 'Retained-component research projection; not a canonical report'}))
        result = sky_total_accrual.compute_sky_total_accrual(Month(2026, 10), repo_root=root, config=cfg)
    rows = []
    for row in result.rows:
        old = indexed[row.prime]
        rows.append(dict(
            prime=row.prime, sky_ordinary=str(row.sky), demand_ordinary=str(row.dv), supply_ordinary=str(row.sv),
            sky_adj=str(row.sky_adj), sv_adj=str(row.sv_adj), dv_adj=str(row.dv_adj), send_credit=str(row.send_credit),
            derived_mint=str(row.derived_mint), derived_send=str(row.derived_send),
            mint=str(row.mint), send=str(row.send),
            mint_change=str(row.mint - D(old['msc_mint'])), send_change=str(row.send - D(old['msc_send'])),
        ))
    return rows


def replay():
    def denied(*args, **kwargs):
        raise AssertionError('Offline instruction replay attempted network access')
    with patch.object(socket.socket, 'connect', denied):
        ordinary = verify_ordinary(ROOT / 'reconciliation/usds-bridge-2026/baseline-correction/wallet-inputs.json')
        historical = calculate()
        committed = json.loads((ROOT / 'reconciliation/2026-01_to_2026-09/data.json').read_text())
        for key in ['items', 'adjustment_totals', 'proposed_combined_october_adjustments']:
            if historical[key] != committed[key]:
                raise ValueError('Historical ledger is stale: ' + key)
        grove = grove_replay(nonzero_only=True)
        after = json.loads((ROOT / 'reconciliation/2026-10/grove-pr280/after-provenance.json').read_text())
        venues = {r['venue_id']: r for r in after['venue_breakdown']}
        current = [r for r in grove['results'] if r['month'] == '2026-10']
        if {r['venue'] for r in current} != {'E19', 'E23', 'E43'}:
            raise ValueError('Incomplete Grove affected-venue replay')
        for r in current:
            if D(r['new']['revenue']) != D(venues[r['venue']]['revenue']):
                raise ValueError('Retained Grove revenue differs from current production replay')
        bridge = bridge_replay()
        preview = json.loads((ROOT / 'reconciliation/2026-10/retained-close/prime_msc_preview.json').read_text())
        cfg = yaml.safe_load((ROOT / 'config/sky_total.yaml').read_text())
        rows = project(preview, cfg, historical['proposed_combined_october_adjustments'])
    paths = [Path(__file__), ROOT / 'scripts/verify_october_retained_after_pr282.py',
             ROOT / 'reconciliation/2026-01_to_2026-09/data.json',
             ROOT / 'reconciliation/2026-10/retained-close/prime_msc_preview.json',
             ROOT / 'reconciliation/2026-10/grove-pr280/after-provenance.json',
             ROOT / 'config/sky_total.yaml']
    return dict(
        status='research_replay_complete_not_finalized', period='2026-10',
        scope='Retained ordinary October components plus independently replayed historical replacement adjustments; no consolidated Sky net revenue asserted',
        ordinary_validation=ordinary, historical_adjustments=historical['adjustment_totals'],
        replacement_adjustments=historical['proposed_combined_october_adjustments'],
        grove_current_production_replay=[dict(venue=r['venue'], revenue=r['new']['revenue']) for r in current],
        october_bridge_credit_already_in_ordinary=bridge['october_ordinary_signed_credit'],
        october_bridge_credit_applied_again=False, rows=rows,
        canonical_reports_written=False, configuration_changed=False, api_published=False,
        finalization_requirement='Canonical writer validates populated daily asset-control files or records explicit empty-file deferral',
        input_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(ROOT / 'settlements') or args.output.resolve().is_relative_to(ROOT / 'config'):
        parser.error('Use a research/reconciliation output path')
    result = replay()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['rows'], indent=2))


if __name__ == '__main__':
    main()
