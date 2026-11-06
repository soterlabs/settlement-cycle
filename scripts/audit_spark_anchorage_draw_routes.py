"""Receipt-proven Anchorage funding; existing principal-return policy is unchanged."""
from decimal import Decimal as D

from audit_spark_usdc_draw_routes import POCKET as POCKET
from audit_spark_usdc_draw_routes import USDC as USDC
from audit_spark_usdc_draw_routes import address as address
from audit_spark_usdc_draw_routes import prove_payments

from settle.compute.spark_anchorage_scenarios import ACCOUNT, ESCROW
from settle.extract.transfer_logs import TRANSFER_TOPIC0 as TRANSFER_TOPIC0


def validate(batch, receipt, *, savings_specs=None):
    selected = [m for m in batch.movements if m.account == ACCOUNT]
    if len(selected) != 1:
        raise ValueError('Missing or duplicate facility movement')
    facility = selected[0]
    if not facility.preserve_basis or facility.external_income or facility.change <= 0:
        raise ValueError('Requires principal deposit')
    proof = prove_payments(batch, receipt, {ESCROW}, savings_specs=savings_specs)
    if len(proof['payments']) != 1 or D(proof['payments'][0]['amount']) != facility.change:
        raise ValueError('Facility deposit does not match normalized principal')
    payment = proof.pop('payments')[0]
    return {**proof, 'facility_deposit_usdc': str(facility.change),
            'verified_borrowed_contribution': payment['borrowed']}

def attribute(history, receipts, start, end):
    from audit_facility_funding import audit
    from audit_spark_ordered_draws import load_savings_specs

    if history.venue_accounts.get('S23') != ACCOUNT:
        raise ValueError('Unexpected Anchorage account')
    specs = load_savings_specs()
    proofs, rejected = [], []
    for batch in history.batches:
        if batch.day > end or not any(m.account == ACCOUNT and m.change > 0 for m in batch.movements):
            continue
        receipt = receipts.get(batch.identity.removeprefix('ethereum:'))
        if receipt is None:
            continue
        try:
            proofs.append(validate(batch, receipt, savings_specs=specs))
        except ValueError as exc:
            rejected.append({'transaction': batch.identity, 'reason': str(exc)})
    result = audit(history, 'S23', start, end, verified_draws={
        p['transaction']: D(p['verified_borrowed_contribution']) for p in proofs})
    result['receipt_proofs'] = proofs
    result['rejected_receipts'] = rejected
    result['principal_policy'] = 'Existing MSC full-principal classification, operator accepted; normalized input already applies July/August returns'
    return result


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
                        help='Already applies the accepted MSC Anchorage principal-return policy')
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
             Path('scripts/audit_spark_ordered_draws.py'), Path('scripts/audit_spark_usdc_draw_routes.py'), Path('scripts/audit_facility_funding.py'),
             Path('config/spark.yaml'), Path('src/settle/compute/spark_anchorage_scenarios.py')]
    result['input_hashes'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    with gzip.open(args.output, 'wt') as dst:
        json.dump(result, dst, indent=2)
    print(json.dumps({k: result[k] for k in ('closing_facility_principal',
        'direct_origin_remaining_before_refinancing', 'closing_origin_not_proved_by_this_audit')}))


if __name__ == '__main__':
    main()
