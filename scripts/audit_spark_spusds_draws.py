"""Receipt-proven Sky draws into spUSDS, independently of Savings token legs.

Only new USDS borrowing is attributed. Opening cash and other receipts remain
unknown; other payments consume borrowing first. No settlement code calls this.
"""
from decimal import Decimal as D
from decimal import localcontext

from audit_spark_ordered_draws import (
    ALM,
    BUFFER,
    USDS,
    WAD,
    address,
    authenticate_draw,
    index,
    prefix_borrowed_cash,
    savings_receipts,
)
from audit_spark_spusds_returns import SPUSDS

from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0

POOL = '0xc13e21b648a5ee794902342038ff3adab66be987'
SUPPLY = '0x' + keccak256(b'Supply(address,address,address,uint256,uint16)').hex()


def validate(batch, receipt, *, savings_specs=None):
    with localcontext() as ctx:
        ctx.prec = 60
        logs, drawn, wiped, positive, negative, draws, wipes = authenticate_draw(batch, receipt)
        ms = {m.account: m for m in batch.movements}
        if len(ms) != len(batch.movements) or not {USDS, SPUSDS} <= ms.keys():
            raise ValueError('Missing or duplicate position/cash movement')
        cash, position = ms[USDS], ms[SPUSDS]
        if any(m.external_income or m.preserve_basis for m in (cash, position)) or position.change <= 0:
            raise ValueError('Requires ordinary positive spUSDS acquisition')
        transfers = [log for log in logs if log['address'] == USDS.split(':')[-1]
                     and log['topics'][0] == TRANSFER_TOPIC0]
        mints = [log for log in transfers if int(log['topics'][1], 16) == 0 and address(log['topics'][2]) == BUFFER]
        ingress = [log for log in transfers if address(log['topics'][1]) == BUFFER and address(log['topics'][2]) == ALM]
        payments = [log for log in transfers if address(log['topics'][1]) == ALM and address(log['topics'][2]) == SPUSDS.split(':')[-1]]
        supplies = [log for log in logs if log['address'] == POOL and log['topics'][0] == SUPPLY
                    and address(log['topics'][1]) == USDS.split(':')[-1]]
        if len(mints) != 1 or len(ingress) != 1 or len(payments) != 1 or len(supplies) != 1:
            raise ValueError('Requires single draw ingress and SparkLend supply')
        mint, incoming, payment, supply = mints[0], ingress[0], payments[0], supplies[0]
        paid = D(int(payment['data'], 16)) / WAD
        if (D(int(mint['data'], 16)) / WAD != drawn or D(int(incoming['data'], 16)) / WAD != drawn
                or not index(positive[0]) < index(mint) < index(draws[0]) < index(incoming) < index(payment) < index(supply)
                or address(supply['topics'][2]) != ALM or address(supply['data'][2:66]) != ALM
                or int(supply['data'][66:130], 16) != int(payment['data'], 16)
                or abs(position.change - paid) > D('1e-8')
                or any(index(log) <= index(supply) for log in negative + wipes)):
            raise ValueError('Supply/debt order or normalized acquisition mismatch')
        shares = [log for log in logs if log['address'] == SPUSDS.split(':')[-1]
                  and log['topics'][0] == TRANSFER_TOPIC0
                  and ALM in (address(log['topics'][1]), address(log['topics'][2]))]
        # aToken mint transfers include previously accrued interest, so their
        # amount is deliberately NOT used as deposited principal.
        if (len(shares) != 1 or int(shares[0]['topics'][1], 16) != 0
                or address(shares[0]['topics'][2]) != ALM
                or not index(payment) < index(shares[0]) < index(supply)):
            raise ValueError('Additional spUSDS movements require separate proof')
        cash_events = [log for log in transfers if ALM in (address(log['topics'][1]), address(log['topics'][2]))]
        balance = cash.value_before
        if balance < 0:
            raise ValueError('Negative opening cash')
        for log in cash_events:
            sender, recipient = address(log['topics'][1]), address(log['topics'][2])
            if sender == recipient == ALM:
                raise ValueError('Self transfer requires separate proof')
            amount = D(int(log['data'], 16)) / WAD
            balance += amount if recipient == ALM else -amount
            if balance < 0:
                raise ValueError('Negative ordered cash')
        if balance != cash.value_before + cash.change:
            raise ValueError('Receipt cash does not reconcile')
        prefix = [log for log in cash_events if index(log) <= index(payment)]
        known, other = prefix_borrowed_cash(prefix, incoming, payment, cash.value_before)
        return {'transaction': batch.identity, 'gross_draw_usds': str(drawn),
                'gross_wipe_usds': str(wiped), 'usds_supplied': str(paid),
                'other_payments_before_supply': str(other),
                'savings_cash_received': savings_receipts(batch, logs, savings_specs),
                'verified_borrowed_contribution': str(min(known, position.change))}


