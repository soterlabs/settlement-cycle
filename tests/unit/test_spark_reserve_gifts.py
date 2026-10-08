import gzip
import json
from collections import defaultdict
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_reserve_gifts import GIFTS, HOLDER, recognize_spark_reserve_gifts
from settle.extract._keccak import keccak256
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

FIXTURE = json.loads(gzip.decompress((Path(__file__).parents[1] / 'fixtures/spark_reserve_gifts.json.gz').read_bytes()))


def history():
    batches = []
    for row in FIXTURE['normalized_batches']:
        r = dict(row)
        r['day'] = date.fromisoformat(r['day'])
        r['minted'] = D(r['minted'])
        r['minted_by_ilk'] = {k: D(v) for k, v in r['minted_by_ilk'].items()}
        r['movements'] = tuple(AssetMovement(m['account'], D(m['value_before']), D(m['change']),
            D(m['external_income']), m['preserve_basis']) for m in r['movements'])
        batches.append(CapitalBatch(**r))
    return CapitalHistory(tuple(batches), {}, {})


def test_every_gift_is_an_actual_treasury_scaled_transfer_at_its_execution_index():
    topic = '0x' + keccak256(b'BalanceTransfer(address,address,uint256,uint256)').hex()
    treasuries = {'b137e7d16564c81ae2b0c8ee6b55de81dd46ece5', '856900aa78e856a5df1a2665ee3a66b2487cd68f'}
    actual = []
    for r in FIXTURE['canonical_logs']:
        if r['topic0'] != topic or r['topic1'][-40:] not in treasuries or not r['topic2'].endswith(HOLDER[2:]):
            continue
        scale = 18 if r['address'] in ('0x4dedf26112b3ec8ec46e7e31ea5e123490b05b8b', '0xc02ab1a5eaa8d1b114ef786d9bde108cd4364359') else 6
        assert len(r['data']) == 130
        scaled, index = D(int(r['data'][2:66], 16)), D(int(r['data'][66:], 16))
        actual.append((r['transaction_hash'], r['block_number'], r['block_time'], r['address'],
                       scaled * (index / 10**27) / 10**scale))
    assert tuple(actual) == GIFTS and len(actual) == 84
    assert sum(g[4] for g in GIFTS).quantize(D('.01')) == D('3178617.57')


def test_actual_saved_history_is_repaired_once_without_changing_cash_values_or_draws():
    h = history()
    linked = recognize_spark_reserve_gifts(h)
    assert recognize_spark_reserve_gifts(linked) is linked
    expected = defaultdict(D)
    for tx, _, _, token, value in GIFTS:
        expected[('ethereum:' + tx, f'ethereum:{HOLDER}:{token}')] += value
    assert len(h.batches) == len(linked.batches) == 21
    total = D(0)
    for before, after in zip(h.batches, linked.batches, strict=True):
        assert before.identity == after.identity
        assert (before.minted, before.minted_by_ilk) == (after.minted, after.minted_by_ilk)
        for a, b in zip(before.movements, after.movements, strict=True):
            assert (a.account, a.value_before, a.change, a.preserve_basis) == (b.account, b.value_before, b.change, b.preserve_basis)
            gift = expected.get((after.identity, b.account))
            assert b.external_income == (gift if gift is not None else a.external_income)
            total += b.external_income - a.external_income
    assert total.quantize(D('.01')) == D('3178617.57')
    # Additional non-treasury receipts in this transaction remain unclassified.
    mixed = next(b for b in linked.batches if b.identity.endswith('d157dbc535da15f78cfb94eacbfbfe20c0b728f9f561350484919dfe499d239d'))
    assert any(m.change - m.external_income > D('500000') for m in mixed.movements)


def test_january_gifts_do_not_absorb_the_unrelated_350m_governance_draw():
    b = next(b for b in history().batches if b.identity.endswith('311bb97ca6fe9688c5dd235fcde093829720b0e1933ef57e616fc0703e1d90e3'))
    h = CapitalHistory((b,), {}, {})
    r = replay_history(h, b.day, b.day)
    assert all(r.ledger.account(m.account).borrowed == 0 for m in b.movements)
    assert r.ledger.drawn == b.minted
    assert abs(r.unmatched_outflows[b.identity] - b.minted) < D('1e-8')
    assert not r.unmatched_receipts


def test_shared_spell_in_another_primes_history_is_an_exact_noop():
    b = history().batches[0]
    grove = 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:cash'
    b = replace(b, movements=(AssetMovement(grove, D(0), D(100)),))
    h = CapitalHistory((b,), {'E1': grove}, {})
    assert recognize_spark_reserve_gifts(h) is h


@pytest.mark.parametrize('fault', ['changed_stamp', 'conflicting_income', 'missing_position', 'insufficient_receipt'])
def test_conflicting_snapshot_is_rejected(fault):
    h = history()
    b = h.batches[0]
    if fault == 'changed_stamp':
        b = replace(b, timestamp=1)
    elif fault == 'missing_position':
        b = replace(b, movements=())
    else:
        m = b.movements[0]
        m = replace(m, external_income=D(1)) if fault == 'conflicting_income' else replace(m, change=D(0))
        b = replace(b, movements=(m, *b.movements[1:]))
    with pytest.raises(ValueError, match='Spark reserve gift'):
        recognize_spark_reserve_gifts(replace(h, batches=(b, *h.batches[1:])))
