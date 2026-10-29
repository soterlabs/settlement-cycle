#!/usr/bin/env python3
"""Attribute sUSDS contributions using separate Savings and gross Sky draw evidence.

A verified contribution replaces the conservative transaction-net attribution;
it never adds a second copy of the draw. Normalized history, acquisition cost,
net ilk debt and external Savings operations are left unchanged.
"""
import argparse
import gzip
import hashlib
import json
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from audit_allocation_positions import audit
from audit_bridged_allocation_origins import surviving_sources
from audit_scoped_borrowing_origins import trace
from audit_spark_ordered_draws import SUSDS, load_savings_specs, validate

from settle.compute.executed_spell_capital import apply_executed_spells
from settle.normalize.allocation_history_cache import load_history


def attribute(history, receipts, start, end, *, allow_mixed_prefix=False, include_native_returns=False,
              allow_prefix_payments=False, include_spusds_returns=False):
    from decimal import localcontext
    with localcontext() as ctx:
        ctx.prec = 60
        return _attribute(history, receipts, start, end, allow_mixed_prefix, include_native_returns,
                          allow_prefix_payments, include_spusds_returns)


def _attribute(history, receipts, start, end, allow_mixed_prefix, include_native_returns,
               allow_prefix_payments, include_spusds_returns):
    if history.venue_accounts['S32'] != SUSDS or history.custody_accounts.get('S32'):
        raise ValueError('Unexpected sUSDS allocation identity')
    by_id = {b.identity: b for b in history.batches}
    proofs, rejected, verified = [], [], {}
    savings_specs = load_savings_specs()
    for tx, receipt in sorted(receipts.items()):
        identity = 'ethereum:' + tx
        if identity not in by_id:
            raise ValueError('Receipt missing original normalized transaction')
        if by_id[identity].day > end:
            raise ValueError('Receipt exceeds audit period')
        try:
            proof = validate(by_id[identity], receipt, allow_mixed_prefix=allow_mixed_prefix,
                             allow_prefix_payments=allow_prefix_payments, savings_specs=savings_specs)
        except ValueError as exc:
            rejected.append({'transaction': identity, 'reason': str(exc)})
            continue
        proofs.append(proof)
        verified[identity] = D(proof['verified_borrowed_contribution'])
    native_proofs, native_origin = [], None
    if include_native_returns:
        from audit_spark_native_return_origins import contributions
        native_proofs, native_origin = contributions(history, end)
        for proof in native_proofs:
            if proof['transaction'] in verified:
                raise ValueError('Receipt cannot be both a new draw and a native return')
            verified[proof['transaction']] = D(proof['verified_borrowed_contribution'])
    spusds_proofs, spusds_origin = [], None
    if include_spusds_returns:
        from audit_spark_spusds_returns import contributions as spusds_contributions
        spusds_proofs, spusds_origin = spusds_contributions(history, receipts, end)
        for proof in spusds_proofs:
            if proof['transaction'] in verified:
                raise ValueError('Reallocation overlaps an already attributed contribution')
            verified[proof['transaction']] = D(proof['verified_borrowed_contribution'])
        handled = {proof['transaction'] for proof in spusds_proofs}
        rejected = [row for row in rejected if row['transaction'] not in handled]
    origin = trace(history, ('S32',), end, verified_draws=verified)
    local = audit(history, 'S32', start, end)
    capital = D(local['closing_capital'])
    borrowed = D(origin['closing_borrowed_by_account'][SUSDS])
    if borrowed > capital + D('1e-8'):
        raise ValueError('Origin exceeds position capital')
    surviving = surviving_sources(local, origin, SUSDS)
    if abs(sum((D(r['unclassified_closing_capital']) for r in surviving), D(0)) - (capital - borrowed)) > D('1e-8'):
        raise ValueError('Residual source ranking does not reconcile')
    result = {'scope': __doc__.strip(), 'venue': 'S32', 'start': str(start), 'end': str(end),
            'closing_capital': str(capital), 'closing_borrowed': str(borrowed),
            'remaining_origin_to_classify': str(capital - borrowed),
            'borrowed_percent': str(borrowed / capital * 100 if capital else D(0)),
            'verified_routes': proofs, 'rejected_routes': rejected,
            'surviving_unclassified_sources': surviving, 'origin_trace': origin}
    if include_native_returns:
        result.update(native_return_proofs=native_proofs, native_origin_trace=native_origin)
    if include_spusds_returns:
        result.update(spusds_return_proofs=spusds_proofs, spusds_origin_trace=spusds_origin)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history', type=Path, required=True)
    p.add_argument('--receipts', type=Path, required=True)
    p.add_argument('--start', type=date.fromisoformat, required=True)
    p.add_argument('--end', type=date.fromisoformat, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--allow-mixed-prefix', action='store_true',
                   help='Subtract all other pre-deposit USDS receipts before proving borrowed origin.')
    p.add_argument('--include-native-returns', action='store_true',
                   help='Carry surviving borrowed basis through authenticated July Base/Optimism returns.')
    p.add_argument('--allow-prefix-payments', action='store_true',
                   help='Charge intervening USDS payments against borrowing before attributing the deposit.')
    p.add_argument('--include-spusds-returns', action='store_true',
                   help='Carry source borrowed principal through isolated spUSDS withdrawals into sUSDS.')
    args = p.parse_args()
    if args.output.resolve() in {args.history.resolve(), args.receipts.resolve()}:
        p.error('Output must not overwrite an input')
    with gzip.open(args.history, 'rt') as f:
        key = json.loads(next(f))['fingerprint']
    h = load_history(args.history, key)
    if h is None:
        raise ValueError('Invalid history')
    opener = gzip.open if args.receipts.suffix == '.gz' else open
    with opener(args.receipts, 'rt') as f:
        receipts = json.load(f)
    r = attribute(apply_executed_spells(h), receipts, args.start, args.end,
                  allow_mixed_prefix=args.allow_mixed_prefix, include_native_returns=args.include_native_returns,
                  allow_prefix_payments=args.allow_prefix_payments, include_spusds_returns=args.include_spusds_returns)
    r['input_sha256'] = hashlib.sha256(args.history.read_bytes()).hexdigest()
    r['receipt_sha256'] = hashlib.sha256(args.receipts.read_bytes()).hexdigest()
    paths = [Path(__file__), Path('scripts/audit_spark_ordered_draws.py'),
             Path('scripts/audit_spark_native_return_origins.py'),
             Path('scripts/audit_spark_spusds_returns.py'),
             Path('scripts/audit_scoped_borrowing_origins.py'), Path('scripts/audit_bridged_allocation_origins.py'),
             Path('scripts/audit_allocation_positions.py'), Path('config/spark.yaml'),
             Path('src/settle/normalize/sources/hypersync_debt.py'),
             *sorted(Path('src/settle/compute').glob('*.py'))]
    r['source_sha256'] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    data = (json.dumps(r, indent=2) + '\n').encode()
    args.output.write_bytes(gzip.compress(data, mtime=0) if args.output.suffix == '.gz' else data)
    print(json.dumps({k:r[k] for k in ('closing_capital','closing_borrowed','remaining_origin_to_classify','borrowed_percent')},indent=2))
    print('Verified routes:',len(r['verified_routes']),'Rejected:',len(r['rejected_routes']))


if __name__ == '__main__':
    main()
