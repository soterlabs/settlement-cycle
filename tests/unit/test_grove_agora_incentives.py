import json
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_agora_incentives import (
    ACCOUNT,
    RECEIPTS,
    recognize_grove_agora_incentives,
)
from settle.domain.config import load_prime_by_id
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROWS = json.loads((Path(__file__).parents[1] / 'fixtures/grove_agora_incentive_events.json').read_text())


def test_actual_agora_receipts_match_income_config_even_when_position_is_skipped():
    prime = load_prime_by_id('grove')
    venue = next(v for v in prime.venues if v.id == 'E38')
    assert venue.skip
    routes = {(s.token.hex, s.payer.hex) for s in venue.cash_distributions}
    assert len(RECEIPTS) == len(ROWS) == 8
    for tx, block, amount in RECEIPTS:
        r = next(r for r in ROWS if r['transaction_hash'] == tx)
        assert r['block_number'] == block
        assert (r['address'], '0x' + r['topic1'][-40:]) in routes
        assert r['topic2'].endswith('491edfb0b8b608044e227225c715981a30f3a44e')
        assert D(int(r['data'], 16)) / 10**6 == amount
    assert sum(r[2] for r in RECEIPTS) == D('2675160.44')


def test_incentive_replay_adds_earned_cash_and_no_borrowing():
    batches, balance = [], D(0)
    for tx, block, amount in RECEIPTS:
        r = next(r for r in ROWS if r['transaction_hash'] == tx)
        batches.append(CapitalBatch('ethereum:' + tx,
            datetime.fromtimestamp(r['block_time'], UTC).date(), r['block_time'], 'ethereum',
            block, (AssetMovement(ACCOUNT, balance, amount),)))
        balance += amount
    h = CapitalHistory(tuple(batches), {'E14': ACCOUNT}, {})
    linked = recognize_grove_agora_incentives(h)
    assert linked == recognize_grove_agora_incentives(linked)
    r = replay_history(h, batches[0].day, batches[-1].day)
    assert r.ledger.account(ACCOUNT).value == D('2675160.44')
    assert r.ledger.account(ACCOUNT).borrowed == 0 and r.ledger.drawn == 0
    assert not r.unmatched_receipts
