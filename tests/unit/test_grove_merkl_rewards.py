import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.grove_merkl_rewards import CLAIMS, HOLDER, TOKENS, recognize_grove_merkl_rewards
from settle.extract import aave_reconstruct as aave
from settle.extract.hypersync import LogRow
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory
from settle.normalize.allocation_merkl import CLAIMED, wrapper_gift_transfers

ROWS = [LogRow(**r) for r in json.loads(
    (Path(__file__).parents[1] / 'fixtures/grove_merkl_wrapper_events.json').read_text())]
DISTRIBUTOR = '0x' + '3ef3d8ba38ebe18db133cec108f4d14ce00dd9ae'.rjust(64, '0')
HT = '0x' + HOLDER[2:].rjust(64, '0')


def test_canonical_wrapper_claims_authenticate_actual_receipts_with_alm_only_logs():
    # Real extraction selects ALM topics, not all logs of its transactions.
    rows = [r for r in ROWS if HT in (r.topic1, r.topic2, r.topic3)]
    gifts = wrapper_gift_transfers(rows, {DISTRIBUTOR})
    assert gifts == {(CLAIMS[0][0], 554), (CLAIMS[0][0], 564),
                     (CLAIMS[1][0], 409), (CLAIMS[1][0], 419)}
    assert gifts == wrapper_gift_transfers(ROWS, {DISTRIBUTOR})
    for tx, block, amounts in CLAIMS:
        for token, amount in zip(TOKENS, amounts, strict=True):
            receipt = next(r for r in rows if r.transaction_hash == tx
                           and r.address == token and (tx, r.log_index) in gifts)
            assert receipt.block_number == block
            assert abs(aave.ray_mul(*aave._words(receipt.data)) - amount) <= 1
            assert receipt.topic1 != DISTRIBUTOR  # Original sender rule missed these.


def test_unapproved_claim_or_missing_link_or_receipt_cannot_create_income():
    assert not wrapper_gift_transfers(ROWS, set())
    assert not wrapper_gift_transfers([r for r in ROWS if r.topic0 != aave.MINT_T0], {DISTRIBUTOR})
    assert not wrapper_gift_transfers([r for r in ROWS if r.topic0 != aave.BT_T0], {DISTRIBUTOR})
    bad_user = [replace(r, topic1='0x' + '1' * 64) if r.topic0 == CLAIMED else r for r in ROWS]
    assert not wrapper_gift_transfers(bad_user, {DISTRIBUTOR})


def test_direct_atoken_claim_does_not_enter_wrapper_branch():
    claim = next(r for r in ROWS if r.topic0 == CLAIMED)
    mint = next(r for r in ROWS if r.topic0 == aave.MINT_T0 and r.topic2 == HT)
    token = '0x' + mint.address[2:].rjust(64, '0')
    rows = [replace(claim, topic2=token), replace(mint, topic1=token)]
    assert not wrapper_gift_transfers(rows, {DISTRIBUTOR})


def test_unrelated_receipt_fails_closed_instead_of_becoming_reward():
    receipt = next(r for r in ROWS if r.topic0 == aave.BT_T0 and r.topic2 == HT)
    with pytest.raises(ValueError, match='do not reconcile'):
        wrapper_gift_transfers([*ROWS, replace(receipt, log_index=99999)], {DISTRIBUTOR})


def history():
    batches = []
    for tx, block, amounts in CLAIMS:
        r = next(r for r in ROWS if r.transaction_hash == tx)
        movements = tuple(AssetMovement(f'ethereum:{HOLDER}:{t}', D('10000000'), D(a)/10**18)
                          for t, a in zip(TOKENS, amounts, strict=True))
        batches.append(CapitalBatch('ethereum:' + tx,
            datetime.fromtimestamp(r.block_time, UTC).date(), r.block_time,
            'ethereum', block, movements))
    return CapitalHistory(tuple(batches), {'E3': f'ethereum:{HOLDER}:{TOKENS[0]}',
                                          'E1': f'ethereum:{HOLDER}:{TOKENS[1]}'}, {})


def test_saved_history_claims_are_income_without_debt_and_idempotent():
    h = history()
    fixed = recognize_grove_merkl_rewards(h)
    assert fixed == recognize_grove_merkl_rewards(fixed)
    for old, new in zip(h.batches, fixed.batches, strict=True):
        assert old.minted == new.minted == 0
        for a, b in zip(old.movements, new.movements, strict=True):
            assert a.change == b.change == b.external_income
            assert a.value_before == b.value_before


def test_saved_history_changed_amount_rejected():
    h = history()
    b = h.batches[0]
    h = replace(h, batches=(replace(b, movements=(replace(b.movements[0], change=D(1)),)),))
    with pytest.raises(ValueError, match='amount differs'):
        recognize_grove_merkl_rewards(h)
