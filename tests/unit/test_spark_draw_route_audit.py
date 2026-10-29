import gzip
import importlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

import pytest

from settle.normalize.allocation_history_cache import load_history

ROOT = Path(__file__).resolve().parents[1] / 'fixtures'


@pytest.fixture
def data(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT.parents[1] / 'scripts'))
    module = importlib.import_module('audit_spark_draw_routes')
    h = load_history(ROOT / 'spark_susds_origin_history.jsonl.gz', 'spark-origin-slim')
    with gzip.open(ROOT / 'spark_susds_draw_route_receipts.json.gz', 'rt') as f:
        receipts = json.load(f)
    return module, h, receipts


def test_receipt_routes_preserve_debt_movements_and_external_funding(data):
    module, h, receipts = data
    fixed, proofs = module.separate(h, receipts)
    assert len(proofs) == 6
    by_id = {b.identity: b for b in h.batches}
    for proof in proofs:
        old = by_id[proof['transaction']]
        parts = [b for b in fixed.batches if b.identity.startswith(old.identity + ':audited-')]
        assert len(parts) == 2
        with localcontext() as ctx:
            ctx.prec = 60
            assert sum((b.minted for b in parts), D(0)) == old.minted
        assert sorted((m for b in parts for m in b.movements), key=lambda m: m.account) == sorted(
            old.movements, key=lambda m: m.account)
        assert tuple(op for b in parts for op in b.external_funding) == old.external_funding
    assert any(D(p['existing_cash_spent']) > 0 for p in proofs)
    assert any(int(p['forwarded_susds_shares_raw']) > 0 for p in proofs)


def test_subsequent_withdrawals_reduce_newly_traced_origin_without_changing_capital(data):
    module, h, receipts = data
    from audit_allocation_positions import audit
    from audit_scoped_borrowing_origins import trace
    start, end = date(2026, 1, 1), date(2026, 8, 31)
    fixed, _ = module.separate(h, receipts)
    before, after = audit(h, 'S32', start, end), audit(fixed, 'S32', start, end)
    assert before['daily'] == after['daily']
    base = D(trace(h, ('S32',), end)['closing_borrowed_by_account'][module.SUSDS])
    improved = D(trace(fixed, ('S32',), end)['closing_borrowed_by_account'][module.SUSDS])
    assert base.quantize(D('.01')) == D('405963203.49')
    assert improved.quantize(D('.01')) == D('429495619.36')
    assert improved <= D(after['closing_capital'])
    assert improved - base < sum(D(b['drawn_usds']) for b in module.separate(h, receipts)[1])


@pytest.mark.parametrize('mutation', ['wrong_buffer', 'wrong_amount', 'wrong_block', 'duplicate_log'])
def test_tampered_receipt_is_rejected(data, mutation):
    module, h, receipts = data
    tx = next(iter(receipts))
    receipt = deepcopy(receipts[tx])
    b = next(b for b in h.batches if b.identity == 'ethereum:' + tx)
    if mutation == 'wrong_block':
        receipt['blockNumber'] = hex(b.block + 1)
    elif mutation == 'duplicate_log':
        receipt['logs'].append(deepcopy(receipt['logs'][0]))
    else:
        log = next(log for log in receipt['logs']
                   if log['address'] == module.USDS.split(':')[-1]
                   and log['topics'][0] == module.TRANSFER_TOPIC0
                   and log['topics'][2][-40:] == module.ALM[2:])
        if mutation == 'wrong_buffer':
            log['topics'][1] = '0x' + '0' * 64
        else:
            log['data'] = hex(int(log['data'], 16) + 10**18)
    with pytest.raises(ValueError):
        module.validate(b, receipt)


def test_missing_history_and_changed_normalized_income_fail(data):
    module, h, receipts = data
    with pytest.raises(ValueError, match='no original'):
        module.separate(replace(h, batches=()), receipts)
    tx, receipt = next(iter(receipts.items()))
    b = next(b for b in h.batches if b.identity == 'ethereum:' + tx)
    b = replace(b, movements=tuple(replace(m, external_income=D(1)) if m.account == module.SUSDS else m
                                  for m in b.movements))
    with pytest.raises(ValueError, match='Normalized'):
        module.validate(b, receipt)


def test_later_usds_repayment_is_not_silently_folded_into_isolated_draw(data):
    module, h, _ = data
    with gzip.open(ROOT / 'spark_susds_mixed_origin_receipts.json.gz', 'rt') as f:
        receipts = json.load(f)
    by_id = {b.identity: b for b in h.batches}
    for tx, receipt in receipts.items():
        # Both deposit Sky USDS first, then receive more USDS and repay the
        # Buffer. Net batch debt is insufficient evidence for the gross legs.
        with pytest.raises(ValueError, match='not isolated'):
            module.validate(by_id['ethereum:' + tx], receipt)
