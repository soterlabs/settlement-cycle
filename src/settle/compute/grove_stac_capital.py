"""Link Grove's first STAC subscriptions to their delayed share issuances.

The December 11, 2025 spell identifies the STAC USDC deposit wallet and token:
https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20251211/GroveEthereum_20251211.sol

December 16's $1,000 test + $49,999,000 subscription funds the first 50,000
STAC shares; December 18's $50m funds the next 50,000. At the observed $1,000
issue price these are $50m each. No exploration of the issuer's commingled
wallet is needed. Exact transfer/mint evidence is pinned in
tests/fixtures/grove_stac_subscription_events.json.

Only existing cash/draw basis funds a pending claim. Issuance changes custody,
not debt or revenue. These explicit historical links do not classify arbitrary
issuer mints, distributions or later subscriptions by amount alone.
"""
from dataclasses import replace
from decimal import Decimal as D

HOLDER = '0x491edfb0b8b608044e227225c715981a30f3a44e'
USDC = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
TOKEN = '0x51c2d74017390cbbd30550179a16a1c28f7210fc'
DEPOSIT_WALLET = '0x51e4c4a356784d0b3b698bfb277c626b2b9fe178'
CASH = f'ethereum:{HOLDER}:{USDC}'
SHARES = f'ethereum:{HOLDER}:{TOKEN}'
PENDING = 'subscription:ethereum:grove:stac:2025-12'
SUFFIX = ':stac-subscription'
# tx, block, USDC paid to the governance-configured entrypoint.
PAYMENTS = (
    ('0xc3aa67c5974286c05ba8fb0013f456897efc5036056d6afb3f8581d8fda81c2b', 24026189, D('1000')),
    ('0x45f8a63c48adb2bcbc867130753f801bc19c63b033a9a6d75e33cbcdf2a311fa', 24028217, D('49999000')),
    ('0x2559a0b92c5897aa8a7f18298999e1d7ab0ae8e736e0be4106ad64435965a82b', 24040327, D('50000000')),
)
ISSUES = (
    ('0x2f544c36733dfc9c71c76a371e21a3c5d04b3af475635a85d7000996c74c0ae4', 24028289),
    ('0x8f8e781c2fbfdefdee08a225e702b319e9fabd65c3c0a1167df5e13f0303dc8e', 24041058),
)


def link_grove_stac_subscriptions(history):
    from ..normalize.allocation_capital import AssetMovement

    if SHARES not in history.venue_accounts.values():
        return history
    payments = {'ethereum:' + tx: (block, amount) for tx, block, amount in PAYMENTS}
    issues = {'ethereum:' + tx: block for tx, block in ISSUES}
    ids = {b.identity for b in history.batches}
    if len(ids) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    if any(b.identity.endswith(SUFFIX) for b in history.batches):
        if ids & (payments.keys() | issues.keys()):
            raise ValueError('Cannot append raw events to linked Grove STAC history')
        return history
    pending, issued = D(0), D(0)
    out = []
    custody = {v: list(accounts) for v, accounts in history.custody_accounts.items()}
    venue = next(v for v, a in history.venue_accounts.items() if a == SHARES)
    for b in sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index)):
        if b.identity in payments:
            block, amount = payments[b.identity]
            cash = [m for m in b.movements if m.account == CASH]
            if (b.chain != 'ethereum' or b.block != block or len(cash) != 1
                    or cash[0].external_income or cash[0].change > 0
                    or b.minted - sum((m.change - m.external_income for m in b.movements), D(0))
                    < amount - D('.01')):
                raise ValueError('Grove STAC payment lacks its normalized cash funding')
            b = replace(b, identity=b.identity + SUFFIX, movements=(*b.movements,
                        AssetMovement(PENDING, pending, amount, preserve_basis=True)))
            pending += amount
            if PENDING not in custody.setdefault(venue, []):
                custody[venue].append(PENDING)
        elif b.identity in issues:
            amount = D('50000000')
            if (b.chain != 'ethereum' or b.block != issues[b.identity] or b.minted
                    or len(b.movements) != 1 or b.movements[0].account != SHARES
                    or b.movements[0].change != amount or b.movements[0].external_income
                    or b.movements[0].value_before != issued or pending != amount):
                raise ValueError('Grove STAC issuance lacks its exact funded subscription')
            b = replace(b, identity=b.identity + SUFFIX, movements=(*b.movements,
                        AssetMovement(PENDING, pending, -amount, preserve_basis=True)))
            pending -= amount
            issued += amount
        out.append(b)
    return replace(history, batches=tuple(out), custody_accounts=custody)
