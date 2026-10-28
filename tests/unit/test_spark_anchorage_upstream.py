import copy
import gzip
import importlib
import json
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_history_cache import load_history

ROOT = Path(__file__).parents[1] / 'fixtures'
START, END = date(2026, 1, 1), date(2026, 8, 31)


def load(name):
    path = ROOT / name
    with gzip.open(path, 'rt') as src:
        key = json.loads(next(src))['fingerprint']
    return load_history(path, key)


def read(name):
    with gzip.open(ROOT / name, 'rt') as src:
        return json.load(src)


@pytest.fixture(scope='module')
def evidence():
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2] / 'scripts'))
    module = importlib.import_module('audit_spark_anchorage_upstream')
    graph = importlib.import_module('audit_spark_usdc_origin_graph')
    facility = load('spark_anchorage_origin_history.jsonl.gz')
    source = graph.merge_history(load('spark_base_morpho_origin_history.jsonl.gz'),
                                 load('spark_usdc_origin_graph_supplement.jsonl.gz'))
    ids = {b.identity for b in facility.batches if any(m.account == module.ACCOUNT and m.change > 0 for m in b.movements)}
    support = module.build_support(source, read('spark_usdc_cctp_draw_receipts.json.gz'), ids, END)
    receipts = read('spark_anchorage_draw_receipts.json.gz')
    return module, facility, support, receipts


def case(evidence, prefix):
    module, history, support, receipts = evidence
    tx = next(key for key in receipts if key.startswith(prefix))
    batch = next(b for b in history.batches if b.identity == 'ethereum:' + tx)
    return module, batch, receipts[tx], support['transactions'][batch.identity], support['venues']


def test_previous_cash_and_bridge_funding_are_carried_without_a_fresh_draw(evidence):
    module, batch, receipt, witness, venues = case(evidence, '0xcff55b84')
    assert batch.minted == 0
    result = module.validate(batch, receipt, witness, venues)
    assert D(result['verified_origin']) > D('769000')
    assert D(result['verified_origin']) < D(result['facility_deposit_usdc'])
    module, batch, receipt, witness, venues = case(evidence, '0xeb7812c1')
    result = module.validate(batch, receipt, witness, venues)
    assert D(result['verified_origin']) > D('1610000')
    assert any(D(value) > 0 for value in result['source_receipt_credits'].values())


def test_savings_cash_is_identified_separately_and_not_labelled_sky_borrowing(evidence):
    module, batch, receipt, witness, venues = case(evidence, '0x8cd38674')
    borrowed = module.validate(batch, receipt, witness, venues)
    external = module.validate(batch, receipt, witness, venues, savings_origin=True)
    assert D(borrowed['verified_origin']) == 0
    assert D(external['verified_origin']) == D('284722.221876')
    assert external['origin_kind'] == 'savings'


@pytest.mark.parametrize('fault', ['source_token', 'source_owner', 'missing_message', 'duplicate_message', 'opening_cash'])
def test_unproven_or_tampered_upstream_evidence_fails_closed(evidence, fault):
    module, batch, original, support, venues = case(evidence, '0xeb7812c1')
    receipt, witness = copy.deepcopy(original), copy.deepcopy(support)
    if fault in ('source_token', 'source_owner'):
        field = 'token' if fault == 'source_token' else 'owner'
        next(iter(witness['claim_metadata'].values()))[field] = '0x' + '1' * 40
    elif fault == 'opening_cash':
        witness['opening_borrowed_cash'][venues['S26']] = '99999999999'
    else:
        message = next(log for log in receipt['logs'] if log['address'] == module.TRANSMITTER
                       and log['topics'][0] == module.MESSAGE_RECEIVED)
        if fault == 'missing_message':
            receipt['logs'].remove(message)
        else:
            receipt['logs'].append(copy.deepcopy(message))
    with pytest.raises(ValueError):
        module.validate(batch, receipt, witness, venues)


def test_august_explained_origin_exceeds_99_without_changing_principal_or_returns(evidence):
    module, history, support, receipts = evidence
    result = module.attribute(history, receipts, support, START, END)
    final = result['monthly'][-1]
    assert D(final['capital']) == D('208829226.694857')
    assert D(result['principal_returns_through_end']) == D('81575229.287194')
    assert D(final['borrowed']) == D('206103343.21572310896627154973735769843917540580330618234271735917')
    assert D(final['identified_savings']) == D('743811.069988')
    assert D(final['explained_percent']) > 99
    assert D(final['borrowed_percent']) < 99
    for row in result['monthly']:
        assert abs(D(row['borrowed']) + D(row['identified_savings']) + D(row['unclassified']) - D(row['capital'])) < D('1e-8')
    # Opening and upstream provenance is independently replayed, not inferred
    # by following the facility's future principal-return cash.
    assert 'S23' not in support['source_venues']
    assert len(support['verified_source_transactions']) == 153


def test_missing_upstream_evidence_preserves_unknown_capital(evidence):
    module, history, support, receipts = evidence
    incomplete = {**support, 'transactions': {}}
    result = module.attribute(history, receipts, incomplete, START, END)
    final = result['monthly'][-1]
    assert D(final['identified_savings']) == 0
    assert D(final['borrowed']) == D('201630012.238073600641115000')
    assert D(final['explained_percent']) < 99
