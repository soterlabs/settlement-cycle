"""Trace S34 origin through Spark's USDC positions, message claims and PSM3 shares.

These are month-end acquisition-capital origins, not current debt allocations.
Normalized message/PSM ownership is reused; receipt proofs identify fresh draws
that transaction-wide netting obscures. Unknown capital is never called debt.
"""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from decimal import localcontext

from audit_allocation_positions import audit
from audit_bridged_allocation_origins import surviving_sources
from audit_scoped_allocation_origin import snapshots
from audit_scoped_borrowing_origins import trace
from audit_spark_cctp_draw_routes import validate
from audit_spark_ordered_draws import load_savings_specs

from settle.domain.config import load_prime_by_id
from settle.normalize.allocation_psm import PsmCapital

VENUES = ('S31', 'S2', 'S8', 'S10', 'S18', 'S26', 'S34', 'S35', 'S39',
          'S41', 'S45', 'S49', 'S53', 'S54', 'S55')
START, END = date(2026, 1, 1), date(2026, 8, 31)


def scope(history, end=END):
    if set(VENUES) - history.venue_accounts.keys():
        raise ValueError('Missing configured USDC origin venue')
    accounts = {history.venue_accounts[v] for v in VENUES}
    accounts.update(a for v in VENUES for a in history.custody_accounts.get(v, []))
    prime = load_prime_by_id('spark')
    psms = {PsmCapital(prime, chain).account for chain in prime.psm}
    claims = {m.account for b in history.batches if b.day <= end
              and any(x.account in accounts for x in b.movements)
              for m in b.movements if m.account.startswith(('cctp:', 'cctpv2:'))}
    aliases = {f'usdc-origin:{i}': a for i, a in enumerate(sorted(claims | psms))}
    if set(aliases) & history.venue_accounts.keys() or accounts & (claims | psms):
        raise ValueError('Overlapping origin accounts')
    return replace(history, venue_accounts={**history.venue_accounts, **aliases}), aliases, accounts | claims | psms, claims


def claim_links(history, claims, end=END):
    by_claim = {a: [] for a in claims}
    for b in history.batches:
        if b.day > end:
            continue
        for m in b.movements:
            if m.account in claims and m.change:
                by_claim[m.account].append((b, m))
    links = []
    for claim, legs in sorted(by_claim.items()):
        legs.sort(key=lambda pair: (pair[0].timestamp, pair[0].chain, pair[0].block, pair[0].log_index))
        if not 1 <= len(legs) <= 2:
            raise ValueError('Claim requires one source and at most one complete receipt')
        source, sent = legs[0]
        if sent.change <= 0 or sent.value_before or sent.external_income:
            raise ValueError('Invalid source claim')
        row = {'claim': claim, 'source': source.identity, 'amount': str(sent.change), 'receipt': None}
        if len(legs) == 2:
            receipt, received = legs[1]
            if (received.change != -sent.change or received.value_before != sent.change
                    or received.external_income or source.chain == receipt.chain
                    or source.timestamp >= receipt.timestamp):
                raise ValueError('Claim receipt amount, chain or order mismatch')
            row['receipt'] = receipt.identity
        links.append(row)
    return links


