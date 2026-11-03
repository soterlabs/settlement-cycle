#!/usr/bin/env python3
"""Explain principal-gap changes without changing funding or publishing yields.

Stream the independent capital control transaction by transaction, then compare
with the funding ledger. Keep bounded witnesses rather than a huge event dump.
Repayment effects are measured separately; the remaining change is not claimed
to be missing cash (it can include routing and prior differences carried forward).
"""
import argparse
import gzip
import hashlib
import heapq
import json
import logging
import tempfile
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from audit_spark_mixed_funding_yields import prepare_history, selected_accounts, write_json
from report_allocation_capital_readiness import capital_timeline

from settle.compute.allocation_capital import replay_history
from settle.normalize.allocation_history_cache import load_history

ZERO = D(0)


class GapEvents:
    def __init__(self, stream, accounts, start, limit=20, on_progress=None):
        self.stream, self.accounts, self.start, self.limit = stream, accounts, start, limit
        self.last = dict.fromkeys(accounts, ZERO)
        self.repayment = dict.fromkeys(accounts, ZERO)
        self.totals = {v: {'repayment_effect': ZERO, 'other_effect': ZERO} for v in accounts}
        self.heaps = {v: {'history': [], 'period': []} for v in accounts}
        self.daily = {}
        self.count = 0
        self.on_progress = on_progress

    def observe(self, batch, kind, values):
        if kind in ('repay', 'external_repay'):
            for venue, keys in self.accounts.items():
                # Repayments consume the clearing account, not the selected
                # allocation accounts. This records their refinancing effect.
                self.repayment[venue] -= sum((values[k] for k in keys), ZERO)
            return
        if kind != 'batch_end':
            raise ValueError('Unexpected funding diagnostic event')
        control = json.loads(next(self.stream))
        if control['identity'] != batch.identity:
            raise ValueError('Control and funding transaction order differ')
        self.count += 1
        for venue, keys in self.accounts.items():
            gap = D(control['principal'][venue])-sum((values[k] for k in keys), ZERO)
            delta = gap-self.last[venue]
            repayment = self.repayment[venue]
            other = delta-repayment
            self.totals[venue]['repayment_effect'] += repayment
            self.totals[venue]['other_effect'] += other
            row = {'identity': batch.identity, 'day': str(batch.day),
                   'gap_before': self.last[venue], 'gap_after': gap,
                   'gap_change': delta, 'repayment_effect': repayment,
                   'other_effect': other, 'minted': batch.minted,
                   'external_operations': [op.kind for op in batch.external_funding],
                   'funding_assumption': batch.funding_assumption}
            for scope in ('history', 'period'):
                if scope == 'period' and batch.day < self.start:
                    continue
                heap = self.heaps[venue][scope]
                # Include offsetting effects even if net gap movement is zero.
                size = max(abs(delta), abs(repayment), abs(other))
                if size:
                    item = (size, self.count, row)
                    heapq.heappush(heap, item)
                    if len(heap) > self.limit:
                        heapq.heappop(heap)
            self.last[venue] = gap
            self.repayment[venue] = ZERO
        if batch.day >= self.start:
            self.daily[str(batch.day)] = dict(self.last)
        if self.on_progress is not None and self.count % 10000 == 0:
            self.on_progress(dict(self.snapshot(), complete=False, through=str(batch.day)))

    def result(self):
        if next(self.stream, None) is not None:
            raise ValueError('Funding replay omitted control transactions')
        for venue, parts in self.totals.items():
            if abs(sum(parts.values(), ZERO)-self.last[venue]) > D('1e-8'):
                raise ValueError('Diagnostic contributions do not bridge the closing gap')
        return dict(self.snapshot(), complete=True)

    def snapshot(self):
        return {'publishable': False, 'transactions': self.count, 'closing_gaps': self.last,
                'inception_contributions': self.totals,
                'largest_events': {v: {scope: [entry[2] for entry in sorted(heap, reverse=True)]
                                      for scope, heap in scopes.items()} for v, scopes in self.heaps.items()},
                'limitation': 'Other effects include routing, original-capital assumptions and carried differences; they are not automatically missing cash.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', type=Path, required=True)
    parser.add_argument('--reference', type=Path, required=True,
                        help='Completed corrected mixed-funding report for daily comparison')
    parser.add_argument('--venues', nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    progress_path = args.output.with_suffix('.progress.json')
    if {args.output.resolve(), progress_path.resolve()} & {args.history.resolve(), args.reference.resolve()}:
        parser.error('Output cannot overwrite evidence')
    if len(set(args.venues)) != len(args.venues):
        parser.error('Duplicate venue')
    source_paths = [Path(__file__), Path('scripts/audit_spark_mixed_funding_yields.py'),
                    Path('scripts/report_allocation_capital_readiness.py'),
                    *sorted(Path('src/settle/compute').glob('*.py')),
                    Path('src/settle/normalize/allocation_capital.py'),
                    Path('src/settle/normalize/allocation_history_cache.py'),
                    Path('config/capital-tracing/spark-par-swap-gains.json')]
    source_hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    reference_hash = hashlib.sha256(args.reference.read_bytes()).hexdigest()
    reference = json.loads(args.reference.read_text())
    refs = {r['venue']: r for r in reference['allocations']}
    if reference.get('prime') != 'spark' or set(args.venues)-refs.keys():
        parser.error('Reference must cover the selected Spark allocations')
    digest = hashlib.sha256(args.history.read_bytes()).hexdigest()
    if reference.get('anchorage_return_policy') != 'all-principal' or reference['input_sha256'].get(str(args.history)) != digest:
        parser.error('Reference must use the same history and accepted Anchorage policy')
    start, end = date.fromisoformat(reference['start']), date.fromisoformat(reference['end'])
    with gzip.open(args.history, 'rt') as stream:
        key = json.loads(next(stream))['fingerprint']
    logging.basicConfig(level=logging.INFO)
    logging.info('Loading and preparing normalized history')
    history = prepare_history(load_history(args.history, key))
    accounts = selected_accounts(history, args.venues)
    # The stream avoids retaining 450k transaction snapshots in memory.
    with tempfile.TemporaryDirectory(prefix='spark-gap-events-') as tmp:
        spool = Path(tmp)/'control.jsonl.gz'
        with gzip.open(spool, 'wt') as stream:
            def record(batch, basis):
                stream.write(json.dumps({'identity': batch.identity, 'principal': {
                    v: str(sum((basis.get(a, ZERO) for a in keys), ZERO)) for v, keys in accounts.items()}})+'\n')
            logging.info('Streaming independent capital control')
            control = capital_timeline(history, start, end, on_batch=record,
                                       on_batch_accounts={a for keys in accounts.values() for a in keys})
        with gzip.open(spool, 'rt') as stream:
            audit = GapEvents(stream, accounts, start, on_progress=lambda value: write_json(progress_path, value))
            replay = replay_history(history, start, end,
                                    funding_accounts={a for keys in accounts.values() for a in keys},
                                    funding_observer=audit.observe)
            result = audit.result()
        # Prove instrumentation did not change the previously calculated gaps.
        for day, funding in replay.daily_funding.items():
            for venue, keys in accounts.items():
                capital = sum((D(control[str(day)][a]['principal']) for a in keys), ZERO)
                funded = sum((funding[a]['sky_principal']+sum(funding[a]['savings_by_source'].values(), ZERO) for a in keys), ZERO)
                if abs(capital-funded-D(refs[venue]['daily'][str(day)]['capital_basis_difference'])) > D('1e-8'):
                    raise ValueError('Instrumented replay differs from corrected daily reference')
        if hashlib.sha256(args.history.read_bytes()).hexdigest() != digest or hashlib.sha256(args.reference.read_bytes()).hexdigest() != reference_hash:
            raise ValueError('Evidence changed during diagnostic replay')
        if source_hashes != {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}:
            raise ValueError('Code or policy changed during diagnostic replay')
        result.update(source_sha256=source_hashes, start=str(start), end=str(end), history_sha256=digest,
                      reference_sha256=reference_hash,
                      daily_reference_verified=True)
        write_json(args.output, result)


if __name__ == '__main__':
    main()
