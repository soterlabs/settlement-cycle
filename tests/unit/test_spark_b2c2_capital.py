import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_b2c2_capital import (
    ACCOUNT,
    ENTRY,
    EVENTS,
    HOLDER,
    VENUE,
    link_spark_b2c2_boundary,
)
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROWS = json.loads((Path(__file__).parents[1] / 'fixtures/spark_b2c2_boundary_events.json').read_text())


def test_complete_boundary_matches_actual_same_wallet_cash_transfers():
    observed = []
    for r in ROWS:
        if r['topic0'] != TRANSFER_TOPIC0:
            continue
        if (r['topic1'][-40:], r['topic2'][-40:]) not in ((HOLDER[2:], ENTRY[2:]), (ENTRY[2:], HOLDER[2:])):
            continue
        assert r['address'] in ('0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', '0xdac17f958d2ee523a2206206994597c13d831ec7')
        amount = D(int(r['data'], 16)) / 10**6 * (1 if r['topic1'].endswith(HOLDER[2:]) else -1)
        observed.append((r['transaction_hash'], r['block_number'], r['block_time'], r['address'], amount))
    assert tuple(observed) == EVENTS
    assert sum(max(e[4], D(0)) for e in EVENTS) == D('2100105')
    assert sum(max(-e[4], D(0)) for e in EVENTS) == D('2098654')


def history():
    balances, batches = {}, []
    for tx, block, stamp, token, paid in EVENTS:
        cash = f'ethereum:{HOLDER}:{token}'
        before = balances.get(cash, D(0))
        change = max(-paid, D(0))
        balances[cash] = before + change
        batches.append(CapitalBatch('ethereum:' + tx, datetime.fromtimestamp(stamp, UTC).date(),
            stamp, 'ethereum', block, (AssetMovement(cash, before, change),), max(paid, D(0)),
            minted_by_ilk={'SPARK': paid} if paid > 0 else {}))
    return CapitalHistory(tuple(batches), {}, {})


def test_returns_move_principal_across_stablecoins_without_forcing_fee_writeoff():
    h = history()
    linked = link_spark_b2c2_boundary(h)
    assert linked == link_spark_b2c2_boundary(linked)
    assert linked.venue_accounts[VENUE] == ACCOUNT and VENUE in linked.analytics_only_venues
    day = h.batches[-1].day
    r = replay_history(h, day, day, quantify_uncertainty=True)
    assert r.ledger.drawn == D('2100105')
    assert r.ledger.account(ACCOUNT).value == r.ledger.account(ACCOUNT).borrowed == D('1451')
    assert r.ledger.realised_principal_loss == 0
    assert not r.unmatched_receipts and not r.unmatched_outflows
    assert sum(a.borrowed for a in r.ledger.accounts.values()) == r.ledger.drawn
    assert ACCOUNT not in linked.idle_accounts


def test_own_cash_and_cutoff_preserve_their_observed_funding():
    h = history()
    first = h.batches[0]
    cash = first.movements[0].account
    income = replace(first, identity='earned-funding', timestamp=first.timestamp - 1,
        minted=D(0), minted_by_ilk={}, movements=(AssetMovement(cash, D(0), D(100), external_income=D(100)),))
    paid = replace(first, minted=D(0), minted_by_ilk={}, movements=(AssetMovement(cash, D(100), D(-100)),))
    r = replay_history(replace(h, batches=(income, paid)), paid.day, paid.day)
    assert r.ledger.account(ACCOUNT).value == D(100)
    assert r.ledger.account(ACCOUNT).borrowed == 0
    assert not r.unmatched_outflows


@pytest.mark.parametrize('fault', ['no_payment', 'no_funding', 'wrong_return', 'wrong_stamp', 'append'])
def test_missing_or_conflicting_evidence_cannot_create_funding(fault):
    h = history()
    batches = list(h.batches)
    if fault == 'no_payment':
        batches.pop(0)
    elif fault == 'no_funding':
        batches[0] = replace(batches[0], minted=D(0), minted_by_ilk={})
    elif fault == 'wrong_return':
        batches[1] = replace(batches[1], movements=(replace(batches[1].movements[0], change=D(11)),))
    elif fault == 'wrong_stamp':
        batches[0] = replace(batches[0], timestamp=1)
    else:
        batches = [*link_spark_b2c2_boundary(h).batches, batches[1]]
    with pytest.raises(ValueError, match='Spark B2C2'):
        link_spark_b2c2_boundary(replace(h, batches=tuple(batches)))