def attribute(history, receipts, start, end):
    from audit_allocation_positions import audit
    from audit_scoped_allocation_origin import rank_residual_sources, snapshots
    from audit_scoped_borrowing_origins import trace
    from audit_spark_ordered_draws import load_savings_specs

    if history.venue_accounts.get('S1') != SPUSDS or history.custody_accounts.get('S1'):
        raise ValueError('Unexpected spUSDS allocation identity')
    with localcontext() as ctx:
        ctx.prec = 60
        specs = load_savings_specs()
        proofs, rejected = [], []
        for batch in history.batches:
            if batch.day > end or not any(m.account == SPUSDS for m in batch.movements):
                continue
            receipt = receipts.get(batch.identity.removeprefix('ethereum:'))
            if receipt is None:
                continue
            try:
                proofs.append(validate(batch, receipt, savings_specs=specs))
            except ValueError as exc:
                rejected.append({'transaction': batch.identity, 'reason': str(exc)})
        verified = {p['transaction']: D(p['verified_borrowed_contribution']) for p in proofs}
        origin = trace(history, ('S1',), end, bound_external_funding=True, verified_draws=verified)
        local = audit(history, 'S1', start, end)
        return {'scope': __doc__, 'venue': 'S1', 'origin_group': ['S1'],
                'start': str(start), 'end': str(end),
                'closing_capital': local['closing_capital'],
                'closing_borrowed': origin['closing_borrowed_by_account'][SPUSDS],
                'monthly': snapshots(local, origin, {SPUSDS}, start, end),
                'remaining_sources': rank_residual_sources(history, {SPUSDS}, local, origin, end),
                'receipt_proofs': proofs, 'rejected_receipts': rejected, 'origin_trace': origin}


def main():
    import argparse
    import gzip
    import hashlib
    import json
    from datetime import date
    from pathlib import Path

    from settle.normalize.allocation_history_cache import load_history

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--normalized-history', type=Path, required=True,
                        help='Already adapted history; do not apply executed-spell adapters twice')
    parser.add_argument('--receipts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() in {args.normalized_history.resolve(), args.receipts.resolve()}:
        parser.error('Output must not overwrite inputs')
    with gzip.open(args.normalized_history, 'rt') as src:
        key = json.loads(next(src))['fingerprint']
    history = load_history(args.normalized_history, key)
    if history is None:
        raise ValueError('Invalid normalized history')
    with gzip.open(args.receipts, 'rt') as src:
        receipts = json.load(src)
    result = attribute(history, receipts, date(2026, 1, 1), date(2026, 8, 31))
    paths = [args.normalized_history, args.receipts, Path(__file__),
             *[Path('scripts') / name for name in (
                 'audit_spark_ordered_draws.py', 'audit_spark_draw_routes.py',
                 'audit_spark_spusds_returns.py', 'audit_scoped_allocation_origin.py',
                 'audit_scoped_borrowing_origins.py', 'audit_allocation_positions.py')],
             Path('config/spark.yaml')]
    result['source_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    result['input_sha256'] = hashlib.sha256(args.normalized_history.read_bytes()).hexdigest()
    with gzip.open(args.output, 'wt') as dst:
        json.dump(result, dst, indent=2)
    print(json.dumps(result['monthly'][-1], indent=2))


if __name__ == '__main__':
    main()
