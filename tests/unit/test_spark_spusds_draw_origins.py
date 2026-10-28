import copy
import gzip
import importlib
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_history_cache import load_history


@pytest.fixture(scope='module')
def evidence():
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2] / 'scripts'))
    module = importlib.import_module('audit_spark_spusds_draws')
    root = Path(__file__).parents[1] / 'fixtures'
    path = root / 'spark_spusds_origin_history.jsonl.gz'
    with gzip.open(path, 'rt') as src:
        key = json.loads(next(src))['fingerprint']
    history = load_history(path, key)
    with gzip.open(root / 'spark_spusds_draw_receipts.json.gz', 'rt') as src:
        receipts = json.load(src)
    tx = '0x917eae55f38fcac8256362a3ed5fca0bb613e4568e18c0018e17ba65597a0172'
    batch = next(b for b in history.batches if b.identity == 'ethereum:' + tx)
    return module, history, receipts, batch, receipts[tx]


def test_savings_usdt_does_not_hide_sky_usds_supply(evidence):
    module, _, _, batch, receipt = evidence
    proof = module.validate(batch, receipt)
    assert D(proof['gross_draw_usds']) == D('7222021.056519919397347579')
    # The remaining USDS came from opening cash, which stays unclassified.
    assert D(proof['verified_borrowed_contribution']) == D(proof['gross_draw_usds'])
    assert D(proof['usds_supplied']) > D(proof['verified_borrowed_contribution'])
    assert sum(map(D, proof['savings_cash_received'].values())) == D('49298987.779554')


@pytest.mark.parametrize('fault', ['recipient', 'amount', 'debt', 'duplicate', 'status', 'share_payer'])
def test_tampered_route_fails_closed(evidence, fault):
    module, _, _, batch, original = evidence
    receipt = copy.deepcopy(original)
    if fault == 'debt':
        batch = replace(batch, minted=batch.minted + 1)
    elif fault == 'duplicate':
        receipt['logs'].append(receipt['logs'][0])
    elif fault == 'status':
        receipt['status'] = '0x0'
    elif fault == 'share_payer':
        log = next(item for item in receipt['logs'] if item['address'] == module.SPUSDS.split(':')[-1]
                   and item['topics'][0] == module.TRANSFER_TOPIC0)
        log['topics'][1] = '0x' + '1' * 64
    else:
        log = next(item for item in receipt['logs'] if item['address'] == module.POOL
                   and item['topics'][0] == module.SUPPLY
                   and module.address(item['topics'][1]) == module.USDS.split(':')[-1])
        if fault == 'recipient':
            log['topics'][2] = '0x' + '1' * 64
        else:
            log['data'] = log['data'][:66] + f'{1:064x}'
    with pytest.raises(ValueError):
        module.validate(batch, receipt)


def test_august_capital_unchanged_and_origin_above_99_percent(evidence):
    module, history, receipts, _, _ = evidence
    result = module.attribute(history, receipts, date(2026, 1, 1), date(2026, 8, 31))
    last = result['monthly'][-1]
    assert abs(D(last['capital']) - D('681732615.541465583750895939580467797153634947997230760671256')) < D('1e-8')
    assert D(last['borrowed_percent']) > 99
    assert D(last['borrowed']) <= D(last['capital'])
    assert result['receipt_proofs']
