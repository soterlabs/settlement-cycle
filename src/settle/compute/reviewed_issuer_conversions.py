"""Carry existing basis through explicitly reviewed issuer conversion groups.

Callers supply historical transaction identities backed by canonical logs.
This is not a runtime amount/date matcher and never creates a debt draw.
"""
from dataclasses import replace
from decimal import Decimal as D


def link_reviewed_issuer_conversions(history, *, source, cash, groups, route, label, custody_account=None):
    suffix = f':{route}-conversion'
    from ..normalize.allocation_capital import AssetMovement

    if source not in history.venue_accounts.values():
        return history
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    custody = {v: list(accounts) for v, accounts in history.custody_accounts.items()}
    venue = next(v for v, a in history.venue_accounts.items() if a == (custody_account or source))
    for payments, receipts, same_token in groups:
        identities = ['ethereum:' + tx for tx, _, _ in (*payments, *receipts)]
        if any(identity + suffix in index for identity in identities):
            if any(identity in index for identity in identities):
                raise ValueError(f'Cannot append raw events to linked {label} conversion')
            continue
        claim = f'conversion:ethereum:grove:{route}:' + payments[0][0]
        pending, last_order = D(0), (-1, -1, -1)
        for tx, block, amount in payments:
            identity = 'ethereum:' + tx
            b = index.get(identity)
            if b is None:
                continue
            source_movements = [m for m in b.movements if m.account == source]
            available = b.minted - sum((m.change - m.external_income for m in b.movements), D(0))
            if (b.chain != 'ethereum' or b.block != block or (b.timestamp, b.block, b.log_index) <= last_order
                    or len(source_movements) != 1 or source_movements[0].external_income or source_movements[0].change > 0
                    or available < amount - D('.01')):
                raise ValueError(f'{label} conversion lacks its normalized source funding')
            index[identity + suffix] = replace(b, identity=identity + suffix,
                movements=(*b.movements, AssetMovement(claim, pending, amount, preserve_basis=True)))
            del index[identity]
            pending += amount
            last_order = (b.timestamp, b.block, b.log_index)
            if claim not in custody.setdefault(venue, []):
                custody[venue].append(claim)
        remaining = sum(amount for _, _, amount in payments)
        for n, (tx, block, amount) in enumerate(receipts):
            identity = 'ethereum:' + tx
            b = index.get(identity)
            if b is None:
                # A later receipt in the input cannot silently skip a missing
                # earlier payout; remaining then differs from actual pending.
                remaining -= amount
                continue
            destination = source if same_token else cash
            if (b.chain != 'ethereum' or b.block != block or (b.timestamp, b.block, b.log_index) <= last_order
                    or b.minted or pending != remaining or pending < amount
                    or len(b.movements) != 1 or b.movements[0].account != destination
                    or b.movements[0].external_income or b.movements[0].change != amount):
                raise ValueError(f'{label} payout lacks its exact outstanding conversion')
            final = n == len(receipts) - 1
            # Keep the unreleased principal pending through partial payments.
            # Only the final observed settlement can realize the known fee.
            value = amount if final else pending
            index[identity + suffix] = replace(b, identity=identity + suffix,
                movements=(*b.movements, AssetMovement(claim, value, -amount,
                                                       preserve_basis=same_token)))
            del index[identity]
            pending = D(0) if final else pending - amount
            remaining -= amount
            last_order = (b.timestamp, b.block, b.log_index)
    return replace(history, batches=tuple(index.values()), custody_accounts=custody)
