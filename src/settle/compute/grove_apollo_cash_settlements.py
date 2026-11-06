"""Reviewed Apollo cash paid before corresponding Plume share cancellations.

The October 2, 2025 Plume spell onboards the Apollo allocation:
https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20251002/GrovePlume_20251002.sol

Cash was returned on Ethereum before shares were cancelled on Plume. For
these two reviewed groups, release basis pro rata at each actual cash receipt;
later cancellation only updates token bookkeeping. The actual cash price and
verified share quantities determine the ratio; no future oracle price is used
at the receipt. Full canonical boundary logs are retained with the tests.
Incomplete groups remain unresolved rather than reading beyond pinned inputs.
"""
from dataclasses import replace
from decimal import Decimal as D

SOURCE = 'plume:0x1db91ad50446a671e2231f77e00948e68876f812:0x9477724bb54ad5417de8baff29e59df3fb4da74f'
CASH = 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
SUFFIX = ':apollo-cash-settlement'
# Cash tx/block/USD; cancellation tx/block/raw shares; raw shares held before.
GROUPS = (
    ((
        ('0x6eeed62f2c984e34c910d1451cbfc3058e3d50c83870f22e03f042a3ce471830', 25073659, D('1')),
        ('0xfdf91604ea1ac277c428044eeb53ebb44e554122da1ae5a65a07402bb54a4b49', 25073918, D('18077674.30')),
    ), (
        ('0xe1756e363f76028c3cd44c251a069678ca794b864894a300257713d968701abd', 67732141, 1000000000000000000),
        ('0xc3348f1272ca85d10128b04066a13b049579a2708a604d780a94628e96f87078', 67736681, 17791982592416089944130531),
    ), 50014229924451562601087412),
    ((
        ('0x8cd2c1cef6561be8bd519a773b896598e3b571270767825a4743cd441d7b0a4f', 25724598, D('1')),
        ('0x66cb3d9d68010cfeb6e1949215dbea5cc18a21c0e3e6be94321272230c70f899', 25724709, D('12263706.47')),
    ), (
        ('0x091e754c0062656749b5eb14a78122e37b62f42027d2a11c2f6ca1ef58dd4fdb', 86433909, 1000000000000000000),
        ('0x9436d124e15bea9def739ab01daf192f8c70b87d16787bbdddba2868ced5008e', 86442086, 12020502039538100000000000),
    ), 32222246332035472656956881),
)


def link_grove_apollo_cash_settlements(history):
    from ..normalize.allocation_capital import AssetMovement

    if SOURCE not in history.venue_accounts.values():
        return history
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    for receipts, burns, held in GROUPS:
        receipt_ids = ['ethereum:' + tx for tx, _, _ in receipts]
        burn_ids = ['plume:' + tx for tx, _, _ in burns]
        ids = receipt_ids + burn_ids
        if any(identity + SUFFIX in index for identity in ids):
            if any(identity in index for identity in ids):
                raise ValueError('Cannot append raw events to linked Apollo cash settlement')
            continue
        if not all(identity in index for identity in ids):
            continue  # A partial pin cannot use future cancellations as evidence.
        cash_batches = [index[i] for i in receipt_ids]
        burn_batches = [index[i] for i in burn_ids]
        stamps = [b.timestamp for b in (*cash_batches, *burn_batches)]
        if stamps != sorted(stamps) or len(set(stamps)) != len(stamps):
            raise ValueError('Apollo cash settlement event order changed')
        # The share ratio is valid only if no other ownership change intervenes.
        for b in history.batches:
            if b.identity not in ids and stamps[0] <= b.timestamp <= stamps[-1]:
                if any(m.account == SOURCE and m.change for m in b.movements):
                    raise ValueError('Apollo settlement overlaps another share movement')
        redeemed = sum(shares for _, _, shares in burns)
        remaining = held - redeemed
        if remaining < 0:
            raise ValueError('Apollo settlement exceeds owned shares')
        total_cash = sum(amount for _, _, amount in receipts)
        # Scale held shares at this settlement's actual cash price. This gives
        # exactly the owned-share ratio, independent of a later NAV observation.
        value = total_cash * D(held) / D(redeemed)
        for b, (_, block, amount) in zip(cash_batches, receipts, strict=True):
            if (b.chain != 'ethereum' or b.block != block or b.minted or len(b.movements) != 1
                    or b.movements[0].account != CASH or b.movements[0].change != amount
                    or b.movements[0].external_income):
                raise ValueError('Apollo settlement cash receipt mismatch')
            index[b.identity + SUFFIX] = replace(b, identity=b.identity + SUFFIX,
                movements=(*b.movements, AssetMovement(SOURCE, value, -amount)))
            del index[b.identity]
            value -= amount
        for b, (_, block, shares) in zip(burn_batches, burns, strict=True):
            if b.chain != 'plume' or b.block != block or b.minted or len(b.movements) != 1:
                raise ValueError('Apollo settlement cancellation mismatch')
            m = b.movements[0]
            if m.account != SOURCE or m.change >= 0 or m.external_income:
                raise ValueError('Apollo settlement cancellation asset mismatch')
            # Both normalized values use the same NAV, so compare their ratio
            # with exact raw share quantities. Allow Decimal context noise only.
            left, right = m.value_before * D(shares), -m.change * D(held)
            if abs(left - right) > max(abs(left), abs(right)) * D('1e-24'):
                raise ValueError('Apollo normalized holdings differ from canonical shares')
            # The already-paid shares are economically gone. Actual cancellation
            # now marks only the remaining shares; it must not release basis twice.
            remaining_value = (-m.change / D(shares)) * D(remaining)
            index[b.identity + SUFFIX] = replace(b, identity=b.identity + SUFFIX,
                movements=(replace(m, value_before=remaining_value, change=D(0)),))
            del index[b.identity]
            held -= shares
    return replace(history, batches=tuple(index.values()))
