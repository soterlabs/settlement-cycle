"""Grove's authorized Paxos USDC bridge entrypoint as a financing boundary.

July 16 spell names the exact Paxos wallet and enables USDC transfers to it:
https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20260716/GroveEthereum_20260716.sol

Under the operator's EOA policy, funding is allocated when ALM cash reaches
this entrypoint. No custody-wallet interior or Robinhood receipt is inferred.
Same-wallet cash returns release principal up to outstanding deposits; excess
is earned. Different return addresses require evidence before attribution.
"""
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal as D

from ..extract.transfer_logs import TRANSFER_TOPIC0

HOLDER = '0x491edfb0b8b608044e227225c715981a30f3a44e'
WALLET = '0x8c0a9e5939b97979f85d9ada3d983c6e713cc2db'
TOKEN = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
CASH = f'ethereum:{HOLDER}:{TOKEN}'
VENUE = 'E_PAXOS_BRIDGE'
ACCOUNT = f'eoa-allocation:ethereum:{VENUE}'
MARKER = ':paxos-boundary'


def paxos_events(rows):
    events = []
    for r in rows:
        if r.address != TOKEN or r.topic0 != TRANSFER_TOPIC0:
            continue
        sender, recipient = '0x' + r.topic1[-40:], '0x' + r.topic2[-40:]
        if (sender, recipient) not in ((HOLDER, WALLET), (WALLET, HOLDER)):
            continue
        if len(r.data) != 66:
            raise ValueError('Invalid Paxos USDC transfer')
        events.append((r.transaction_hash, r.block_number, r.block_time, r.log_index,
                       D(int(r.data, 16)) / 10**6 * (1 if sender == HOLDER else -1)))
    return events


def link_paxos_boundary(history, events):
    from .allocation_capital import AssetMovement

    if CASH not in history.venue_accounts.values():
        return history
    events = list(events)
    if VENUE in history.venue_accounts:
        if history.venue_accounts[VENUE] != ACCOUNT:
            raise ValueError('Paxos boundary venue already mapped elsewhere')
        # Fresh extraction has already applied the complete observed stream.
        identities = {b.identity for b in history.batches}
        if any('ethereum:' + e[0] in identities for e in events):
            raise ValueError('Partially transformed Paxos history')
        return history
    indexed = {b.identity: b for b in history.batches}
    if len(indexed) != len(history.batches):
        raise ValueError('Duplicate Paxos capital transaction')
    grouped = defaultdict(list)
    for e in sorted(events, key=lambda e: (e[1], e[3])):
        grouped['ethereum:' + e[0]].append(e)
    balance = D(0)
    changed = False
    for identity, es in grouped.items():
        b = indexed.get(identity)
        if b is None:
            continue  # Do not read beyond a pinned history.
        if any(b.chain != 'ethereum' or b.block != e[1] or b.timestamp != e[2] for e in es):
            raise ValueError('Paxos transfer differs from normalized transaction')
        cash = [m for m in b.movements if m.account == CASH]
        if len(cash) != 1:
            raise ValueError('Paxos boundary needs its normalized ALM cash leg')
        paid = sum((max(e[4], D(0)) for e in es), D(0))
        received = sum((max(-e[4], D(0)) for e in es), D(0))
        principal = min(received, balance + paid)
        gain = received - principal
        ms = list(b.movements)
        if received:
            if cash[0].external_income not in (D(0), received):
                raise ValueError('Paxos return has mixed income attribution')
            ms = [replace(m, external_income=gain) if m.account == CASH else m for m in ms]
        change = paid - principal
        ms.append(AssetMovement(ACCOUNT, balance, change, preserve_basis=True))
        balance += change
        indexed[identity] = replace(b, identity=identity + MARKER, movements=tuple(ms))
        changed = True
    if not changed:
        return history
    return replace(history, batches=tuple(indexed.values()),
                   venue_accounts={**history.venue_accounts, VENUE: ACCOUNT},
                   analytics_only_venues=(*history.analytics_only_venues, VENUE))
