"""Expose observed Grove cash omitted from the revenue venue list in allocation-only financing diagnostics.

The July 2, 2026 spell deploys the additional compartment, without migrating
or replacing the legacy ALM:
https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20260702/GroveEthereum_20260702.sol

Capital extraction already tracks cash at this NFT holder. Without explicit
allocation ownership, its temporary AUSD/USDC holdings disappear from summed
venue costs even though their funding and eventual reinvestment are traced.
This adds ownership only: no synthetic flows, revenue, new loan or exemption.
"""
from dataclasses import replace

HOLDER = '0x0dcd9298e163dfd3c0b5b00f0d9093c36e40a153'
PRIMARY_CASH = 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
TOKENS = {
    'E14_PAU_CASH': '0x00000000efe302beaa2b3e6e1b18d08d69a9012a',
    'E15_PAU_CASH': '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
    'E16_PAU_CASH': '0x6b175474e89094c44da98b954eedeac495271d0f',
    'E17_PAU_CASH': '0xdc035d45d973e3ec169d2276ddab16f1e407384f',
}


def include_grove_secondary_cash(history):
    if PRIMARY_CASH not in history.venue_accounts.values():
        return history
    observed = {m.account for b in history.batches for m in b.movements if m.value_before or m.change}
    mapping = dict(history.venue_accounts)
    extra = set(history.analytics_only_venues)
    # E14 is display-only in config/grove.yaml: the AUSD routing balance
    # earns no standalone venue revenue. That does not exempt its borrowed
    # capital from financing. Its Agora pending-conversion custody is already
    # attached to E14 and must be included exactly once alongside the cash.
    # Keep the existing revenue report untouched; the extra row has no APY.
    primary_ausd = PRIMARY_CASH.rsplit(':', 1)[0] + ':' + TOKENS['E14_PAU_CASH']
    if mapping.get('E14') == primary_ausd and primary_ausd in observed:
        extra.add('E14')
    custody = {a for accounts in history.custody_accounts.values() for a in accounts}
    for venue, token in TOKENS.items():
        account = f'ethereum:{HOLDER}:{token}'
        if account not in observed:
            continue
        if venue in mapping and mapping[venue] != account:
            raise ValueError('Grove PAU cash venue ID has another account')
        if account in custody:
            continue
        owners = [v for v, a in mapping.items() if a == account]
        if owners and owners != [venue]:
            continue  # Already included under an explicit configured venue.
        mapping[venue] = account
        extra.add(venue)
    return replace(history, venue_accounts=mapping, analytics_only_venues=tuple(sorted(extra)))
