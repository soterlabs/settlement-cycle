"""Carry proven L2 funding into two authenticated July sUSDS returns.

The existing exact spell/message adapters authenticate custody links. This
additional diagnostic traces surviving borrowed basis through those links;
it never treats the bridged market value or its appreciation as a new draw.
"""
from dataclasses import replace
from decimal import Decimal as D

from audit_scoped_borrowing_origins import trace
from audit_spark_ordered_draws import SUSDS

RETURNS = {
    'ethereum:0xb2c9215da24ae539d7a3fe9b65b731d079f0aaf0229e51e885e35d319e05c9e8:spark-base-native':
        'native-bridge:spark:base:0xb2c9215da24ae539d7a3fe9b65b731d079f0aaf0229e51e885e35d319e05c9e8',
    'ethereum:0x23e6354a139fb301518266439c14a5f49c0a72dbc1b88ec3fc0b229a0ebedb74:spark-op-uni-withdrawal':
        'native-bridge:spark:optimism:0x23e6354a139fb301518266439c14a5f49c0a72dbc1b88ec3fc0b229a0ebedb74',
}
SUPPORT_VENUES = ('S36', 'S37', 'S38', 'S47', 'S48', 'S51', 'S52')


def support_history(history):
    accounts = {a for v, a in history.venue_accounts.items()
                if a.startswith('native-bridge:') or v in SUPPORT_VENUES}
    if SUSDS in accounts:
        raise ValueError('Native support must exclude the Ethereum target')
    aliases = {f'origin:{i}': a for i, a in enumerate(sorted(accounts))}
    # Retain every movement in each batch, so competing destinations still
    # constrain draw attribution. Unknown receipts never acquire a Sky basis.
    return replace(history, venue_accounts=aliases, custody_accounts={},
                   batches=tuple(b for b in history.batches
                                 if any(m.account in accounts for m in b.movements)))


def contributions(history, end):
    scoped = support_history(history)
    origin = trace(scoped, tuple(scoped.venue_accounts), end)
    index = {b.identity: b for b in history.batches}
    proofs = []
    for tx, claim in RETURNS.items():
        receipt = index.get(tx)
        if receipt is None or receipt.day > end:
            continue
        legs = [(b, m) for b in scoped.batches for m in b.movements
                if m.account == claim and m.change and b.day <= end]
        legs.sort(key=lambda pair: (pair[0].timestamp, pair[0].log_index))
        if len(legs) != 2:
            raise ValueError('Native return requires funded source and unique receipt')
        (source, sent), (arrival, released) = legs
        if (source.chain not in ('base', 'optimism') or arrival.identity != tx
                or source.timestamp >= receipt.timestamp or receipt.chain != 'ethereum'
                or sent.value_before or sent.change <= 0 or sent.external_income
                or released.change != -released.value_before or released.external_income
                or not released.preserve_basis or receipt.minted or receipt.external_funding
                or len(receipt.movements) != 2):
            raise ValueError('Invalid native return custody sequence')
        targets = [m for m in receipt.movements if m.account == SUSDS]
        if (len(targets) != 1 or targets[0].change != -released.change
                or targets[0].external_income or targets[0].preserve_basis):
            raise ValueError('Native return differs from sUSDS receipt')
        amount = D(origin['withdrawals'][tx][claim])
        if amount < 0 or amount > min(sent.change, targets[0].change):
            raise ValueError('Native borrowed basis exceeds original custody')
        proofs.append({'transaction': tx, 'source': source.identity, 'claim': claim,
                       'source_value': str(sent.change), 'received_value': str(targets[0].change),
                       'verified_borrowed_contribution': str(amount)})
    return proofs, origin
