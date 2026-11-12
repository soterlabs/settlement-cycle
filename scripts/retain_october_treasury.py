"""Retain October treasury provenance and source hashes after all three runners.

Run non-MSC, consolidated Sky, then TMF for --months 2026-10 first.
This saves reproducibility inputs; it neither runs extraction nor publishes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    folder = ROOT / 'reconciliation/2026-10/treasury'
    names = {'non_msc': 'non-msc.json', 'sky_total': 'sky-total.json', 'tmf': 'tmf.json'}
    files = ['config/non_msc.yaml', 'config/sky_total.yaml', 'config/tmf.yaml',
             'reconciliation/2026-10/prime_msc_preview.json',
             'reconciliation/2026-10/partner_rebates.json',
             'reconciliation/2026-01_to_2026-09/data.json',
             'reconciliation/2026-10/treasury/ba-inputs.json',
             'reconciliation/2026-10/treasury/rwa-jar-transfers.json',
             'reconciliation/2026-10/treasury/rwa-jar-receipt.json',
             'src/settle/compute/non_msc.py', 'src/settle/compute/sky_total_accrual.py',
             'src/settle/compute/tmf.py', 'src/settle/extract/tmf_state.py',
             'src/settle/normalize/sources/hypersync_non_msc.py',
             'src/settle/normalize/sources/hypersync_sbe.py']
    for name, filename in names.items():
        source = ROOT / 'settlements' / name / '2026-10/provenance.json'
        data = json.loads(source.read_text())
        if data['month'] != '2026-10' or data['id'] != name:
            raise ValueError('Incorrect treasury input: ' + str(source))
        if name == 'tmf' and (data['pins']['partial'] or data['activity'] is None):
            raise ValueError('October TMF activity is incomplete')
        target = folder / filename
        target.write_bytes(source.read_bytes())
        files += [str(target.relative_to(ROOT)), f'settlements/{name}/2026-10/summary.md']
    hashes = {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in files}
    (folder / 'manifest.json').write_text(json.dumps({'month': '2026-10', 'sha256': hashes}, indent=2) + '\n')
    from build_october_forum_draft import build
    (ROOT / 'forum_posts/msc-2026-10.md').write_text(build())


if __name__ == '__main__':
    main()
