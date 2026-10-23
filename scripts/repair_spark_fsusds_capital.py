#!/usr/bin/env python3
"""Repair diagnostic fsUSDS marks using exact, paired sUSDS cash movements.

Only canonical vault/cash pairs with complete Deposit/Withdraw and Transfer
witnesses qualify. This does not price or regenerate published settlements.
"""
import argparse
import gzip
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path
from tempfile import NamedTemporaryFile

from settle.domain.config import load_prime_by_id
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import DEPOSIT, WITHDRAW


def repair_batch(batch, receipt):
    prime = load_prime_by_id('spark')
    venues = [v for v in prime.venues if v.id in ('S36', 'S42') and v.chain.value == batch['chain']]
    if len(venues) != 1:
        raise ValueError('Unexpected fsUSDS chain')
    venue = venues[0]
    holder = prime.alm[venue.chain].hex
    prefix = f'{venue.chain.value}:{holder}:'
    share_account, cash_account = prefix + venue.token.address.hex, prefix + venue.underlying.address.hex
    if (receipt['status'] != '0x1' or int(receipt['blockNumber'], 16) != batch['block']
            or batch['identity'] != batch['chain'] + ':' + receipt['transactionHash']):
        raise ValueError('fsUSDS receipt metadata differs')
    if len(batch['movements']) != 2 or {m['account'] for m in batch['movements']} != {share_account, cash_account}:
        raise ValueError('fsUSDS repair requires an isolated vault/cash pair')
    share = next(m for m in batch['movements'] if m['account'] == share_account)
    cash = next(m for m in batch['movements'] if m['account'] == cash_account)
    if D(batch['minted']) or batch['minted_by_ilk'] or any(D(m['external_income']) for m in batch['movements']):
        raise ValueError('Unexpected funding or income in fsUSDS exchange')
    events, transfers, seen = [], {venue.token.address.hex: 0, venue.underlying.address.hex: 0}, set()
    for row in receipt['logs']:
        index = int(row['logIndex'], 16)
        if index in seen:
            raise ValueError('Duplicate fsUSDS receipt log')
        seen.add(index)
        if (row['transactionHash'] != receipt['transactionHash']
                or row['blockNumber'] != receipt['blockNumber'] or row.get('removed', False)):
            raise ValueError('Inconsistent fsUSDS receipt log')
        topics = row['topics']
        if row['address'] in transfers and topics[0] == TRANSFER_TOPIC0:
            if len(topics) != 3 or len(row['data']) != 66:
                raise ValueError('Invalid fsUSDS transfer')
            amount = int(row['data'], 16)
            transfers[row['address']] += (amount if topics[2].endswith(holder[2:]) else 0) - (amount if topics[1].endswith(holder[2:]) else 0)
        if row['address'] == venue.token.address.hex and topics[0] in (DEPOSIT, WITHDRAW):
            if len(row['data']) != 130 or len(topics) != (3 if topics[0] == DEPOSIT else 4):
                raise ValueError('Invalid fsUSDS vault event')
            if any(not t.endswith(holder[2:]) for t in topics[1:]):
                raise ValueError('fsUSDS owner/caller/receiver differs')
            sign = 1 if topics[0] == DEPOSIT else -1
            events.append((sign * int(row['data'][2:66], 16), sign * int(row['data'][66:], 16)))
    if len(events) != 1:
        raise ValueError('fsUSDS repair requires one vault event')
    assets, shares = events[0]
    if (not assets or not shares or transfers[venue.token.address.hex] != shares
            or transfers[venue.underlying.address.hex] != -assets):
        raise ValueError('fsUSDS vault event does not match actual token transfers')
    asset_units = D(assets) / D(10**venue.underlying.decimals)
    if D(share['change']) != asset_units:
        raise ValueError('fsUSDS snapshot is not the old underlying-at-par shape')
    price = -D(cash['change']) / asset_units
    if not price.is_finite() or price <= 0:
        raise ValueError('Invalid observed sUSDS cash price')
    fixed = {**share, 'value_before': str(D(share['value_before']) * price),
             'change': str(-D(cash['change']))}
    result = {**batch, 'movements': [fixed if m is share else m for m in batch['movements']]}
    return result, {'identity': batch['identity'], 'venue': venue.id, 'day': batch['day'],
                    'underlying_usd_price': str(price), 'old_change': share['change'],
                    'new_change': fixed['change'],
                    'old_false_gap': str(D(share['change']) + D(cash['change']))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('history', 'evidence', 'output', 'audit'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if len({p.resolve() for p in (args.history, args.evidence, args.output, args.audit)}) != 4:
        raise ValueError('Repair inputs and outputs must be distinct')
    evidence = json.loads(args.evidence.read_text())
    witnesses = {r['batch']['identity']: r for r in evidence}
    if len(witnesses) != len(evidence):
        raise ValueError('Duplicate fsUSDS witness')
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (args.history, args.evidence, Path(__file__), Path('config/spark.yaml'))}
    changes, seen, count = [], set(), 0
    with NamedTemporaryFile(dir=args.output.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with gzip.open(args.history, 'rt') as source, gzip.open(temporary, 'wt') as target:
            header = json.loads(next(source))
            if 'diagnostic_fsusds_patch' in header:
                raise ValueError('fsUSDS repair already applied')
            header['diagnostic_fsusds_patch'] = {'input_hashes': hashes}
            target.write(json.dumps(header) + '\n')
            for line in source:
                batch = json.loads(line)
                witness = witnesses.get(batch['identity'])
                if witness:
                    if batch != witness['batch'] or batch['identity'] in seen:
                        raise ValueError('fsUSDS snapshot differs from witnessed input')
                    seen.add(batch['identity'])
                    batch, change = repair_batch(batch, witness['receipt'])
                    changes.append(change)
                target.write(json.dumps(batch) + '\n')
                count += 1
        if seen != witnesses.keys():
            raise ValueError('Missing fsUSDS witness in history')
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)
    args.audit.write_text(json.dumps({'batches': count, 'repaired': len(changes),
        'input_hashes': hashes, 'output_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
        'false_receipts_usd': str(sum((max(D(r['old_false_gap']), D(0)) for r in changes), D(0))),
        'false_outflows_usd': str(sum((max(-D(r['old_false_gap']), D(0)) for r in changes), D(0))),
        'changes': changes}, indent=2) + '\n')


if __name__ == '__main__':
    main()
