"""Carry separately traced cash, vault and CCTP funding into Anchorage deposits.

The upstream ledger excludes Anchorage. It cannot circularly prove a loan's
origin from its own later returns. Receipt paths consume only that independently
released basis, fresh authenticated draws, or separately identified Savings cash.
"""
from decimal import Decimal as D
from decimal import localcontext

from audit_spark_ordered_draws import DRAW, VAULT, authenticate_draw, savings_receipts
from audit_spark_usdc_draw_routes import (
    ALM,
    BUFFER,
    CONVERTER,
    DAI,
    POCKET,
    PSM,
    USDC,
    USDS,
    WAD,
    ZERO,
    address,
    index,
)

from settle.compute.spark_anchorage_scenarios import ACCOUNT, ESCROW
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_bridges import MESSAGE_RECEIVED, MINT_AND_WITHDRAW

TRANSMITTER = '0x0a992d191deec32afe36203ad87d7d289a738f81'
MESSENGER = '0xbd3fa81b58ba92a82136038b25adec7066af3155'
EPS = D('1e-8')


def receipt_logs(batch, receipt):
    tx = receipt['transactionHash'].lower()
    if (batch.identity != 'ethereum:' + tx or batch.chain != 'ethereum'
            or int(receipt['status'], 16) != 1 or int(receipt['blockNumber'], 16) != batch.block):
        raise ValueError('Upstream receipt metadata mismatch')
    logs = sorted(receipt['logs'], key=index)
    if len({index(log) for log in logs}) != len(logs) or any(
            log.get('removed') or log['transactionHash'].lower() != tx
            or int(log['blockNumber'], 16) != batch.block for log in logs):
        raise ValueError('Upstream receipt log mismatch')
    return logs


def source_credits(batch, logs, witness, venue_accounts):
    """Receipt-indexed credits backed by releases in the independent source ledger."""
    ms = {m.account: m for m in batch.movements}
    if len(ms) != len(batch.movements):
        raise ValueError('Duplicate upstream account movement')
    transfers = [log for log in logs if log['address'] == USDC
                 and log['topics'][0] == TRANSFER_TOPIC0 and address(log['topics'][2]) == ALM]
    credits = {}
    for venue in ('S2', 'S8', 'S10', 'S18'):
        account = venue_accounts[venue]
        movement = ms.get(account)
        if movement is None or movement.change - movement.external_income >= 0:
            continue
        received = [log for log in transfers if address(log['topics'][1]) == account.split(':')[-1]]
        amount = sum((D(int(log['data'], 16)) / 10**6 for log in received), D(0))
        if not amount or abs(amount + movement.change - movement.external_income) > EPS:
            continue  # Netting multiple vault operations requires another proof.
        borrowed = D(witness['withdrawals'].get(account, '0'))
        if borrowed < 0 or borrowed > amount + EPS:
            raise ValueError('Source borrowing exceeds received redemption')
        for log in received:
            credits[index(log)] = min(amount, borrowed) * D(int(log['data'], 16)) / 10**6 / amount
    used, matched_claims = set(credits), set()
    for message in logs:
        if message['address'] != TRANSMITTER or message['topics'][0] != MESSAGE_RECEIVED:
            continue
        raw = bytes.fromhex(message['data'][2:])
        if len(raw) < 128:
            raise ValueError('Truncated upstream message')
        domain, sender = int.from_bytes(raw[:32], 'big'), '0x' + raw[44:64].hex()
        claim = f"cctp:{domain}:{int(message['topics'][2], 16)}:{sender}"
        movement = ms.get(claim)
        if movement is None:
            continue
        offset = int.from_bytes(raw[64:96], 'big')
        length = int.from_bytes(raw[offset:offset+32], 'big')
        body = raw[offset+32:offset+32+length]
        if length != 132 or len(body) != 132:
            raise ValueError('Invalid upstream message body')
        raw_amount = int.from_bytes(body[68:100], 'big')
        amount = D(raw_amount) / 10**6
        metadata = witness['claim_metadata'].get(claim)
        if (metadata is None or '0x' + body[16:36].hex() != metadata['token']
                or '0x' + body[112:132].hex() != metadata['owner']):
            raise ValueError('Incoming message owner/token differs from authenticated source')
        if ('0x' + body[48:68].hex() != ALM or movement.change != -amount
                or movement.value_before != amount or movement.external_income):
            raise ValueError('Incoming message differs from normalized claim')
        mint_events = [log for log in logs if log['address'] == MESSENGER
                       and log['topics'][0] == MINT_AND_WITHDRAW
                       and address(log['topics'][1]) == ALM and address(log['topics'][2]) == USDC
                       and int(log['data'], 16) == raw_amount and index(log) < index(message)]
        matches = [log for log in transfers if address(log['topics'][1]) == ZERO
                   and int(log['data'], 16) == raw_amount and index(log) not in used
                   and any(index(log) < index(event) < index(message) for event in mint_events)]
        if len(matches) != 1:
            raise ValueError('Ambiguous CCTP cash receipt')
        borrowed = D(witness['withdrawals'].get(claim, '0'))
        if borrowed < 0 or borrowed > amount + EPS:
            raise ValueError('Claim borrowing exceeds incoming message')
        used.add(index(matches[0]))
        if claim in matched_claims:
            raise ValueError('Duplicate upstream message claim')
        matched_claims.add(claim)
        credits[index(matches[0])] = min(amount, borrowed)
    expected = {a for a, value in witness['withdrawals'].items() if a.startswith('cctp:') and D(value) > 0}
    if expected - matched_claims:
        raise ValueError('Known upstream claim lacks a matching receipt')
    return credits


