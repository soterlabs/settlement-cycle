"""Allocation entry/exit boundaries for configured, commingled EOA venues.

Only ALM payments and configured returns are observed. Wallet balances and
transactions beyond either boundary are not evidence of the prime's assets.
"""
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal as D

from ..domain.pricing import PricingCategory
from .allocation_custody import _transfers
from .prices import is_par_stable


def eoa_boundaries(prime):
    result = []
    for v in prime.venues:
        if v.pricing_category != PricingCategory.EOA or v.skip:
            continue
        anchor = next((a for a in prime.venues if a.id == v.paired_with), None)
        if (not v.holder_override or not v.paired_source or anchor is None
                or anchor.chain != v.chain or not is_par_stable(v.token)
                or not is_par_stable(anchor.token)):
            raise ValueError(f'Invalid allocation EOA boundary: {v.id}')
        result.append((v, anchor))
    return result


def boundary_scope(prime):
    """Collapse configured boundary wallets; never infer a venue for an EOA."""
    boundaries = eoa_boundaries(prime)
    owners = {}
    for v, _ in boundaries:
        for addr in (v.holder_override, v.paired_source):
            if addr in owners and owners[addr] != v.id:
                raise ValueError('Overlapping allocation EOA boundaries')
            owners[addr] = v.id
    covered = {}
    for v in prime.venues:
        holder = v.holder_override or prime.alm.get(v.chain)
        if holder in owners and v.id != owners[holder]:
            covered[v.id] = owners[holder]
    scoped = replace(prime,
        venues=[v for v in prime.venues if v.id not in covered],
        alm={chain: holder for chain, holder in prime.alm.items() if holder not in owners})
    return scoped, covered


def link_eoa_boundaries(prime, chain, batches, rows, venue_accounts, unsupported):
    """Use the existing paired-principal-cap convention for realized returns.

    Return up to outstanding deposited value releases principal; excess is
    realized gain. Borrowed basis itself follows the ledger's weighted-average
    funding fraction, so reinvested gains never become borrowed capital.
    This convention does not infer unreported NAV, partial-return interest or
    terminal losses from a commingled wallet's balance.
    """
    from .allocation_capital import AssetMovement

    entries = [(v, a) for v, a in eoa_boundaries(prime) if v.chain == chain]
    for venue, anchor in entries:
        holder = prime.alm[chain].hex
        recipient = (anchor.holder_override or prime.alm[chain]).hex
        account = f'eoa-allocation:{chain.value}:{venue.id}'
        deposits, receipts = defaultdict(D), defaultdict(D)
        for r in _transfers(rows):
            sender, target = '0x' + r.topic1[-40:], '0x' + r.topic2[-40:]
            identity = f'{chain.value}:{r.transaction_hash}'
            if (r.address, sender, target) == (venue.token.address.hex, holder, venue.holder_override.hex):
                deposits[identity] += D(int(r.data, 16)) / 10**venue.token.decimals
            if (r.address, sender, target) == (anchor.token.address.hex, venue.paired_source.hex, recipient):
                receipts[identity] += D(int(r.data, 16)) / 10**anchor.token.decimals
        balance = D(0)
        out = []
        seen = set()
        for b in sorted(batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index)):
            paid, received = deposits[b.identity], receipts[b.identity]
            if not paid and not received:
                out.append(b)
                continue
            seen.add(b.identity)
            principal = min(received, balance + paid)
            gain = received - principal
            cash_account = f'{chain.value}:{recipient}:{anchor.token.address.hex}'
            deposit_cash = f'{chain.value}:{holder}:{venue.token.address.hex}'
            if paid and not any(m.account == deposit_cash for m in b.movements):
                raise ValueError('EOA deposit lacks normalized ALM cash')
            ms = list(b.movements)
            if received:
                indices = [i for i, m in enumerate(ms) if m.account == cash_account]
                if len(indices) != 1:
                    raise ValueError('EOA return lacks normalized anchor cash')
                i = indices[0]
                # A pre-existing sender-wide gift classification must not turn
                # the principal component into income. Mixed income needs an
                # explicit breakdown rather than an invented adjustment.
                if ms[i].external_income not in (D(0), received):
                    raise ValueError('Mixed EOA return income needs an explicit split')
                ms[i] = replace(ms[i], external_income=gain)
            ms.append(AssetMovement(account, balance, paid - principal, preserve_basis=True))
            balance += paid - principal
            out.append(replace(b, movements=tuple(ms)))
        expected = {k for k, v in deposits.items() if v} | {k for k, v in receipts.items() if v}
        if seen != expected:
            raise ValueError('EOA boundary transfer missing from normalized history')
        batches = out
        venue_accounts[venue.id] = account
        unsupported.pop(venue.id, None)
    return batches
