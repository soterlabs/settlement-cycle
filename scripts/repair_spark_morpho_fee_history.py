#!/usr/bin/env python3
"""Repair only proven Morpho V2 fee legs in an immutable tracing snapshot.

The output is a separately fingerprinted diagnostic input, not a fresh full
extraction and not a published settlement. All other batches and debt amounts
remain unchanged. Complete inception-to-pin ALM share transfers are required.
"""
import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from decimal import Decimal as D
from pathlib import Path

from settle.domain.primes import Chain
from settle.extract._keccak import keccak256
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_morpho_fees import V2_VAULTS, fee_mints

HOLDER = '0x1601843c5e9bc251a3272907010afa41fa18347e'
DEPOSIT = '0x' + keccak256(b'Deposit(address,address,uint256,uint256)').hex()
WITHDRAW = '0x' + keccak256(b'Withdraw(address,address,address,uint256,uint256)').hex()


def fee_contexts(rows):
    groups, unique = defaultdict(list), {}
    for raw in rows:
        r = LogRow(**raw)
        key = r.block_number, r.log_index
        if key in unique and unique[key] != r:
            raise ValueError('Conflicting canonical share event')
        unique[key] = r
    for r in sorted(unique.values(), key=lambda x: (x.block_number, x.log_index)):
        groups[(r.block_number, r.transaction_hash)].append(r)
    holdings = defaultdict(int)
    contexts = {}
    tracked = {(v, HOLDER) for v in V2_VAULTS[Chain.ETHEREUM]}
    for (block, tx), logs in groups.items():
        fees = fee_mints(Chain.ETHEREUM, logs, tracked)
        before = dict(holdings)
        changes, deposits, withdrawals = defaultdict(int), defaultdict(list), defaultdict(list)
        for r in logs:
            if (r.address, HOLDER) not in tracked:
                continue
            if r.topic0 == TRANSFER_TOPIC0:
                amount = int(r.data, 16)
                change = (amount if r.topic2.endswith(HOLDER[2:]) else 0) - (amount if r.topic1.endswith(HOLDER[2:]) else 0)
                changes[r.address] += change
                holdings[r.address] += change
                if holdings[r.address] < 0:
                    raise ValueError('Incomplete inception share history')
            elif ((r.topic0 == DEPOSIT and r.topic2.endswith(HOLDER[2:]))
                  or (r.topic0 == WITHDRAW and r.topic3.endswith(HOLDER[2:]))):
                if len(r.data) != 130:
                    raise ValueError('Invalid canonical ERC4626 event')
                values = (D(int(r.data[2:66], 16))/10**6, int(r.data[66:], 16))
                (deposits if r.topic0 == DEPOSIT else withdrawals)[r.address].append(values)
        for (vault, _), amount in fees.items():
            if not amount:
                continue
            contexts[('ethereum:' + tx, f'ethereum:{HOLDER}:{vault}')] = {
                'block': block, 'timestamp': logs[0].block_time, 'units_before': before.get(vault, 0),
                'net_units': changes[vault], 'fee_units': amount,
                'deposits': deposits[vault], 'withdrawals': withdrawals[vault],
            }
    return contexts


