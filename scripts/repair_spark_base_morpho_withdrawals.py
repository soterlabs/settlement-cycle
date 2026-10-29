#!/usr/bin/env python3
"""Refresh exact cash marks for already-recognized Base Morpho fee withdrawals.

Diagnostic snapshot repair only. Does not extract or regenerate settlements.
The current normalizer already has this execution-price branch; older snapshots
have fee income but value the simultaneous withdrawal with a rounded NAV.
"""
import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path
from tempfile import NamedTemporaryFile

from settle.domain.primes import Chain
from settle.extract._keccak import keccak256
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_morpho_fees import fee_mints

HOLDER = '0x2917956eff0b5eaf030abdb4ef4296df775009ca'
VAULT = '0x7bfa7c4f149e7415b73bdedfe609237e29cbf34a'
ACCOUNT = f'base:{HOLDER}:{VAULT}'
DEPOSIT = '0x' + keccak256(b'Deposit(address,address,uint256,uint256)').hex()
WITHDRAW = '0x' + keccak256(b'Withdraw(address,address,address,uint256,uint256)').hex()


def withdrawal_contexts(rows):
    groups, unique = defaultdict(list), {}
    for raw in rows:
        r = LogRow(**raw)
        key = r.block_number, r.log_index
        if key in unique and unique[key] != r:
            raise ValueError('Conflicting Base Morpho withdrawal event')
        unique[key] = r
    for row in unique.values():
        groups[(row.block_number, row.transaction_hash)].append(row)
    contexts = {}
    for (block, tx), logs in groups.items():
        fees = fee_mints(Chain.BASE, logs, {(VAULT, HOLDER)}).get((VAULT, HOLDER), 0)
        if not fees:
            continue
        paid, burned, net, deposits = 0, 0, 0, False
        for r in logs:
            if r.address != VAULT:
                continue
            if r.topic0 == TRANSFER_TOPIC0:
                if len(r.data) != 66:
                    raise ValueError('Invalid Base Morpho share transfer')
                units = int(r.data, 16)
                net += (units if r.topic2.endswith(HOLDER[2:]) else 0) - (units if r.topic1.endswith(HOLDER[2:]) else 0)
            elif r.topic0 == DEPOSIT and r.topic2.endswith(HOLDER[2:]):
                deposits = True
            elif r.topic0 == WITHDRAW and r.topic3.endswith(HOLDER[2:]):
                if len(r.data) != 130:
                    raise ValueError('Invalid Base Morpho withdrawal')
                paid += int(r.data[2:66], 16)
                burned += int(r.data[66:], 16)
        # Only the exact pure-withdrawal branch supported by the normalizer.
        # Mixed deposits, gifts or other share transfers remain untouched.
        if burned and paid and not deposits and burned == fees - net:
            contexts['base:' + tx] = {'block': block, 'timestamp': logs[0].block_time,
                                      'cash': str(D(paid)/10**6), 'burned_units': burned,
                                      'fee_units': fees, 'net_units': net}
    return contexts


def repair_batch(batch, context):
    if context is None:
        return batch, None
    matches = [m for m in batch['movements'] if m['account'] == ACCOUNT]
    if (batch['chain'] != 'base' or batch['block'] != context['block']
            or batch['timestamp'] != context['timestamp'] or len(matches) != 1):
        raise ValueError('Base withdrawal snapshot metadata differs')
    m = matches[0]
    gift, change, mark = D(m['external_income']), D(m['change']), D(m['value_before'])
    if gift <= 0 or mark < 0:
        raise ValueError('Base withdrawal fee was not already recognized')
    # Infer the original per-raw-share price from the already recognized fee.
    # This validates the old shape; no historical balance or new loan is guessed.
    price = gift / context['fee_units']
    if abs(change - D(context['net_units'])*price) > D('1e-8'):
        raise ValueError('Base withdrawal snapshot is not the old NAV-priced shape')
    old_debit = gift - change
    if old_debit <= 0:
        raise ValueError('Base withdrawal has no outgoing value')
    cash = D(context['cash'])
    ratio = cash / old_debit
    actual_fee = cash * D(context['fee_units']) / D(context['burned_units'])
    fixed = {**m, 'value_before': str(mark*ratio), 'external_income': str(actual_fee),
             'change': str(actual_fee-cash)}
    result = {**batch, 'movements': [fixed if x is m else x for x in batch['movements']]}
    return result, {'batch': batch['identity'], 'cash': str(cash), 'old_debit': str(old_debit),
                    'cash_less_old_debit': str(cash-old_debit),
                    'old_fee_income': str(gift), 'new_fee_income': str(actual_fee)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('history', 'events', 'output', 'audit'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    sources = {args.history.resolve(), args.events.resolve()}
    if (args.output.resolve() in sources or args.audit.resolve() in sources
            or args.output.resolve() == args.audit.resolve()):
        raise ValueError('Repair outputs must not overwrite input evidence or one another')
    raw = args.events.read_bytes()
    contexts = withdrawal_contexts(json.loads(gzip.decompress(raw) if args.events.suffix == '.gz' else raw))
    import settle.normalize.allocation_morpho_fees as fee_module
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (args.history, args.events, Path(__file__), Path(fee_module.__file__))}
    changes, seen, count = [], set(), 0
    with NamedTemporaryFile(dir=args.output.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with gzip.open(args.history, 'rt') as source, gzip.open(temporary, 'wt') as target:
            meta = json.loads(next(source))
            if 'diagnostic_base_withdrawal_patch' in meta:
                raise ValueError('Base withdrawal repair already applied')
            meta['diagnostic_base_withdrawal_patch'] = {'input_hashes': hashes}
            target.write(json.dumps(meta)+'\n')
            for line in source:
                batch = json.loads(line)
                fixed, change = repair_batch(batch, contexts.get(batch['identity']))
                target.write(json.dumps(fixed)+'\n')
                count += 1
                if change is not None:
                    changes.append(change)
                    seen.add(batch['identity'])
        if seen != contexts.keys():
            raise ValueError('Snapshot misses observed Base withdrawal transactions')
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)
    result = {'scope': 'Diagnostic exact-cash repair only; no debt or other positions changed.',
              'input_hashes': hashes, 'output_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
              'batches': count, 'repaired_withdrawals': len(changes),
              'cash_less_old_debit_total': str(sum((D(r['cash_less_old_debit']) for r in changes), D(0))),
              'changes': changes}
    args.audit.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
