"""Read a finalized month without importing historical workbook additions.

The workbook's summary rounds to cents. Its companion CSV retains venue-level
precision and the Grove farm's emitted-code split. Both files are hash-bound;
the CSV must reconcile to the workbook before it can supply normal accrual.
"""
from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


def snapshot_rows(root: Path, month: str) -> tuple | None:
    from ..domain.period import Month

    month = str(Month.parse(month))
    folder = root / 'data' / 'distribution_rewards' / month
    if not folder.exists():
        return None
    manifest = json.loads((folder / 'manifest.json').read_text())
    if manifest['month'] != month:
        raise ValueError('DR snapshot month mismatch')
    for name in ('accrual.csv', 'finalized.xlsx'):
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != manifest['files'][name]:
            raise ValueError(f'DR snapshot checksum mismatch: {name}')

    totals: dict[str, Decimal] = defaultdict(Decimal)
    rows = [('ref_code', month, 'notes')]
    seen = set()
    with (folder / 'accrual.csv').open() as f:
        for r in csv.DictReader(f):
            key = tuple(r[k] for k in ('month', 'blockchain', 'token', 'ref_code', 'source'))
            amount = Decimal(r['dr_usd'])
            if r['month'] != f'{month}-01' or key in seen or not amount.is_finite():
                raise ValueError(f'Invalid or duplicate DR snapshot row: {key}')
            seen.add(key)
            totals[r['ref_code']] += amount
            rows.append((r['ref_code'], amount, f"{r['source']} / {r['blockchain']} / {r['token']}"))
    if not seen:
        raise ValueError('Empty DR snapshot')

    import openpyxl

    wb = openpyxl.load_workbook(folder / 'finalized.xlsx', read_only=True, data_only=True)
    try:
        sheet = iter(wb['Soter by Ref Code'].values)
        header = next(sheet)
        col = header.index(month)
        ref = header.index('ref_code')
        workbook = {str(r[ref]): Decimal(str(r[col] or 0)) for r in sheet
                    if r[ref] is not None and str(r[ref]).lower() != 'total'}
    finally:
        wb.close()
    # The workbook deliberately rounds only the final ref-code aggregate.
    # Upstream omits codes whose rounded history is entirely zero. Keep
    # their full precision in the import, including unresolved sub-cent codes.
    if any(
        abs(totals.get(code, Decimal(0)) - workbook.get(code, Decimal(0))) > Decimal('0.00500001')
        for code in set(totals) | set(workbook)
    ):
        raise ValueError('DR snapshot does not reconcile to finalized workbook')
    return tuple(rows)
