import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_cash_distributions import (
    CASH,
    RECEIPTS,
    recognize_grove_cash_distributions,
)
from settle.domain.config import load_prime_by_id
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROWS = json.loads((Path(__file__).parents[1] / 'fixtures/grove_cash_distribution_events.json').read_text())


def history():
    batches = []
    value = D(0)
    for tx, block, amount in RECEIPTS:
        row = next(r for r in ROWS if r['transaction_hash'] == tx)
        batches.append(CapitalBatch('ethereum:' + tx,
            datetime.fromtimestamp(row['block_time'], UTC).date(), row['block_time'],
            'ethereum', block, (AssetMovement(CASH, value, amount),), log_index=row['log_index']))
        value += amount
    return CapitalHistory(tuple(batches), {'Cash': CASH}, {})


def test_receipts_match_existing_configured_yield_routes_not_principal_payer():
    prime = load_prime_by_id('grove')
    routes = {(str(s.chain or v.chain), s.token.hex, s.payer.hex, prime.alm[s.chain or v.chain].hex)
              for v in prime.venues for s in v.cash_distributions}
    assert len(RECEIPTS) == len(ROWS) == 13
    for tx, block, amount in RECEIPTS:
        r = next(r for r in ROWS if r['transaction_hash'] == tx)
        assert r['block_number'] == block
        assert ('ethereum', r['address'], '0x' + r['topic1'][-40:], '0x' + r['topic2'][-40:]) in routes
        assert not r['topic1'].endswith('9dd1929124a9ad8d1bc7f029eebbbfeb0d898318')
        assert D(int(r['data'], 16)) / 10**6 == amount
    assert sum(x[2] for x in RECEIPTS) == D('3104495.81')


def test_known_yield_is_never_borrowed_or_an_unmatched_receipt():
    h = history()
    linked = recognize_grove_cash_distributions(h)
    assert linked == recognize_grove_cash_distributions(linked)
    r = replay_history(h, h.batches[0].day, h.batches[-1].day)
    assert r.ledger.account(CASH).value == D('3104495.81')
    assert r.ledger.account(CASH).borrowed == 0 and r.ledger.drawn == 0
    assert not r.unmatched_receipts


def test_future_normalizer_already_classified_receipts_are_not_counted_twice():
    h = history()
    h = replace(h, batches=tuple(replace(b, movements=(
        replace(b.movements[0], external_income=b.movements[0].change),)) for b in h.batches))
    assert recognize_grove_cash_distributions(h) == h


def test_other_receipt_from_same_cash_account_stays_unresolved():
    h = history()
    b = replace(h.batches[0], identity='ethereum:unknown')
    h = replace(h, batches=(b,))
    assert recognize_grove_cash_distributions(h) == h
    r = replay_history(h, b.day, b.day)
    assert r.unmatched_receipts and r.ledger.account(CASH).borrowed == 0


@pytest.mark.parametrize('fault', ['mixed_income', 'wrong_amount', 'wrong_block', 'debt_draw'])
def test_changed_receipt_fails_instead_of_overwriting_another_classification(fault):
    h = history()
    b = h.batches[0]
    if fault == 'mixed_income':
        b = replace(b, movements=(replace(b.movements[0], external_income=D(1)),))
    elif fault == 'wrong_amount':
        b = replace(b, movements=(replace(b.movements[0], change=D(1)),))
    elif fault == 'wrong_block':
        b = replace(b, block=1)
    else:
        b = replace(b, minted=D(1))
    with pytest.raises(ValueError, match='cash distribution'):
        recognize_grove_cash_distributions(replace(h, batches=(b,)))
