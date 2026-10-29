import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_galaxy_arch_capital import (
    ACCOUNT,
    CASH,
    DEPOSIT_WALLET,
    FLOWS,
    HOLDER,
    RETURN_WALLET,
    USDC,
    link_grove_galaxy_arch,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

RAW = json.loads((Path(__file__).parents[1] / 'fixtures/grove_galaxy_arch_events.json').read_text())


def history():
    rows = {r['transaction_hash']: r for r in RAW['ethereum']}
    first = rows[FLOWS[0][0]]
    day = datetime.fromtimestamp(first['block_time'], UTC).date()
    batches = [CapitalBatch('funding', day, first['block_time'] - 1, 'ethereum',
        first['block_number'] - 1, (AssetMovement(CASH, D(0), D('49900000')),), D('49900000'))]
    balance = D('49900000')
    for tx, block, amount in FLOWS:
        r = rows[tx]
        batches.append(CapitalBatch('ethereum:' + tx,
            datetime.fromtimestamp(r['block_time'], UTC).date(), r['block_time'],
            'ethereum', block, (AssetMovement(CASH, balance, -amount),), log_index=r['log_index']))
        balance -= amount
    return CapitalHistory(tuple(batches), {'Cash': CASH}, {'E21': 'Requires off-chain principal payment matching'})


def test_actual_boundary_flows_match_onchain_clo_issuance_and_principal_reductions():
    transfers = [r for r in RAW['avalanche_c'] if r['topic0'].startswith('0xddf252ad')]
    issue = transfers[0]
    assert D(int(issue['data'], 16)) / 10**6 == D('49900000')
    assert issue['topic2'][-40:] == '7107dd8f56642327945294a18a4280c78e153644'
    assert sum(amount for _, _, amount in FLOWS if amount > 0) == D('49900000')
    assert sum(-amount for _, _, amount in FLOWS if amount < 0) == D('31992165.21')
    returns = {D(int(r['data'], 16)) / 10**6: r for r in transfers[1:]}
    for tx, block, amount in FLOWS:
        r = next(r for r in RAW['ethereum'] if r['transaction_hash'] == tx)
        assert r['block_number'] == block and r['address'] == USDC
        assert D(int(r['data'], 16)) / 10**6 == abs(amount)
        assert r['topic1'][-40:] == (HOLDER if amount > 0 else RETURN_WALLET)[2:]
        assert r['topic2'][-40:] == (DEPOSIT_WALLET if amount > 0 else HOLDER)[2:]
        if amount < 0:
            token = returns[-amount]
            assert token['topic1'][-40:] == '7107dd8f56642327945294a18a4280c78e153644'
            assert token['topic2'][-40:] == '058557179be19269202595e66644f7986500ae3b'
            assert r['block_time'] < token['block_time']  # Cash precedes token bookkeeping.
            assert token['block_time'] - r['block_time'] < 10 * 86400


def test_principal_follows_cash_boundary_and_not_later_token_updates():
    h = history()
    linked = link_grove_galaxy_arch(h)
    assert linked == link_grove_galaxy_arch(linked)
    assert linked.venue_accounts['E21'] == ACCOUNT and 'E21' not in linked.unsupported
    r = replay_history(h, h.batches[0].day, h.batches[-1].day)
    assert r.ledger.account(ACCOUNT).borrowed == D('17907834.79')
    assert r.ledger.account(CASH).borrowed == D('31992165.21')
    assert r.ledger.drawn == D('49900000')  # The notional $50m is never used.
    assert not r.unmatched_receipts and not r.unmatched_outflows
    # Both August cash payments precede August 19's token reductions.
    assert h.batches[-1].day.isoformat() == '2026-08-14'
    assert r.daily[h.batches[-1].day][ACCOUNT] == D('17907834.79')


def test_earned_part_of_initial_payment_remains_unborrowed():
    h = history()
    first = h.batches[0]
    h = replace(h, batches=(replace(first, minted=D('39920000'), movements=(
        replace(first.movements[0], external_income=D('9980000')),)), *h.batches[1:]))
    r = replay_history(h, h.batches[0].day, h.batches[-1].day)
    assert r.ledger.account(ACCOUNT).borrowed == D('17907834.79') * D('.8')
    assert r.ledger.account(CASH).borrowed == D('31992165.21') * D('.8')


def test_partial_pinned_history_retains_outstanding_capital():
    h = history()
    h = replace(h, batches=h.batches[:4])  # First principal return only.
    r = replay_history(h, h.batches[0].day, h.batches[-1].day)
    assert r.ledger.account(ACCOUNT).borrowed == D('46308344.52')


@pytest.mark.parametrize('fault', ['missing_funding', 'wrong_amount', 'duplicate', 'different_account'])
def test_unsupported_or_changed_boundary_cannot_be_silently_matched(fault):
    h = history()
    if fault == 'missing_funding':
        h = replace(h, batches=h.batches[3:])
    elif fault == 'wrong_amount':
        last = h.batches[-1]
        h = replace(h, batches=(*h.batches[:-1], replace(last,
            movements=(replace(last.movements[0], change=D(1)),))))
    elif fault == 'duplicate':
        h = replace(h, batches=(*h.batches, h.batches[-1]))
    else:
        h = replace(h, venue_accounts={**h.venue_accounts, 'E21': 'other'})
    with pytest.raises(ValueError, match=r'Galaxy ARCH|Duplicate'):
        link_grove_galaxy_arch(h)
