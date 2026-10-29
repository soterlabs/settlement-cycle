import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.grove_curve_swap_gains import GAINS, recognize_grove_curve_swap_gains
from settle.extract.hypersync import LogRow
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory
from settle.normalize.allocation_curve_swaps import EXCHANGE, HOLDER, POOL, curve_swap_income

ROWS = [LogRow(**r) for r in json.loads(
    (Path(__file__).parents[1] / 'fixtures/grove_curve_swap_gain_events.json').read_text())]


def test_ten_reviewed_gains_match_pool_events_and_actual_token_transfers():
    assert curve_swap_income(ROWS) == list(GAINS)
    assert len(GAINS) == 10
    assert sum(g[-1] for g in GAINS) == D('1123.768286697514264239')
    # ALM-topic selection retains every proof leg needed by fresh extraction.
    selected = [r for r in ROWS if any((x or '').endswith(HOLDER[2:])
                                      for x in (r.topic1, r.topic2, r.topic3))]
    assert curve_swap_income(selected) == list(GAINS)


def test_forged_pool_missing_transfer_and_wrong_buyer_cannot_create_gain():
    assert not curve_swap_income([replace(r, address='0x'+'99'*20) if r.address == POOL else r for r in ROWS])
    assert not curve_swap_income([replace(r, topic1='0x'+'99'*32) if r.topic0 == EXCHANGE else r for r in ROWS])
    with pytest.raises(ValueError, match='actual ALM token transfers'):
        curve_swap_income([r for r in ROWS if r.topic0 == EXCHANGE])
    assert curve_swap_income([*ROWS, *ROWS]) == list(GAINS)  # Overlapping log selections.


def test_saved_history_matches_only_exact_reviewed_cash_and_is_idempotent():
    batches = tuple(CapitalBatch('ethereum:'+tx, datetime.fromtimestamp(t, UTC).date(),
        t, 'ethereum', block, (AssetMovement(account, D(0), change),))
        for tx, block, t, account, change, gain in GAINS)
    h = CapitalHistory(batches, {}, {})
    fixed = recognize_grove_curve_swap_gains(h)
    assert fixed == recognize_grove_curve_swap_gains(fixed)
    assert sum(b.movements[0].external_income for b in fixed.batches) == sum(g[-1] for g in GAINS)
    b = batches[0]
    bad = replace(h, batches=(replace(b, movements=(replace(b.movements[0], change=D(1)),)),))
    with pytest.raises(ValueError, match='reviewed cash'):
        recognize_grove_curve_swap_gains(bad)
