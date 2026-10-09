"""Exact Grove issuer executions that bypass ordinary vault claim events.

These links relocate existing borrowed basis; they never seed it from NAV.
The evidence and pinned contract-state checks are in
docs/grove/initial-funding-investigation-2026-10-07.md.
"""
from dataclasses import replace
from decimal import Decimal

GROVE = '0x491edfb0b8b608044e227225c715981a30f3a44e'
JAAA = '0x5a0f93d040de44e78f251b03c43be9cf317dcf64'
VAULT = '0xe9d1f733f406d4bbbdfac6d4cfcd2e13a6ee1d01'
SUBSCRIPTION = 'ethereum:0x922d87f293b4bafb0edd5cc237d7df9aadffba3fe1e2e831e8601d1d724ae31a'
ISSUE = 'ethereum:0x535e1bb08f66d6e01662bcb0959cb46e9026cddb274d4097515b47d537e1c4e6'
CLEANUP = 'ethereum:0x71bd49f75f114b85a21e9245f13e0503c7c8f4a9b1fe45a564a7015a4b72bacc'
PENDING = f'async:ethereum:{VAULT}:{GROVE}:subscribe'
SHARES = f'ethereum:{GROVE}:{JAAA}'
AMOUNT = Decimal('50000000')
MARKER = ':issuer-delivery'


def link_grove_initial_jaaa(history):
    """July 24 delivery against the funded request; July 28 admin cleanup.

    The issuer used BalanceSheet.issue, not vault.deposit. The cleanup spell
    at 0x5a110bc4fdd01b193fdadddd38231dd098274a06 fulfilled the old request,
    transferred its escrow shares to itself and burned them. No second Grove
    receipt occurred. Its pending + claimable state is zero afterwards.
    https://etherscan.io/tx/0x71bd49f75f114b85a21e9245f13e0503c7c8f4a9b1fe45a564a7015a4b72bacc#eventlog
    https://etherscan.io/tx/0x535e1bb08f66d6e01662bcb0959cb46e9026cddb274d4097515b47d537e1c4e6#eventlog

    Wait for the confirming cleanup within the supplied history: never fetch
    a future transaction to explain an earlier pinned snapshot. This is an
    execution-specific exception, not a generic amount/date match for mints.
    """
    from ..normalize.allocation_capital import AssetMovement

    if SHARES not in history.venue_accounts.values():
        return history
    by_id = {b.identity: b for b in history.batches}
    if ISSUE + MARKER in by_id or ISSUE not in by_id or CLEANUP not in by_id:
        return history

    def require(ok, detail):
        if not ok:
            raise ValueError('Grove historical JAAA mismatch: ' + detail)

    require(SUBSCRIPTION in by_id, 'missing funded subscription')
    request, issue, cleanup = (by_id[k] for k in (SUBSCRIPTION, ISSUE, CLEANUP))
    require((request.block, issue.block, cleanup.block) == (22990215, 22991336, 23015872),
            'execution blocks')
    require(all(b.chain == 'ethereum' for b in (request, issue, cleanup)), 'chain')
    require(request.timestamp < issue.timestamp < cleanup.timestamp, 'chronology')
    require(request.minted == AMOUNT and issue.minted == cleanup.minted == 0, 'debt draws')
    requests = [m for m in request.movements if m.account == PENDING]
    require(len(requests) == 1 and requests[0].value_before == 0
            and requests[0].change == AMOUNT and requests[0].external_income == 0,
            'subscription shape')
    require(len(issue.movements) == 1 and issue.movements[0].account == SHARES
            and issue.movements[0].value_before == 0 and issue.movements[0].change == AMOUNT
            and issue.movements[0].external_income == 0, 'issuer delivery shape')
    require(not cleanup.movements, 'cleanup must not deliver a second holding')
    batches = []
    for b in history.batches:
        if b.identity == ISSUE:
            b = replace(b, identity=ISSUE + MARKER, movements=(*b.movements,
                        AssetMovement(PENDING, AMOUNT, -AMOUNT, preserve_basis=True)))
        elif b.timestamp > issue.timestamp:
            movements = []
            for m in b.movements:
                if m.account == PENDING:
                    require(m.value_before >= AMOUNT, 'pending mark already consumed delivery')
                    m = replace(m, value_before=m.value_before - AMOUNT)
                    require(m.value_before + m.change >= 0, 'later claim consumes linked subscription')
                movements.append(m)
            b = replace(b, movements=tuple(movements))
        batches.append(b)
    return replace(history, batches=tuple(batches))


