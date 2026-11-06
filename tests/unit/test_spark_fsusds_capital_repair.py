import copy
import importlib.util
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('fsusds_repair', ROOT / 'scripts/repair_spark_fsusds_capital.py')
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)
WITNESSES = json.loads((ROOT / 'tests/fixtures/spark_fsusds_capital.json').read_text())


def test_all_22_actual_exchanges_balance_without_changing_cash_or_debt():
    receipts, outflows = D(0), D(0)
    for witness in WITNESSES:
        batch = witness['batch']
        original = copy.deepcopy(batch)
        fixed, audit = repair.repair_batch(batch, witness['receipt'])
        assert batch == original
        assert sum(D(m['change']) for m in fixed['movements']) == 0
        assert fixed['minted'] == batch['minted']
        assert fixed['minted_by_ilk'] == batch['minted_by_ilk']
        changed = [(a, b) for a, b in zip(batch['movements'], fixed['movements'], strict=True) if a != b]
        assert len(changed) == 1
        before, after = changed[0]
        assert before['external_income'] == after['external_income']
        assert before['preserve_basis'] == after['preserve_basis']
        if D(before['value_before']):
            assert abs(D(after['value_before']) / D(before['value_before']) - D(audit['underlying_usd_price'])) < D('1e-25')
        receipts += max(D(audit['old_false_gap']), D(0))
        outflows += max(-D(audit['old_false_gap']), D(0))
    assert len(WITNESSES) == 22
    assert receipts > D(500000)
    assert outflows > D(450000)


@pytest.mark.parametrize('mutation', ['cash', 'share', 'block', 'duplicate', 'recipient', 'gift'])
def test_repair_rejects_inconsistent_evidence(mutation):
    witness = copy.deepcopy(WITNESSES[0])
    batch, receipt = witness['batch'], witness['receipt']
    if mutation == 'cash':
        cash = next(r for r in receipt['logs'] if r['address'].startswith('0x5875') and r['topics'][0] == repair.TRANSFER_TOPIC0)
        cash['data'] = '0x' + f'{2 * 10**18:064x}'
    elif mutation == 'share':
        batch['movements'][1]['change'] = '2'
    elif mutation == 'block':
        batch['block'] += 1
    elif mutation == 'duplicate':
        receipt['logs'].append(receipt['logs'][0])
    elif mutation == 'recipient':
        event = next(r for r in receipt['logs'] if r['topics'][0] == repair.DEPOSIT)
        event['topics'][2] = '0x' + '1' * 64
    elif mutation == 'gift':
        batch['movements'][1]['external_income'] = '1'
    with pytest.raises(ValueError):
        repair.repair_batch(batch, receipt)