def validate(batch, receipt, witness, venue_accounts, *, savings_specs=None, savings_origin=False):
    with localcontext() as ctx:
        ctx.prec = 80
        logs = receipt_logs(batch, receipt)
        ms = {m.account: m for m in batch.movements}
        if len(ms) != len(batch.movements) or ACCOUNT not in ms:
            raise ValueError('Missing or duplicate facility')
        facility = ms[ACCOUNT]
        if not facility.preserve_basis or facility.external_income or facility.change <= 0:
            raise ValueError('Requires principal deposit')
        if savings_specs is None:
            from audit_spark_ordered_draws import load_savings_specs
            savings_specs = load_savings_specs()
        saved = savings_receipts(batch, logs, savings_specs)
        credits = {} if savings_origin else source_credits(batch, logs, witness, venue_accounts)
        tokens = {USDS.split(':')[-1]: WAD, DAI: WAD, USDC: D(10)**6}
        transfers = [log for log in logs if log['address'] in tokens and log['topics'][0] == TRANSFER_TOPIC0]
        if savings_origin:
            for log in transfers:
                venue = savings_specs.get(address(log['topics'][1]))
                if (venue is not None and venue.underlying.address.hex == log['address']
                        and address(log['topics'][2]) == ALM):
                    credits[index(log)] = D(int(log['data'], 16)) / tokens[log['address']]
        def edges(token, sender, recipient, raw=None):
            return [log for log in transfers if log['address'] == token
                    and address(log['topics'][1]) == sender and address(log['topics'][2]) == recipient
                    and (raw is None or int(log['data'], 16) == raw)]
        drawn = D(0)
        draw_credit = {}
        if any(log['address'] == VAULT and log['topics'][0] == DRAW for log in logs):
            _, drawn, _, positive, _, draws, _ = authenticate_draw(batch, receipt)
            mint = edges(USDS.split(':')[-1], ZERO, BUFFER, int(drawn * WAD))
            incoming = edges(USDS.split(':')[-1], BUFFER, ALM, int(drawn * WAD))
            if (len(mint) != 1 or len(incoming) != 1
                    or not index(positive[0]) < index(mint[0]) < index(draws[0]) < index(incoming[0])):
                raise ValueError('Missing fresh draw ingress')
            if not savings_origin:
                draw_credit[index(incoming[0])] = drawn
        elif batch.minted > 0:
            raise ValueError('Positive net debt without a draw')
        # Map authenticated output receipts to their earlier conversion payment.
        conversion_inputs, converted_receipts, consumed = set(), {}, set()
        for payment in edges(USDS.split(':')[-1], ALM, CONVERTER):
            raw = int(payment['data'], 16)
            burns = [log for log in edges(USDS.split(':')[-1], CONVERTER, ZERO, raw)
                     if index(log) > index(payment) and index(log) not in consumed]
            mints = [log for log in edges(DAI, ZERO, ALM, raw)
                     if index(log) > index(payment) and index(log) not in consumed]
            if not burns or not mints or not index(payment) < index(burns[0]) < index(mints[0]):
                raise ValueError('Missing matched USDS/DAI conversion')
            conversion_inputs.add(index(payment))
            converted_receipts[index(mints[0])] = index(payment)
            consumed.update((index(burns[0]), index(mints[0])))
        for payment in edges(DAI, ALM, PSM):
            raw = int(payment['data'], 16)
            incoming = [log for log in edges(USDC, POCKET, ALM, raw // 10**12)
                        if index(log) > index(payment) and index(log) not in consumed]
            if raw % 10**12 or not incoming:
                raise ValueError('Missing matched PSM purchase')
            conversion_inputs.add(index(payment))
            converted_receipts[index(incoming[0])] = index(payment)
            consumed.add(index(incoming[0]))
        balances, known = {}, {}
        for token in tokens:
            account = 'ethereum:' + ALM + ':' + token
            movement = ms.get(account)
            if movement is None:
                if any(log['address'] == token and ALM in (address(log['topics'][1]), address(log['topics'][2])) for log in transfers):
                    raise ValueError('Missing cash movement for used token')
                balances[token] = known[token] = D(0)
                continue
            if movement.value_before < 0 or movement.external_income or movement.preserve_basis:
                raise ValueError('Requires ordinary cash balances')
            balances[token] = movement.value_before
            origin = D(0) if savings_origin else D(witness['opening_borrowed_cash'].get(account, '0'))
            if not origin.is_finite() or origin < 0 or origin > balances[token] + EPS:
                raise ValueError('Opening borrowing exceeds cash')
            known[token] = min(origin, balances[token])
        released, deposits, applied = {}, [], set()
        for log in transfers:
            sender, recipient = address(log['topics'][1]), address(log['topics'][2])
            if ALM not in (sender, recipient):
                continue
            if sender == recipient:
                raise ValueError('Self-transfer needs separate proof')
            token, n = log['address'], index(log)
            value = D(int(log['data'], 16)) / tokens[token]
            if recipient == ALM:
                candidates = [draw_credit.get(n, D(0)), credits.get(n, D(0)),
                              released.get(converted_receipts.get(n), D(0))]
                credit = max(candidates)  # Alternative evidence for the same cash, never additive.
                if credit < 0 or credit > value + EPS:
                    raise ValueError('Incoming origin exceeds cash')
                balances[token] += value
                known[token] += min(value, credit)
                if n in credits:
                    applied.add(n)
            else:
                if value > balances[token]:
                    raise ValueError('Negative ordered cash')
                target = token == USDC and recipient == ESCROW
                route = n in conversion_inputs or target
                amount = max(D(0), value - (balances[token] - known[token])) if route else min(value, known[token])
                balances[token] -= value
                known[token] -= amount
                released[n] = amount
                if target:
                    deposits.append((value, amount))
        if applied != credits.keys():
            raise ValueError('Unused receipt origin credit')
        for token, balance in balances.items():
            movement = ms.get('ethereum:' + ALM + ':' + token)
            if movement is not None and balance != movement.value_before + movement.change:
                raise ValueError('Final cash does not reconcile')
        if len(deposits) != 1 or deposits[0][0] != facility.change:
            raise ValueError('Escrow payment differs from principal deposit')
        return {'transaction': batch.identity, 'facility_deposit_usdc': str(facility.change),
                'verified_origin': str(deposits[0][1]), 'origin_kind': 'savings' if savings_origin else 'borrowed',
                'gross_draw_usds': str(drawn), 'savings_cash_received': saved,
                'source_receipt_credits': {str(k): str(v) for k, v in credits.items()}}


def build_support(history, bridge_receipts, loan_ids, end):
    from audit_scoped_borrowing_origins import trace
    from audit_spark_usdc_origin_graph import VENUES, claim_links, scope, verified_routes

    scoped, aliases, accounts, claims = scope(history, end)
    if ACCOUNT in accounts:
        raise ValueError('Loan cannot prove its own upstream funding')
    links = claim_links(history, claims, end)
    _, _, verified = verified_routes(history, bridge_receipts, accounts, end)
    cash_accounts = {history.venue_accounts['S26'], history.venue_accounts['S31']}
    origin = trace(scoped, (*VENUES, *aliases), end, snapshot_accounts=cash_accounts,
                   bound_external_funding=True, verified_group_inflows=verified)
    raw_usdc = {history.venue_accounts[v].split(':')[0]: history.venue_accounts[v].split(':')
                for v in ('S26', 'S39', 'S45', 'S49', 'S53', 'S55')}
    metadata = {}
    for link in links:
        chain = link['source'].split(':')[0]
        if chain in raw_usdc:
            _, owner, token = raw_usdc[chain]
            metadata[link['claim']] = {'owner': owner, 'token': token, 'source': link['source']}
    previous, witnesses = dict.fromkeys(cash_accounts, '0'), {}
    for row in origin['transactions']:
        if row['transaction'] in loan_ids:
            withdrawals = row['withdrawn_borrowed_basis']
            witnesses[row['transaction']] = {'opening_borrowed_cash': dict(previous),
                'withdrawals': withdrawals,
                'claim_metadata': {a: metadata[a] for a in withdrawals if a in metadata}}
        previous = row['borrowed_by_account']
    return {'venues': history.venue_accounts, 'transactions': witnesses,
            'source_venues': list(VENUES), 'verified_source_transactions': sorted(verified)}


def attribute(history, receipts, support, start, end):
    from audit_scoped_allocation_origin import month_ends
    from audit_spark_anchorage_draw_routes import attribute as direct_audit
    from audit_spark_ordered_draws import load_savings_specs

    baseline = direct_audit(history, receipts, start, end)
    specs, proofs, rejected = load_savings_specs(), {}, []
    with localcontext() as ctx:
        ctx.prec = 80
        for batch in history.batches:
            if batch.day > end or not any(m.account == ACCOUNT and m.change > 0 for m in batch.movements):
                continue
            receipt = receipts.get(batch.identity.removeprefix('ethereum:'))
            witness = support['transactions'].get(batch.identity)
            if receipt is None or witness is None:
                continue
            try:
                borrowed = validate(batch, receipt, witness, support['venues'], savings_specs=specs)
                savings = validate(batch, receipt, witness, support['venues'], savings_specs=specs, savings_origin=True)
            except ValueError as exc:
                rejected.append({'transaction': batch.identity, 'reason': str(exc)})
                continue
            proofs[batch.identity] = {'borrowed': borrowed, 'savings': savings}
        capital = borrowed = savings = D(0)
        events, monthly = [], []
        cursor = 0
        for cutoff in month_ends(start, end):
            rows = baseline['transactions']
            while cursor < len(rows) and rows[cursor]['day'] <= str(cutoff):
                row = rows[cursor]
                cursor += 1
                change = D(row['principal_change'])
                capital += change
                if change < 0:
                    # Preserve the accepted borrowed-principal-first return
                    # policy. Any further return consumes identified Savings
                    # before unknown funds, keeping explained origin conservative.
                    outstanding = -change
                    released = min(borrowed, outstanding)
                    borrowed -= released
                    savings = max(D(0), savings - (outstanding - released))
                    extra = {'borrowed_added': '0', 'savings_added': '0'}
                else:
                    proof = proofs.get(row['transaction'])
                    known = max(D(row['direct_draw_link']), D(proof['borrowed']['verified_origin']) if proof else D(0))
                    external = D(proof['savings']['verified_origin']) if proof else D(0)
                    if known + external > change + EPS:
                        raise ValueError('Independent funding bounds exceed the same deposit')
                    borrowed += known
                    savings += external
                    extra = {'borrowed_added': str(known), 'savings_added': str(external)}
                if min(capital, borrowed, savings) < 0 or borrowed + savings > capital + EPS:
                    raise ValueError('Facility origin components exceed principal')
                events.append({'transaction': row['transaction'], 'day': row['day'],
                    'principal_change': row['principal_change'], **extra,
                    'closing_origin': {'borrowed': str(borrowed), 'savings': str(savings),
                                      'unclassified': str(max(D(0), capital - borrowed - savings))}})
            if capital != D(baseline['daily'][str(cutoff)]['facility_principal']):
                raise ValueError('Changed accepted facility principal')
            monthly.append({'month_end': str(cutoff), 'capital': str(capital), 'borrowed': str(borrowed),
                'identified_savings': str(savings), 'unclassified': str(max(D(0), capital - borrowed - savings)),
                'borrowed_percent': str(borrowed / capital * 100) if capital else None,
                'explained_percent': str((borrowed + savings) / capital * 100) if capital else None})
        return {'scope': __doc__, 'venue': 'S23', 'start': str(start), 'end': str(end),
            'monthly': monthly, 'transactions': events, 'receipt_proofs': list(proofs.values()),
            'rejected_receipts': rejected, 'source_support': support,
            'principal_policy': baseline['principal_policy'],
            'principal_returns_through_end': baseline['principal_returns_through_end']}


def main():
    import argparse
    import gzip
    import hashlib
    import json
    from datetime import date
    from pathlib import Path

    from audit_spark_usdc_origin_graph import merge_history

    from settle.normalize.allocation_history_cache import load_history

    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('facility-history', 'source-history', 'source-supplement', 'receipts', 'bridge-receipts', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    inputs = [args.facility_history, args.source_history, args.source_supplement, args.receipts, args.bridge_receipts]
    if args.output.resolve() in {p.resolve() for p in inputs}:
        parser.error('Output must not overwrite an input')
    def load(path):
        with gzip.open(path, 'rt') as src:
            key = json.loads(next(src))['fingerprint']
        history = load_history(path, key)
        if history is None:
            raise ValueError('Invalid normalized history')
        return history
    history = load(args.facility_history)
    source = merge_history(load(args.source_history), load(args.source_supplement))
    with gzip.open(args.receipts, 'rt') as src:
        receipts = json.load(src)
    with gzip.open(args.bridge_receipts, 'rt') as src:
        bridge_receipts = json.load(src)
    start, end = date(2026, 1, 1), date(2026, 8, 31)
    loan_ids = {b.identity for b in history.batches if b.day <= end
                and any(m.account == ACCOUNT and m.change > 0 for m in b.movements)}
    support = build_support(source, bridge_receipts, loan_ids, end)
    result = attribute(history, receipts, support, start, end)
    paths = [*inputs, Path(__file__), Path('config/spark.yaml'),
             *[Path('scripts') / name for name in (
                 'audit_spark_usdc_origin_graph.py', 'audit_spark_cctp_draw_routes.py',
                 'audit_spark_usdc_draw_routes.py', 'audit_spark_ordered_draws.py',
                 'audit_scoped_borrowing_origins.py', 'audit_facility_funding.py',
                 'audit_spark_anchorage_draw_routes.py', 'audit_scoped_allocation_origin.py')]]
    result['source_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    with gzip.open(args.output, 'wt') as dst:
        json.dump(result, dst, indent=2)
    print(json.dumps(result['monthly'][-1], indent=2))


if __name__ == '__main__':
    main()
