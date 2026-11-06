import copy
import gzip
import importlib
import json
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_history_cache import load_history

ROOT = Path(__file__).parents[1] / 'fixtures'


def load(name):
    path = ROOT / name
    with gzip.open(path, 'rt') as src:
        key = json.loads(next(src))['fingerprint']
    return load_history(path, key)


@pytest.fixture(scope='module')
def evidence():
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2] / 'scripts'))
    graph = importlib.import_module('audit_spark_usdc_origin_graph')
    routes = importlib.import_module('audit_spark_cctp_draw_routes')
    source = load('spark_usdc_cctp_source_batches.jsonl.gz')
    with gzip.open(ROOT / 'spark_usdc_cctp_draw_receipts.json.gz', 'rt') as src:
        receipts = json.load(src)
    tx = '0x0c75c1ed6074e317d0fcc0d6e691a6abd0393c148cb33081bf7a2a9e698051eb'
    batch = next(b for b in source.batches if b.identity == 'ethereum:' + tx)
    return graph, routes, source, batch, receipts[tx], receipts


def test_bridge_funding_precedes_unrelated_debt_repayment(evidence):
    _, routes, _, batch, receipt, _ = evidence
    result = routes.validate(batch, receipt)
    assert D(result['gross_draw_usds']) == D('594394.832629')
    assert D(result['gross_wipe_usds']) > D('550009')
    assert result['claims'][0]['claim'] == 'cctp:0:494564:0xbd3fa81b58ba92a82136038b25adec7066af3155'
    assert D(result['claims'][0]['borrowed']) == D('594394.832629')


@pytest.mark.parametrize('fault', ['nonce', 'owner', 'amount', 'duplicate', 'burn', 'claim'])
def test_bridge_proof_cannot_reuse_or_redirect_funding(evidence, fault):
    _, routes, _, batch, original, _ = evidence
    receipt = copy.deepcopy(original)
    event = next(log for log in receipt['logs'] if log['topics'][0] == routes.DEPOSIT_FOR_BURN)
    if fault == 'nonce':
        event['topics'][1] = '0x' + f'{1:064x}'
    elif fault == 'owner':
        event['topics'][3] = '0x' + '1' * 64
    elif fault == 'amount':
        event['data'] = '0x' + f'{1:064x}' + event['data'][66:]
    elif fault == 'duplicate':
        receipt['logs'].append(copy.deepcopy(event))
    elif fault == 'burn':
        burn = next(log for log in receipt['logs'] if log['address'] == routes.USDC
                    and log['topics'][0] == routes.TRANSFER_TOPIC0
                    and routes.address(log['topics'][1]) == routes.MINTER)
        burn['topics'][2] = '0x' + '1' * 64
    else:
        batch = replace(batch, movements=tuple(replace(m, value_before=D(1))
            if m.account.startswith('cctp:') else m for m in batch.movements))
    with pytest.raises(ValueError):
        routes.validate(batch, receipt)


def test_supplement_requires_identical_shared_history(evidence):
    graph, _, source, batch, _, _ = evidence
    base = replace(source, batches=(batch,))
    assert len(graph.merge_history(base, base).batches) == 1
    changed = replace(base, batches=(replace(batch, block=batch.block + 1),))
    with pytest.raises(ValueError):
        graph.merge_history(base, changed)


@pytest.mark.parametrize('fault', ['amount', 'chain', 'order', 'duplicate'])
def test_claim_graph_rejects_changed_receipt(evidence, fault):
    graph, _, source, batch, _, _ = evidence
    claim = next(m for m in batch.movements if m.account.startswith('cctp:') and m.change > 0)
    sent = replace(batch, movements=(claim,))
    received = replace(sent, identity='base:receipt', chain='base', timestamp=sent.timestamp + 10,
                       movements=(replace(claim, value_before=claim.change, change=-claim.change),))
    pending = graph.claim_links(replace(source, batches=(sent,)), {claim.account})
    assert pending[0]['receipt'] is None
    assert graph.claim_links(replace(source, batches=(sent, received)), {claim.account})[0]['receipt'] == 'base:receipt'
    if fault == 'amount':
        received = replace(received, movements=(replace(received.movements[0], change=-claim.change + 1),))
    elif fault == 'chain':
        received = replace(received, chain=sent.chain)
    elif fault == 'order':
        received = replace(received, timestamp=sent.timestamp)
    rows = (sent, received, received) if fault == 'duplicate' else (sent, received)
    with pytest.raises(ValueError):
        graph.claim_links(replace(source, batches=rows), {claim.account})


def test_august_graph_reproduces_full_history_above_99_percent(evidence):
    graph, _, _, _, _, receipts = evidence
    history = graph.merge_history(load('spark_base_morpho_origin_history.jsonl.gz'),
                                  load('spark_usdc_origin_graph_supplement.jsonl.gz'))
    result = graph.attribute(history, receipts)
    final = result['monthly'][-1]
    assert D(final['capital']) == D('283116139.249274376706966830133034106492575607234493990883206')
    assert abs(D(final['borrowed']) - D('280456335.511974807749629376164247099103680538972815714565146')) < D('1e-8')
    assert D(final['borrowed_percent']) > 99
    assert len(result['receipt_proofs']) == 153
