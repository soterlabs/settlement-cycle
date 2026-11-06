"""Separate registered same-venue custody legs before diagnostic origin pooling.

Ownership comes from normalized request/claim links, not a guessed counterparty
or date/amount match. All original movements and net debt remain intact.
"""
from dataclasses import replace
from decimal import Decimal as D


def separate(history, venues):
    owners = {}
    for v in venues:
        for a in (history.venue_accounts[v], *history.custody_accounts.get(v, [])):
            if a in owners:
                raise ValueError('Overlapping custody ownership')
            owners[a] = v
    output, proofs = [], []
    for b in history.batches:
        if ':origin-custody:' in b.identity:
            output.append(b)
            continue
        selected = []
        for v in venues:
            ms = [m for m in b.movements if owners.get(m.account) == v and m.change]
            if len(ms) != 2 or any(m.external_income for m in ms):
                continue
            holding = history.venue_accounts[v]
            if sum(m.account == holding for m in ms) != 1:
                continue
            if sum((m.change for m in ms), D(0)) != 0 or not any(m.change < 0 for m in ms):
                continue
            selected.append((v, ms))
        if not selected:
            output.append(b)
            continue
        isolated = {m.account for _, ms in selected for m in ms}
        # A zero-net custody transfer cannot consume a new draw. Keep debt and
        # external funding in the remainder, separate from disjoint routes.
        output.append(replace(b, movements=tuple(m for m in b.movements if m.account not in isolated)))
        for n, (v, ms) in enumerate(selected, 1):
            identity = b.identity + ':origin-custody:' + v
            output.append(replace(b, identity=identity, movements=tuple(ms), minted=D(0),
                                  minted_by_ilk={}, external_funding=(), funding_assumption=None,
                                  log_index=b.log_index + n))
            proofs.append({'transaction': b.identity, 'route': identity, 'venue': v,
                           'accounts': [m.account for m in ms],
                           'amount': str(max(m.change for m in ms))})
    return replace(history, batches=tuple(output)), proofs
