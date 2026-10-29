"""Verified cash-funded facilities and pending BUIDL claims, for tracing only.

No notional schedule seeds borrowed basis. Synthetic claims receive whatever
funding the actual ALM cash outflow carries, including unresolved provenance.
"""
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal as D

from ..domain.period import Period
from ..domain.primes import Chain
from ..extract.transfer_logs import TRANSFER_TOPIC0
from .allocation_principal_returns import principal_return_logs
from .redemption_settlements import match_redemptions, policy_for

USDC = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
# Address trails and verified cash funding: config/spark.yaml S23 and
# config/grove.yaml E42; docs/grove/spell-funding-map.md. This registry does
# not classify arbitrary payments to a facility's yield-paying address.
FACILITIES = {
    'spark': ('S23', '0x49506c3aa028693458d6ee816b2ec28522946872'),
    'grove': ('E42', '0x3e23311f9ff660e3c3d87e4b7c207b3c3d7e04f0'),
}
# December19's zero-net round trip cannot match a day-net exception. Exact
# receipt, verified against the equal December19 outgoing transfer:
# etherscan.io/tx/0x93979f7a1116b61d99e4076143bfb579ac7571e97d12b085a71e9914ced2a2ea
ANCHORAGE_RETURN = '0x84be9779551b28575c20ccfb447291f048aabf70d7638a2038c84f5941c9cbfe'
ANCHORAGE_CORRECTION = '0x1d3dd0adf2b6ab8c1bc89998bc4e370a4549382cf73a16c968c94f49445ad667'
# July 21 duplicates + exact round-trip correction: net deployment is 10m.
# Complete receipts: tests/fixtures/spark_anchorage_july_correction.json.gz.
ANCHORAGE_JULY_CORRECTION = '0x4ad39783662761407cc239326f07ebe8102485838934f03a94d1b59bec512c69'
KNOWN_RETURNS = {ANCHORAGE_RETURN: D('5000000'), ANCHORAGE_CORRECTION: D('5270830'),
                 ANCHORAGE_JULY_CORRECTION: D('10000017.287194')}
AMBIGUOUS_RETURNS = {
    '0xe74aeeb85c45d4ee617fb2c422f5ed36c4887202e2074194f0926af53cb1a1d6',
    '0x5b719b6e1f8d7ae0035edbe4d4832efa544a3d79474f86c2fbea5899fc4a67fe',
}


def _transfers(rows):
    unique = {}
    for r in rows:
        if r.topic0 != TRANSFER_TOPIC0 or len(r.data) != 66 or not r.transaction_hash:
            continue
        key = r.block_number, r.log_index
        if key in unique and unique[key] != r:
            raise ValueError('Conflicting custody transfer identity')
        unique[key] = r
    return sorted(unique.values(), key=lambda r: (r.block_number, r.log_index))


def link_facility(prime, chain, batches, rows, venue_accounts, unsupported, mapping):
    from .allocation_capital import AssetMovement

    if chain != Chain.ETHEREUM or prime.id not in FACILITIES:
        return batches
    vid, counterparty = FACILITIES[prime.id]
    if not any(v.id == vid for v in prime.venues):
        return batches
    holder = prime.alm[chain].hex
    account = f'facility:{chain.value}:{holder}:{counterparty}'
    cash = f'{chain.value}:{holder}:{USDC}'
    changes, returns = defaultdict(D), defaultdict(D)
    incomes = defaultdict(D)
    exceptions = principal_return_logs(prime, chain, mapping, rows)
    ambiguous = False
    for r in _transfers(rows):
        if r.address != USDC:
            continue
        sender, recipient = '0x' + r.topic1[-40:], '0x' + r.topic2[-40:]
        amount = D(int(r.data, 16)) / 10**6
        tx = f'{chain.value}:{r.transaction_hash}'
        if (sender, recipient) == (holder, counterparty):
            changes[tx] += amount
        elif (sender, recipient) == (counterparty, holder):
            if prime.id == 'spark' and r.transaction_hash in KNOWN_RETURNS:
                if amount != KNOWN_RETURNS[r.transaction_hash]:
                    raise ValueError('Verified Anchorage return amount changed')
                changes[tx] -= amount
                returns[tx] += amount
            elif prime.id == 'grove' or r.transaction_hash in AMBIGUOUS_RETURNS:
                ambiguous = True  # Unknown principal/interest split stays unresolved.
            elif (r.block_number, r.log_index) in exceptions:
                income = exceptions[(r.block_number, r.log_index)] / 10**6
                changes[tx] -= amount - income
                returns[tx] += amount - income
                incomes[tx] += income
    balance, out = D(0), []
    for b in sorted(batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index)):
        if b.identity in changes:
            change = changes[b.identity]
            if balance + change < 0:
                raise ValueError('Facility return exceeds verified funding')
            ms = list(b.movements)
            matches = [i for i, m in enumerate(ms) if m.account == cash]
            if len(matches) != 1:
                raise ValueError('Facility payment missing normalized ALM cash movement')
            i = matches[0]
            # The December receipt was previously labelled income. The May
            # receipt may already have been fixed by the day-net normalizer.
            if returns[b.identity]:
                if ms[i].external_income not in (incomes[b.identity], returns[b.identity] + incomes[b.identity]):
                    raise ValueError('Mixed facility principal and income needs an explicit split')
                ms[i] = replace(ms[i], external_income=incomes[b.identity])
            ms.append(AssetMovement(account, balance, change, preserve_basis=True))
            b = replace(b, movements=tuple(ms))
            balance += change
        out.append(b)
    if changes:
        venue_accounts[vid] = account
        unsupported.pop(vid, None)
        if ambiguous:
            unsupported[vid] = 'Facility return principal/interest split is unconfirmed'
    return out