def repair_batch(batch, contexts, prices=None):
    result = dict(batch)
    movements = []
    applied = []
    for m in batch['movements']:
        ctx = contexts.get((batch['identity'], m['account']))
        if ctx is None:
            movements.append(m)
            continue
        if (batch['chain'] != 'ethereum' or batch['block'] != ctx['block']
                or batch['timestamp'] != ctx['timestamp'] or D(m['external_income']) != 0):
            raise ValueError('Fee repair requires the reviewed unclassified snapshot shape')
        before, net, fees = ctx['units_before'], ctx['net_units'], ctx['fee_units']
        old_before, old_change = D(m['value_before']), D(m['change'])
        if before:
            unit_price = old_before / D(before)
        elif net and not ctx['deposits']:
            unit_price = old_change / D(net)
        else:
            quote = (prices or {}).get(batch['identity'] + '|' + m['account'])
            if quote is None or quote['block'] != batch['block']:
                raise ValueError('Cannot recover historical fee share price from this snapshot')
            unit_price = D(quote['price_usd']) / 10**18
        if unit_price <= 0:
            raise ValueError('Invalid historical share price')
        deposits = sum((x[0] for x in ctx['deposits']), D(0))
        withdrawn = sum((x[0] for x in ctx['withdrawals']), D(0))
        burned = sum(x[1] for x in ctx['withdrawals'])
        expected = deposits if deposits and not ctx['withdrawals'] and net > 0 else D(net) * unit_price
        if abs(old_change - expected) > D('1e-8'):
            raise ValueError('Old snapshot differs from the reconstructed share/cash movement')
        fee_value = D(fees) * unit_price
        change, mark = old_change, old_before
        if burned and not deposits and burned == fees - net:
            # Same exact execution-price branch as fresh normalization.
            fee_value = withdrawn * D(fees) / D(burned)
            mark = withdrawn * D(before) / D(burned)
            change = fee_value - withdrawn
        elif deposits and not ctx['withdrawals'] and net > 0:
            change = deposits + fee_value
        fixed = {**m, 'value_before': str(mark), 'change': str(change), 'external_income': str(fee_value)}
        movements.append(fixed)
        applied.append({'batch': batch['identity'], 'account': m['account'], 'block': batch['block'],
                        'fee_income': str(fee_value), 'old_change': m['change'], 'new_change': str(change)})
    result['movements'] = movements
    return result, applied


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--history', type=Path, required=True)
    p.add_argument('--events', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--audit', type=Path, required=True)
    p.add_argument('--unit-prices', type=Path, help='Exact-block normalizer quotes for otherwise unprovable opening prices')
    args = p.parse_args()
    if args.history.resolve() == args.output.resolve():
        raise ValueError('Original history must remain immutable')
    raw = args.events.read_bytes()
    rows = json.loads(gzip.decompress(raw) if args.events.suffix == '.gz' else raw)
    contexts = fee_contexts(rows)
    prices = json.loads(args.unit_prices.read_text()) if args.unit_prices else {}
    import settle.normalize.allocation_morpho_fees as fee_module

    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (args.history, args.events, Path(__file__), Path(fee_module.__file__))}
    if args.unit_prices:
        hashes[str(args.unit_prices)] = hashlib.sha256(args.unit_prices.read_bytes()).hexdigest()
    temporary = args.output.with_suffix(args.output.suffix + '.tmp')
    changes = []
    try:
        with gzip.open(args.history, 'rt') as src, gzip.open(temporary, 'wt') as out:
            meta = json.loads(next(src))
            if 'diagnostic_fee_patch' in meta:
                raise ValueError('Fee snapshot already repaired')
            meta['diagnostic_fee_patch'] = {'original_fingerprint': meta['fingerprint'], 'input_hashes': hashes}
            meta['fingerprint'] = hashlib.sha256(json.dumps(meta['diagnostic_fee_patch'], sort_keys=True).encode()).hexdigest()
            out.write(json.dumps(meta) + '\n')
            for line in src:
                batch = json.loads(line)
                updated, applied = repair_batch(batch, contexts, prices)
                out.write(json.dumps(updated) + '\n' if applied else line)
                changes.extend(applied)
        used = {(r['batch'], r['account']) for r in changes}
        if used != set(contexts):
            raise ValueError('Canonical fee event lacks an original normalized position')
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)
    audit = {'scope': 'Fee-only diagnostic history repair, not a full latest-code extraction',
             'input_hashes': hashes, 'output_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
             'changed_positions': len(changes), 'changed_batches': len({r['batch'] for r in changes}),
             'fee_income_total': str(sum((D(r['fee_income']) for r in changes), D(0))), 'changes': changes}
    args.audit.write_text(json.dumps(audit, indent=2) + '\n')
    print(json.dumps({k: v for k, v in audit.items() if k != 'changes'}, indent=2))


if __name__ == '__main__':
    main()
