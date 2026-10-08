"""Grove Galaxy ARCH funding at the issuer's deposit and return boundaries.

The December 11, 2025 spell authorizes the deposit wallet, explicitly named
GALAXY_ARCH_CLOS_USDC_DEPOSIT_WALLET:
https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20251211/GroveEthereum_20251211.sol

Actual payments total $49.9m, not the $50m notional schedule. Four principal
returns exactly match GACLO balance reductions, but cash arrives 1-9 days
before the Avalanche bookkeeping. Observe capital at the cash boundary; do
not trace commingled funds in the deposit wallet or wait for later token moves.
These reviewed identities are backed by grove_galaxy_arch_events.json.
Yield from the separate configured cash-distribution payer is not principal.
"""
from dataclasses import replace
from decimal import Decimal as D

HOLDER = '0x491edfb0b8b608044e227225c715981a30f3a44e'
USDC = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
DEPOSIT_WALLET = '0x2e3a11807b94e689387f60cd4bf52a56857f2edc'
RETURN_WALLET = '0x9dd1929124a9ad8d1bc7f029eebbbfeb0d898318'
ACCOUNT = 'eoa-allocation:ethereum:E21'
CASH = f'ethereum:{HOLDER}:{USDC}'
# Positive is paid into the allocation; negative is principal returned to ALM.
FLOWS = (
    ('0xc3aa67c5974286c05ba8fb0013f456897efc5036056d6afb3f8581d8fda81c2b', 24026189, D('1000')),
    ('0x823b37638a0aef78a6cd9bdca6d091c06f78464e44e11dc49028be936828f868', 24028185, D('49899000')),
    ('0x10b2759ce45c535e5f374b178613c5693fa5abdcdc30d2af13018ebc69bb7e1a', 25000577, D('-3591655.48')),
    ('0x8d0b6c1e199b8f1504b69b9aad5ba3b78eccf3699e0d176d97e44ddbeb0590fa', 25302856, D('-18721388.15')),
    ('0xf0d63ee761c6a0f86dd1fd1c96aeb708fb208a568ad3389c146f79f6f36a2a77', 25725632, D('-4729780.41')),
    ('0xd7a04ea7e6aef2519bb177da44444482a58bcb9d15c932100b5cdfb64581adeb', 25754295, D('-4949341.17')),
)


def link_grove_galaxy_arch(history):
    from ..normalize.allocation_capital import AssetMovement
    from .grove_stac_capital import SUFFIX as STAC_SUFFIX

    if ('E21' not in history.unsupported and 'E21' not in history.venue_accounts
            or CASH not in history.venue_accounts.values()):
        return history
    lookup = {}
    for tx, block, amount in FLOWS:
        lookup['ethereum:' + tx] = (block, amount)
        # The $1k test transaction also funds STAC. Its adapter runs first;
        # consume only ARCH's remaining $1k, without erasing the STAC link.
        if amount == D(1000):
            lookup['ethereum:' + tx + STAC_SUFFIX] = (block, amount)
    ids = [b.identity for b in history.batches]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate capital transaction')
    if not set(ids) & lookup.keys():
        return history
    if history.venue_accounts.get('E21', ACCOUNT) != ACCOUNT:
        raise ValueError('Galaxy ARCH already has a different capital account')
    balance = D(0)
    out = []
    seen_transactions = set()
    for b in sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index)):
        flow = lookup.get(b.identity)
        if flow is None:
            out.append(b)
            continue
        tx = b.identity.split(':')[1]
        if tx in seen_transactions:
            raise ValueError('Galaxy ARCH cash transfer consumed twice')
        seen_transactions.add(tx)
        block, amount = flow
        if b.chain != 'ethereum' or b.block != block or balance + amount < 0:
            raise ValueError('Galaxy ARCH funding history mismatch')
        existing = [m for m in b.movements if m.account == ACCOUNT]
        wanted = AssetMovement(ACCOUNT, balance, amount, preserve_basis=True)
        cash = [m for m in b.movements if m.account == CASH]
        if len(cash) != 1 or cash[0].external_income:
            raise ValueError('Galaxy ARCH cash boundary mismatch')
        if amount < 0 and (b.minted or cash[0].change != -amount):
            raise ValueError('Galaxy ARCH principal return mismatch')
        if existing:
            if existing != [wanted]:
                raise ValueError('Galaxy ARCH linked movement mismatch')
        else:
            available = b.minted - sum((m.change - m.external_income for m in b.movements), D(0))
            if amount > 0 and (cash[0].change > 0 or available < amount - D('.01')):
                raise ValueError('Galaxy ARCH deposit lacks actual funding')
            b = replace(b, movements=(*b.movements, wanted))
        balance += amount
        out.append(b)
    unsupported = dict(history.unsupported)
    unsupported.pop('E21', None)
    return replace(history, batches=tuple(out), unsupported=unsupported,
        venue_accounts={**history.venue_accounts, 'E21': ACCOUNT})