def link_buidl_claims(prime, chain, pin, batches, rows, custody):
    """Reuse the reviewed redemption matcher over inception history.

    This does not activate retrospective revenue recognition: the ledger is
    consumed only as evidence linking funded shares to eventual cash.
    """
    from .allocation_capital import AssetMovement

    if prime.id != 'grove' or chain != Chain.ETHEREUM:
        return batches
    venue = next((v for v in prime.venues if v.id == 'E10'), None)
    if venue is None:
        return batches
    policy = policy_for(prime.id, venue.id)
    holder = prime.alm[chain].hex
    events = []
    for r in _transfers(rows):
        shape = (r.address, '0x' + r.topic1[-40:], '0x' + r.topic2[-40:])
        request = shape == (policy['share_token'], holder, policy['request_receiver'])
        cash = shape == (policy['cash_token'], policy['cash_payer'], holder)
        if request or cash:
            events.append(dict(kind='request' if request else 'cash',
                date=datetime.fromtimestamp(r.block_time, UTC).date().isoformat(),
                amount=str(D(int(r.data, 16)) / 10**6), tx=r.transaction_hash,
                block=r.block_number, log_index=r.log_index))
    if not events:
        return batches
    first = min(date.fromisoformat(e['date']) for e in events)
    last = max(date.fromisoformat(e['date']) for e in events)
    policy = {**policy, 'history_start': first, 'recognition_start': first}
    ledger = match_redemptions(venue, Period(first, last, {chain: pin}), policy, events)
    additions = defaultdict(list)
    share_account = f'{chain.value}:{holder}:{venue.token.address.hex}'
    index = {b.identity: b for b in batches}
    request_values = {}
    # Include outstanding claims: no lookahead to a later cash receipt.
    requests = [r for s in ledger.settlements for r in s['requests']] + list(ledger.outstanding)
    by_tx = defaultdict(list)
    for r in requests:
        by_tx[f'{chain.value}:{r["tx"]}'].append(r)
    for identity, group in by_tx.items():
        b = index[identity]
        source = next(m for m in b.movements if m.account == share_account)
        gross = sum(D(r['shares']) for r in group)
        if source.change >= 0 or source.external_income or abs(source.change) > gross + D('0.01'):
            raise ValueError('Mixed BUIDL request transaction needs explicit custody allocation')
        for r in group:
            value = -source.change * D(r['shares']) / gross
            account = f'redemption:{chain.value}:{r["event_id"]}'
            request_values[r['event_id']] = account, value
            additions[identity].append(AssetMovement(account, D(0), value, preserve_basis=True))
            custody.setdefault('E10', []).append(account)
    for s in ledger.settlements:
        total = sum(request_values[r['event_id']][1] for r in s['requests'])
        cash = D(s['cash_usd'])
        remaining = cash
        for i, r in enumerate(s['requests']):
            account, value = request_values[r['event_id']]
            proceeds = remaining if i == len(s['requests']) - 1 else cash * value / total
            remaining -= proceeds
            # Mark to actual proceeds before exit: the normal capital ledger
            # releases any lost borrowed basis to financing, not to new cash.
            additions[f'{chain.value}:{s["cash"]["tx"]}'].append(
                AssetMovement(account, proceeds, -proceeds))
    out = []
    for b in batches:
        if b.identity in additions:
            ms = tuple(replace(m, preserve_basis=True) if m.account == share_account else m
                       for m in b.movements)
            b = replace(b, movements=ms + tuple(additions[b.identity]))
        out.append(b)
    return out
