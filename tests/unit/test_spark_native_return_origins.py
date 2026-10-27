import gzip
import importlib
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_history_cache import load_history

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures'
END = date(2026, 8, 31)


@pytest.fixture(scope='module')
def native():
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(FIXTURES.parents[1] / 'scripts'))
        module = importlib.import_module('audit_spark_native_return_origins')
        h = load_history(FIXTURES / 'spark_native_origin_support.jsonl.gz', 'spark-native-support')
        yield module, h


def test_native_returns_carry_only_surviving_borrowed_basis(native):
    module, h = native
    proofs, origin = module.contributions(h, END)
    assert [D(p['verified_borrowed_contribution']).quantize(D('.01')) for p in proofs] == [
        D('71503201.44'), D('100939898.13')]
    for p in proofs:
        assert D(p['verified_borrowed_contribution']) < D(p['source_value']) < D(p['received_value'])
        assert D(origin['closing_borrowed_by_account'][p['claim']]) == 0
    assert module.contributions(h, date(2026, 7, 12))[0] == []


def test_without_original_draws_returns_cannot_manufacture_borrowing(native):
    module, h = native
    h = replace(h, batches=tuple(replace(b, minted=D(0), minted_by_ilk={}) for b in h.batches))
    proofs, _ = module.contributions(h, END)
    assert all(D(p['verified_borrowed_contribution']) == 0 for p in proofs)


def test_missing_bridge_source_is_rejected(native):
    module, h = native
    proofs, _ = module.contributions(h, END)
    h = replace(h, batches=tuple(b for b in h.batches if b.identity != proofs[0]['source']))
    with pytest.raises(ValueError, match='funded source'):
        module.contributions(h, END)


def test_combined_replay_preserves_capital_and_counts_transfers_once(native):
    _module, support = native
    from audit_spark_savings_origin import attribute
    h = load_history(FIXTURES / 'spark_susds_origin_history.jsonl.gz', 'spark-origin-slim')
    batches = {b.identity: b for b in h.batches}
    for b in support.batches:
        if b.identity in batches:
            assert batches[b.identity] == b
        batches[b.identity] = b
    h = replace(support, batches=tuple(batches.values()))
    receipts = json.load(gzip.open(FIXTURES / 'spark_susds_ordered_draw_receipts.json.gz'))
    result = attribute(h, receipts, date(2026, 1, 1), END,
                       allow_mixed_prefix=True, include_native_returns=True)
    assert D(result['closing_capital']).quantize(D('.01')) == D('703133724.15')
    assert D(result['closing_borrowed']).quantize(D('.01')) == D('636767362.15')
    assert D(result['remaining_origin_to_classify']).quantize(D('.01')) == D('66366362.00')
    assert len(result['native_return_proofs']) == 2
    assert len(result['verified_routes']) == 108
    assert len(result['rejected_routes']) == 18
    # No incremental mint: each custody return replaces the single receipt's
    # zero direct-draw attribution with the source ledger's released basis.
    rows = {r['transaction']: r for r in result['origin_trace']['transactions']}
    for p in result['native_return_proofs']:
        row = rows[p['transaction']]
        assert row['draw_by_ilk'] == {}
        assert row['receipt_verified_contribution'] == p['verified_borrowed_contribution']