# The August 21, 2025 Grove payload enables this precise JAAA corridor:
# https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20250821/GroveEthereum_20250821.sol
# InitiateTransferShares authenticates pool 281474976710663, share class
# 0x00010000000000070000000000000001, destination domain 5 and Grove's
# Avalanche ALM. The destination Safe uses BalanceSheet.issue (manual
# fulfillment), so there is no CCTP/message nonce to join. These are explicit
# historical links, not a generic amount/time heuristic for arbitrary mints.
# See the raw event proofs in tests/fixtures/grove_initial_custody_events.json.
AVALANCHE_SHARES = ('avalanche_c:0x7107dd8f56642327945294a18a4280c78e153644:'
                    '0x58f93d6b1ef2f44ec379cb975657c132cbed3b6b')
JAAA_TRANSFERS = (
    (23233417, '0xc49e1b375beae51df861da3f19da036223f0381f534909adffac3fe7a40f29f2',
     67841789, '0x04e17ed6696fcd9ff573c3e7b7946543bb02c3a676dd78fd4532a68a9b0c70a5'),
    (23276269, '0xb91127cd37947c01b60e8f0d8162d6ca56f11dda198f2d1a372dc84ad8f96390',
     68116627, '0x8eac7829c1699b587a10ac4c545e74782553d89e8ae1f5ebc9791a66151b75f0'),
    (23290184, '0xa4aece3ea9d5dd283d993c772f8a53c86c077a0a06d511c21eeb55b8b152da56',
     68180018, '0x590d7ed33663ac4afc77b6898dfb8e5a60c1031df9b6779d747d3ee5bda766bd'),
    (23318877, '0xdda1d9a966cc482b4fef9d5cdd1408c58fd636bac34d2219233b2823c6231cdd',
     68419703, '0x850ada023547d1579e72f31519caad111518ee35c9b7812edbd5d1e4ae4dd24b'),
    (23327014, '0xa09ec9751938f06a4be385fcebb211f72e2882bab5ae4a4b0a584d4ef1f70bb7',
     68487554, '0xa32798d5cf4305375fd5c2c52f071ff34779b1164b0e12182c6f1a9791babdb4'),
)


def link_grove_jaaa_avalanche(history):
    from ..normalize.allocation_capital import AssetMovement

    if SHARES not in history.venue_accounts.values():
        return history
    batches = {b.identity: b for b in history.batches}
    if len(batches) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    custody = {v: list(accounts) for v, accounts in history.custody_accounts.items()}
    source_venue = next(v for v, a in history.venue_accounts.items() if a == SHARES)
    suffix = ':jaaa-crosschain'
    for block_out, tx_out, block_in, tx_in in JAAA_TRANSFERS:
        source_id, target_id = 'ethereum:' + tx_out, 'avalanche_c:' + tx_in
        if source_id + suffix in batches:
            if target_id in batches:
                raise ValueError('Cannot append raw receipts to an already linked Grove history')
            continue
        if source_id not in batches:
            if target_id in batches:
                raise ValueError('Grove JAAA bridge receipt lacks its source execution')
            continue
        source = batches[source_id]
        sent = [m for m in source.movements if m.account == SHARES]
        if (source.chain != 'ethereum' or source.block != block_out or len(sent) != 1
                or sent[0].change >= 0 or sent[0].external_income != 0):
            raise ValueError('Grove JAAA bridge source mismatch')
        out = sent[0]
        amount = -out.change
        pending = 'jaaa-in-transit:' + tx_out
        custody.setdefault(source_venue, []).append(pending)
        batches[source_id + suffix] = replace(source, identity=source_id + suffix,
            movements=(*tuple(replace(m, preserve_basis=True) if m.account == SHARES else m
                              for m in source.movements), AssetMovement(pending, Decimal(0), amount)))
        del batches[source_id]
        if target_id not in batches:
            continue  # A pinned period may end while the shares are in transit.
        target = batches[target_id]
        received = [m for m in target.movements if m.account == AVALANCHE_SHARES]
        if (target.chain != 'avalanche_c' or target.block != block_in
                or target.timestamp <= source.timestamp or target.minted != 0
                or len(received) != 1 or len(target.movements) != 1
                or received[0].change <= 0 or received[0].external_income != 0):
            raise ValueError('Grove JAAA bridge destination mismatch')
        # Same shares, possibly a different NAV quotation at receipt. Mark the
        # in-flight holding to that quote and release all basis without treating
        # a price change as new capital or a realized redemption loss.
        value = received[0].change
        batches[target_id + suffix] = replace(target, identity=target_id + suffix,
            movements=(*target.movements, AssetMovement(pending, value, -value, preserve_basis=True)))
        del batches[target_id]
    return replace(history, batches=tuple(batches.values()), custody_accounts=custody)
