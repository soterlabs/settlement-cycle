"""Render October reports from verified retained inputs, with historical adjustments once.

No event/balance extraction, API publication or prior-month writes. The output
root must be a new staging directory. Canonical report writers and the explicit
asset-control deferral are used; the ALM review is still checked for every prime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

import openpyxl
import yaml
from replay_october_grove_pr280 import decode_provenance
from replay_october_with_reconciliation import replay
from run_october_close import check_review

from settle.compute import sky_total_accrual
from settle.domain import Month
from settle.domain.config import load_prime_by_id
from settle.load.provenance import render_provenance
from settle.load.writer import write_settlement
from settle.revenue.monthly import validate_interest
from settle.revenue.verification import canonical

ROOT = Path(__file__).resolve().parents[1]
RETAINED = ROOT / 'reconciliation/2026-10/retained-close'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def instruction_table(row):
    return [
        ['Ordinary Sky revenue', row['sky']],
        ['Ordinary demand-side revenue', row['dv']],
        ['Ordinary supply-side share', row['sv']],
        ['Sky adjustment (historical plus approved ordinary)', row['sky_adj']],
        ['Supply adjustment (historical plus approved ordinary)', row['sv_adj']],
        ['Demand adjustment', row['dv_adj']],
        ['Send-only credit', row['send_credit']],
        ['Mint USDS (whole tokens)', row['mint']],
        ['Send USDS (whole tokens)', row['send']],
    ]


def finalize(output):
    if output.exists():
        raise ValueError('Use a new staging output directory')
    if output.resolve().is_relative_to(ROOT / 'settlements'):
        raise ValueError('Stage outside canonical settlements before promotion')
    manifest = json.loads((RETAINED / 'manifest.json').read_text())
    for relative, digest in manifest.items():
        if sha(RETAINED / relative) != digest:
            raise ValueError('Retained close input changed: ' + relative)
    checked = replay()
    preview = json.loads((RETAINED / 'prime_msc_preview.json').read_text())
    cfg = yaml.safe_load((ROOT / 'config/sky_total.yaml').read_text())
    if any(cfg['msc_preview']['2026-10'].get(p) != v for p, v in checked['replacement_adjustments'].items()):
        raise ValueError('Apply replacement adjustments to October config before rendering')
    reference = json.loads((ROOT / 'config/october_2026_official_reference_rates.json').read_text())
    prepared = []
    for old in preview['rows']:
        name = old['prime']
        review = check_review(name)
        source = RETAINED / name / 'provenance.json'
        original = json.loads(source.read_text())
        pnl = decode_provenance(original)
        roundtrip = render_provenance(pnl)
        for field in ['results', 'venue_breakdown', 'sky_revenue_daily', 'sde_daily_breakdown']:
            if original[field] != roundtrip[field]:
                raise ValueError(f'{name}: lossy retained input decode: {field}')
        validate_interest(pnl, load_prime_by_id(name), {'reference_rates': reference})
        prepared.append((name, pnl, source, review, old))
    # All input checks precede rendering. Only existing source data is reused.
    paths = {}
    for name, pnl, source, review, _old in prepared:
        paths[name] = write_settlement(
            pnl, output / 'settlements' / name / '2026-10',
            sources={'calculation': 'Scoped October retained-input finalization; historical components replayed separately',
                     'retained_input_sha256': sha(source), 'alm_transfer_review_sha256': review},
            reference_rates=reference,
        )
        if 'xlsx' not in paths[name]:
            raise ValueError(f'{name}: workbook rendering failed')
        written = json.loads(paths[name]['provenance'].read_text())
        if written['results'] != json.loads(source.read_text())['results']:
            raise ValueError(f'{name}: report rendering changed ordinary financial results')
    # Read actual staged provenance through production logic. Non-MSC is outside
    # this prime-close replay; never export the mocked consolidated total.
    with patch.object(sky_total_accrual, '_load_non_msc', lambda *a: (D(0), D(0), ['Non-MSC not recalculated'])):
        totals = sky_total_accrual.compute_sky_total_accrual(Month(2026, 10), repo_root=output, config=cfg)
    projected = {r['prime']: r for r in checked['rows']}
    updated = json.loads(json.dumps(preview))
    indexed = {r['prime']: r for r in updated['rows']}
    for row in totals.rows:
        name = row.prime
        expected = projected[name]
        if str(row.mint) != expected['mint'] or str(row.send) != expected['send']:
            raise ValueError(f'{name}: production instructions differ from scoped replay')
        for field in ['derived_mint', 'derived_send']:
            if abs(getattr(row, field) - D(expected[field])) > D('1e-9'):
                raise ValueError(f'{name}: unrounded instruction differs')
        instruction = canonical(row)
        table = instruction_table(instruction)
        p = paths[name]['provenance']
        payload = json.loads(p.read_text())
        payload['msc_instruction'] = instruction
        payload['msc_instruction_sources'] = {'historical_ledger_sha256': sha(ROOT / 'reconciliation/2026-01_to_2026-09/data.json'),
                                               'sky_total_config_sha256': sha(ROOT / 'config/sky_total.yaml')}
        p.write_text(json.dumps(payload, indent=2) + '\n')
        section = '\n## MSC settlement instructions — October 2026\n\n'
        section += 'These payment instructions include the approved adjustments once; the operating revenue above remains October-earned revenue.\n\n'
        section += '| Component | USDS |\n|---|---:|\n'
        section += ''.join(f'| {label} | {D(value):,.2f} |\n' for label, value in table)
        section += '\nIndependent daily asset checks: **' + payload['asset_controls']['status'] + '**. ALM transfer review retained. No payment executed.\n'
        summary = paths[name]['summary']
        summary.write_text(summary.read_text().rstrip() + '\n' + section)
        book = openpyxl.load_workbook(paths[name]['xlsx'])
        sheet = book.create_sheet('MSC instructions')
        sheet.append(['October 2026 MSC instructions', 'USDS'])
        for label, value in table:
            sheet.append([label, float(D(value))])
            sheet.cell(sheet.max_row, 2).number_format = '#,##0.00;[Red]-#,##0.00'
        sheet.append(['Independent daily asset controls', payload['asset_controls']['status']])
        sheet.append(['Includes historical adjustments once; no payment executed'])
        sheet.column_dimensions['A'].width = 65
        sheet.column_dimensions['B'].width = 26
        book.save(paths[name]['xlsx'])
        book.close()
        target = indexed[name]
        target.update(sky_adjustment=str(row.sky_adj), supply_adjustment=str(row.sv_adj),
                      demand_adjustment=str(row.dv_adj), send_credit=str(row.send_credit),
                      msc_supply_component=str(max(row.sv, D(0)) + row.sv_adj) if row.has_ilk else str(row.sv + row.sv_adj),
                      msc_mint_unrounded=str(row.derived_mint), msc_send_unrounded=str(row.derived_send),
                      msc_mint=str(row.mint), msc_send=str(row.send),
                      summary_sha256=sha(paths[name]['summary']), workbook_sha256=sha(paths[name]['xlsx']),
                      workbook_sheets=target['workbook_sheets'] + 1,
                      asset_controls=payload['asset_controls'])
    updated['sky_total_config_sha256'] = sha(ROOT / 'config/sky_total.yaml')
    updated['scope'] = 'October ordinary inputs retained; historical replacements applied once; independent daily checks explicitly deferred; no API publication or payment'
    (output / 'prime_msc_preview.json').write_text(json.dumps(updated, indent=2) + '\n')
    result = {'status': 'reports_rendered_daily_controls_deferred',
              'historical_ledger_sha256': sha(ROOT / 'reconciliation/2026-01_to_2026-09/data.json'),
              'retained_manifest_sha256': sha(RETAINED / 'manifest.json'),
              'finalizer_sha256': sha(Path(__file__)),
              'api_published': False, 'prior_months_rewritten': False,
              'output_sha256': {str(p.relative_to(output)): sha(p) for bundle in paths.values() for p in bundle.values()}}
    (output / 'finalization.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-root', type=Path, required=True)
    args = parser.parse_args()
    def denied(*args, **kwargs):
        raise AssertionError('Retained close attempted network access')
    with patch.object(socket.socket, 'connect', denied):
        result = finalize(args.output_root)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