def attribute(history, receipts, start=START, end=END):
    with localcontext() as ctx:
        ctx.prec = 60
        scoped, aliases, accounts, claims = scope(history, end)
        links = claim_links(history, claims, end)
        specs, proofs, rejected, verified = load_savings_specs(), [], [], {}
        for batch in history.batches:
            if batch.day > end:
                continue
            receipt = receipts.get(batch.identity.removeprefix('ethereum:'))
            if receipt is None:
                continue
            try:
                proof = validate(batch, receipt, savings_specs=specs)
            except ValueError as exc:
                rejected.append({'transaction': batch.identity, 'reason': str(exc)})
                continue
            incoming = {m.account for m in batch.movements if m.account in accounts
                        and m.change - m.external_income > 0}
            paid = {r['claim'] for r in proof['claims']}
            if incoming != paid:
                rejected.append({'transaction': batch.identity, 'reason': 'Other positive scoped accounts need a complete route proof'})
                continue
            verified[batch.identity] = {'accounts': sorted(paid),
                'borrowed': sum((D(r['borrowed']) for r in proof['claims']), D(0)),
                'by_account': {r['claim']: D(r['borrowed']) for r in proof['claims']}}
            proofs.append(proof)
        target = history.venue_accounts['S34']
        origin = trace(scoped, (*VENUES, *aliases), end, snapshot_accounts={target},
                       bound_external_funding=True, verified_group_inflows=verified)
        local = audit(history, 'S34', start, end)
        monthly = snapshots(local, origin, {target}, start, end)
        remaining = surviving_sources(local, origin, target)
        if abs(sum((D(r['unclassified_closing_capital']) for r in remaining), D(0))
               - D(monthly[-1]['unclassified'])) > D('1e-8'):
            raise ValueError('Residual sources do not reconcile')
        # Keep all target events and the receipt witnesses, rather than storing
        # hundreds of thousands of unchanged target snapshots. The full scoped
        # input and deterministic trace reproduce every upstream intermediate.
        keep = {b.identity for b in history.batches if any(
            m.account == target and (m.change or m.external_income) for m in b.movements)} | verified.keys()
        origin['transactions'] = [r for r in origin['transactions'] if r['transaction'] in keep]
        origin['withdrawals'] = {tx: rows for tx, rows in origin['withdrawals'].items() if tx in keep}
        return {'scope': __doc__, 'venue': 'S34', 'origin_group': list(VENUES),
                'start': str(start), 'end': str(end), 'monthly': monthly,
                'closing_capital': local['closing_capital'],
                'closing_borrowed': origin['closing_borrowed_by_account'][target],
                'remaining_sources': remaining, 'bridge_links': links,
                'receipt_proofs': proofs, 'rejected_receipts': rejected, 'origin_trace': origin}


def main():
    import argparse
    import gzip
    import hashlib
    import json
    from pathlib import Path

    from settle.normalize.allocation_history_cache import load_history

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--normalized-history', type=Path, required=True)
    parser.add_argument('--supplement', type=Path,
                        help='Additional already normalized batches; shared identities must match exactly')
    parser.add_argument('--receipts', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    inputs = [args.normalized_history, args.receipts, *([args.supplement] if args.supplement else [])]
    if args.output.resolve() in {p.resolve() for p in inputs}:
        parser.error('Output must not overwrite inputs')
    with gzip.open(args.normalized_history, 'rt') as src:
        key = json.loads(next(src))['fingerprint']
    history = load_history(args.normalized_history, key)
    if history is None:
        raise ValueError('Invalid normalized history')
    if args.supplement:
        with gzip.open(args.supplement, 'rt') as src:
            key = json.loads(next(src))['fingerprint']
        history = merge_history(history, load_history(args.supplement, key))
    with gzip.open(args.receipts, 'rt') as src:
        receipts = json.load(src)
    result = attribute(history, receipts)
    paths = [*inputs, Path(__file__), Path('config/spark.yaml'),
             *[Path('scripts') / name for name in (
                 'audit_spark_cctp_draw_routes.py', 'audit_spark_usdc_draw_routes.py',
                 'audit_spark_ordered_draws.py', 'audit_scoped_borrowing_origins.py',
                 'audit_scoped_allocation_origin.py', 'audit_allocation_positions.py',
                 'audit_bridged_allocation_origins.py')]]
    result['source_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    with gzip.open(args.output, 'wt') as dst:
        json.dump(result, dst, indent=2)
    print(json.dumps(result['monthly'][-1], indent=2))


def merge_history(base, supplement):
    if supplement is None or base.venue_accounts != supplement.venue_accounts or base.custody_accounts != supplement.custody_accounts:
        raise ValueError('Supplement allocation metadata differs')
    existing = {b.identity: b for b in base.batches}
    if len(existing) != len(base.batches):
        raise ValueError('Duplicate base history identity')
    seen = set()
    for batch in supplement.batches:
        if batch.identity in seen or (batch.identity in existing and existing[batch.identity] != batch):
            raise ValueError('Duplicate or conflicting supplemental batch')
        seen.add(batch.identity)
        existing[batch.identity] = batch
    return replace(base, batches=tuple(existing.values()))


if __name__ == '__main__':
    main()
