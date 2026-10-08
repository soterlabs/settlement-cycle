import importlib.util
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_morpho_fees import ACCRUE_INTEREST_V2

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location('repair_fees', ROOT / 'scripts/repair_spark_morpho_fee_history.py')
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)
VAULT = '0xc7cdcfdefc64631ed6799c95e3b110cd42f2bd22'
ACCOUNT = f'ethereum:{repair.HOLDER}:{VAULT}'
ZERO = '0x' + '0'*64
WHO = '0x' + repair.HOLDER[2:].rjust(64, '0')


def row(block, index, sig, topics, words):
    ts = [sig, *topics, *([None]*(3-len(topics)))]
    return dict(block_number=block, block_time=100+block, log_index=index, address=VAULT,
                topic0=ts[0], topic1=ts[1], topic2=ts[2], topic3=ts[3],
                data='0x'+''.join(f'{w:064x}' for w in words), transaction_hash=f'0x{block:064x}')


def examples(fee=2, burned=10):
    rows = [row(1, 0, repair.TRANSFER_TOPIC0, [ZERO, WHO], [100*10**18]),
            row(2, 0, ACCRUE_INTEREST_V2, [], [100*10**6, 110*10**6, fee*10**18, 0]),
            row(2, 1, repair.TRANSFER_TOPIC0, [ZERO, WHO], [fee*10**18]),
            row(2, 2, repair.TRANSFER_TOPIC0, [WHO, ZERO], [burned*10**18]),
            row(2, 3, repair.WITHDRAW, [WHO, WHO, WHO], [11*10**6, burned*10**18])]
    batch = dict(identity=f'ethereum:0x{2:064x}', chain='ethereum', block=2, timestamp=102,
                 minted='0', minted_by_ilk={}, movements=[dict(account=ACCOUNT, value_before='100',
                 change=str(fee-burned), external_income='0', preserve_basis=False)])
    return rows, batch


@pytest.mark.parametrize('fee', [2, 20])
def test_fee_repair_matches_exact_cash_even_when_net_shares_increase(fee):
    rows, batch = examples(fee)
    original = deepcopy(batch)
    contexts = repair.fee_contexts(rows)
    fixed, audit = repair.repair_batch(batch, contexts)
    assert batch == original
    m = fixed['movements'][0]
    assert D(m['external_income']) == D(fee)*D('1.1')
    assert D(m['change']) - D(m['external_income']) == -11
    assert D(m['value_before']) == 110
    assert fixed['minted'] == batch['minted'] and len(audit) == 1
    assert repair.repair_batch({**batch, 'identity': 'unrelated'}, contexts)[0] == {**batch, 'identity': 'unrelated'}


def test_incomplete_share_history_conflicting_evidence_and_already_classified_snapshot_fail():
    rows, batch = examples()
    with pytest.raises(ValueError, match='Incomplete inception'):
        repair.fee_contexts(rows[1:])
    with pytest.raises(ValueError, match='Conflicting'):
        repair.fee_contexts([*rows, {**rows[0], 'data': '0x'+f'{1:064x}'}])
    contexts = repair.fee_contexts(rows)
    with pytest.raises(ValueError, match='unclassified'):
        repair.repair_batch({**batch, 'timestamp': 1}, contexts)
    wrong = deepcopy(batch)
    wrong['movements'][0]['change'] = '42'
    with pytest.raises(ValueError, match='differs from'):
        repair.repair_batch(wrong, contexts)
    wrong['movements'][0]['external_income'] = '1'
    with pytest.raises(ValueError, match='unclassified'):
        repair.repair_batch(wrong, contexts)


def test_real_old_and_new_vault_receipts_authenticate_the_omitted_fee_mints():
    import gzip
    import json

    from settle.domain.primes import Chain
    from settle.extract.hypersync import LogRow
    from settle.normalize.allocation_morpho_fees import V2_VAULTS, fee_mints

    examples = json.loads(gzip.decompress((ROOT / 'tests/fixtures/spark_morpho_v2_fee_examples.json.gz').read_bytes()))
    seen = set()
    for example in examples:
        rows = [LogRow(**r) for r in example['rows']]
        found = fee_mints(Chain.ETHEREUM, rows, {(v, repair.HOLDER) for v in V2_VAULTS[Chain.ETHEREUM]})
        assert found
        for (vault, holder), units in found.items():
            seen.add(vault)
            canonical = [r for r in rows if r.address == vault and r.topic0 == ACCRUE_INTEREST_V2]
            assert units == sum(int(r.data[130:194], 16) + int(r.data[194:258], 16) for r in canonical)
            assert any(r.address == vault and r.topic0 == repair.TRANSFER_TOPIC0
                       and r.topic1 == ZERO and r.topic2.endswith(holder[2:]) for r in rows)
            old = next(m for m in example['batch']['movements'] if m['account'].endswith(vault))
            assert D(old['external_income']) == 0  # The pinned input omitted these.
    assert seen == V2_VAULTS[Chain.ETHEREUM]


def test_opening_deposit_fee_requires_an_explicit_exact_block_price():
    rows, batch = examples()
    contexts = repair.fee_contexts(rows)
    ctx = dict(next(iter(contexts.values())), units_before=0, net_units=102*10**18,
               fee_units=2*10**18, deposits=[(D(100), 100*10**18)], withdrawals=[])
    contexts = {(batch['identity'], ACCOUNT): ctx}
    batch['movements'][0].update(value_before='0', change='100')
    with pytest.raises(ValueError, match='Cannot recover'):
        repair.repair_batch(batch, contexts)
    key = batch['identity'] + '|' + ACCOUNT
    prices = {key: {'block': 2, 'price_usd': '1.05'}}
    fixed, _ = repair.repair_batch(batch, contexts, prices)
    assert D(fixed['movements'][0]['external_income']) == D('2.10')
    assert D(fixed['movements'][0]['change']) == D('102.10')
    with pytest.raises(ValueError, match='Cannot recover'):
        repair.repair_batch(batch, contexts, {key: {'block': 1, 'price_usd': '1.05'}})
