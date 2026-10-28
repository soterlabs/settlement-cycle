import copy
import gzip
import importlib
import json
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_history_cache import load_history


@pytest.fixture(scope='module')
def evidence():
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2] / 'scripts'))
    module = importlib.import_module('audit_spark_anchorage_draw_routes')
    root = Path(__file__).parents[1] / 'fixtures'
    path = root / 'spark_anchorage_origin_history.jsonl.gz'
    with gzip.open(path, 'rt') as src:
        key = json.loads(next(src))['fingerprint']
    history = load_history(path, key)
    with gzip.open(root / 'spark_anchorage_draw_receipts.json.gz', 'rt') as src:
        receipts = json.load(src)
    return module, history, receipts


@pytest.mark.parametrize('tx,expected', [
    ('0x9f1eea716acf5c781e19880d98fbe1745ba413c380eed29e941a952673e0dc7b', '5000000'),
    ('0x15e49a0ab289bbc05ff883138c3d2d09e85b4ff67f42653c8bf96c3b43e9064b', '548611.110444'),
    ('0xeb7812c1bb950c16a4e70e7fb707b79a8cbf7a5e07c743d2decfcb36ad0b11ef', '1002961.928704'),
])
def test_real_isolated_partial_and_later_wipe_routes(evidence, tx, expected):
    module, history, receipts = evidence
    batch = next(b for b in history.batches if b.identity == 'ethereum:' + tx)
    proof = module.validate(batch, receipts[tx])
    assert D(proof['verified_borrowed_contribution']) == D(expected)
    assert D(expected) <= D(proof['facility_deposit_usdc'])


@pytest.mark.parametrize('fault', ['recipient', 'amount', 'psm_sender', 'order', 'duplicate', 'failed'])
def test_route_mutations_fail_closed(evidence, fault):
    module, history, receipts = evidence
    tx = '0x9f1eea716acf5c781e19880d98fbe1745ba413c380eed29e941a952673e0dc7b'
    batch = next(b for b in history.batches if b.identity == 'ethereum:' + tx)
    receipt = copy.deepcopy(receipts[tx])
    deposit = next(log for log in receipt['logs'] if log['address'] == module.USDC
                   and log['topics'][0] == module.TRANSFER_TOPIC0
                   and module.address(log['topics'][2]) == module.ESCROW)
    if fault == 'recipient':
        deposit['topics'][2] = '0x' + '1' * 64
    elif fault == 'amount':
        deposit['data'] = '0x' + f'{1:064x}'
    elif fault == 'psm_sender':
        incoming = next(log for log in receipt['logs'] if log['address'] == module.USDC
                        and log['topics'][0] == module.TRANSFER_TOPIC0
                        and module.address(log['topics'][1]) == module.POCKET)
        incoming['topics'][1] = '0x' + '1' * 64
    elif fault == 'order':
        deposit['logIndex'] = '0x0'
    elif fault == 'duplicate':
        receipt['logs'].append(deposit)
    else:
        receipt['status'] = '0x0'
    with pytest.raises(ValueError):
        module.validate(batch, receipt)


def test_facility_principal_unchanged_and_no_double_counting(evidence):
    module, history, receipts = evidence
    result = module.attribute(history, receipts, date(2026, 1, 1), date(2026, 8, 31))
    assert D(result['closing_facility_principal']) == D('208829226.694857')
    assert D(result['principal_returns_through_end']) == D('81575229.287194')
    assert D(result['direct_origin_remaining_before_refinancing']) == D('201630012.238073600641115000')
    assert D(result['gross_same_transaction_draws']) - D(result['principal_returns_through_end']) == D(result['direct_origin_remaining_before_refinancing'])
    assert all(D(value) == 0 for value in result['outstanding_direct_debt_floor_by_ilk'].values())
