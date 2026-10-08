import gzip
import importlib.util
import json
import sys
from copy import deepcopy
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location('base_withdrawal_repair', ROOT/'scripts/repair_spark_base_morpho_withdrawals.py')
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def example():
    # 1,000 opening shares; one earned fee share; burn 100 for 119.9 USDC.
    batch = {'identity': 'base:example', 'chain': 'base', 'block': 1, 'timestamp': 2,
             'minted': '0', 'minted_by_ilk': {}, 'movements': [
                 {'account': repair.ACCOUNT, 'value_before': '1200', 'change': '-118.8', 'external_income': '1.2'},
                 {'account': 'other', 'value_before': '10', 'change': '119.9', 'external_income': '0'},
             ]}
    context = {'block': 1, 'timestamp': 2, 'cash': '119.9', 'fee_units': 10**18,
               'burned_units': 100*10**18, 'net_units': -99*10**18}
    return batch, context


def test_execution_price_retains_share_funding_fraction_and_exact_cash():
    batch, context = example()
    original = deepcopy(batch)
    fixed, change = repair.repair_batch(batch, context)
    assert batch == original
    m = fixed['movements'][0]
    assert D(m['external_income']) == D('1.199')
    assert D(m['external_income']) - D(m['change']) == D('119.9')
    # The exact fraction burned is 100/(1,000+1), including earned shares.
    assert D('119.9') / (D(m['value_before']) + D(m['external_income'])) == D(100)/D(1001)
    assert fixed['movements'][1] == batch['movements'][1]
    assert fixed['minted'] == batch['minted'] and fixed['minted_by_ilk'] == batch['minted_by_ilk']
    assert D(change['cash_less_old_debit']) == D('-.1')


def test_fee_larger_than_burn_still_uses_actual_withdrawal_cash():
    batch, context = example()
    batch['movements'][0].update(value_before='0', external_income='120', change='60')
    context.update(cash='59.95', fee_units=100*10**18, burned_units=50*10**18, net_units=50*10**18)
    fixed, _ = repair.repair_batch(batch, context)
    m = fixed['movements'][0]
    assert D(m['change']) == D('59.95')
    assert D(m['external_income']) == D('119.9')
    assert D(m['value_before']) == 0


@pytest.mark.parametrize('fault', ['missing_income', 'wrong_change', 'wrong_block'])
def test_inconsistent_old_snapshot_is_not_repriced(fault):
    batch, context = example()
    if fault == 'missing_income':
        batch['movements'][0]['external_income'] = '0'
    elif fault == 'wrong_change':
        batch['movements'][0]['change'] = '-100'
    else:
        batch['block'] = 2
    with pytest.raises(ValueError):
        repair.repair_batch(batch, context)


def test_failed_completeness_check_cannot_replace_a_verified_output(tmp_path, monkeypatch):
    history, events, output, audit = [tmp_path/n for n in ('history.gz', 'events.json', 'output.gz', 'audit.json')]
    batch, context = example()
    with gzip.open(history, 'wt') as source:
        source.write(json.dumps({'fingerprint': 'test'})+'\n'+json.dumps(batch)+'\n')
    events.write_text('[]')
    output.write_bytes(b'previous verified output')
    monkeypatch.setattr(repair, 'withdrawal_contexts', lambda _: {'base:missing': context})
    monkeypatch.setattr(sys, 'argv', ['repair', '--history', str(history), '--events', str(events),
                                    '--output', str(output), '--audit', str(audit)])
    with pytest.raises(ValueError, match='Snapshot misses observed'):
        repair.main()
    assert output.read_bytes() == b'previous verified output'
    assert not audit.exists()


def test_canonical_base_fee_withdrawals_reproduce_real_repaired_movements():
    events = json.loads(gzip.decompress((ROOT/'tests/fixtures/spark_base_morpho_fee_withdrawals.json.gz').read_bytes()))
    contexts = repair.withdrawal_contexts(events)
    assert len(contexts) == 37809
    examples = json.loads((ROOT/'tests/fixtures/spark_base_morpho_withdrawal_examples.json').read_text())
    for example in examples:
        batch = example['original']
        # Match the standalone CLI's decimal context for this saved witness.
        with localcontext() as ctx:
            ctx.prec = 28
            fixed, change = repair.repair_batch(batch, contexts[batch['identity']])
        assert fixed == example['repaired']
        assert change == example['audit']
    assert D(examples[0]['audit']['cash_less_old_debit']) == D('23.53546924090129212390')
