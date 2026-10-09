import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('legacy_curve', Path(__file__).parents[2]/'scripts/audit_spark_legacy_curve_swaps.py')
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def evidence():
    pool = next(iter(audit.POOLS))
    def who(address):
        return '0x'+address[2:].rjust(64, '0')
    def row(index, address, topic, a, b, words):
        return {'address': address, 'block_number': 10, 'block_time': 100,
                'transaction_hash': '0x123', 'log_index': index, 'topic0': topic,
                'topic1': who(a), 'topic2': who(b), 'topic3': None,
                'data': '0x'+''.join(f'{v:064x}' for v in words)}
    rows = [row(1, audit.USDC, audit.TRANSFER_TOPIC0, audit.HOLDER, pool, [500000000000]),
            row(2, audit.USDT, audit.TRANSFER_TOPIC0, pool, audit.HOLDER, [499512656668]),
            row(3, pool, audit.CURVESWAP, audit.HOLDER, pool, [0, 500000000000, 1, 499512656668])]
    return {'holder': audit.HOLDER, 'metadata': {'pin': 20, 'pools': {
        p: {'coin0': coins[0], 'coin1': coins[1], 'first_swap_coins': list(coins), 'first_swap_block': 10}
        for p, coins in audit.POOLS.items()}}, 'rows': rows}


def test_exchange_amounts_match_actual_cash_and_keep_execution_loss():
    proof = evidence()
    rows, excluded = audit.audit(proof)
    assert not excluded
    assert rows[0]['gain'] == '-487.343332'
    assert proof == evidence()
    proof['rows'].append(deepcopy(proof['rows'][0]))
    assert audit.audit(proof) == (rows, excluded)


def test_unmatched_cash_is_not_explained_away():
    proof = evidence()
    proof['rows'].pop(0)
    assert audit.audit(proof) == ([], [{'identity': 'ethereum:0x123', 'reason': 'cash mismatch'}])


def test_wrong_coin_control_and_conflicting_logs_fail():
    proof = evidence()
    proof['metadata']['pools'][next(iter(audit.POOLS))]['coin0'] = audit.PYUSD
    with pytest.raises(ValueError, match='coin controls'):
        audit.audit(proof)
    proof = evidence()
    proof['rows'].append({**proof['rows'][0], 'data': '0x'+f'{1:064x}'})
    with pytest.raises(ValueError, match='Conflicting'):
        audit.audit(proof)


def test_real_legacy_pools_explain_the_reviewed_2025_outflows():
    import gzip
    import json

    path = Path(__file__).parents[1]/'fixtures/spark_legacy_curve_swaps.json.gz'
    rows, _ = audit.audit(json.loads(gzip.decompress(path.read_bytes())))
    by_tx = {r['identity']: r for r in rows}
    assert by_tx['ethereum:0x37500e81844741b9f17e933a4a1cf8f9b0fccd693130b723e734ef2b58afde19']['gain'] == '-487.343332'
    assert by_tx['ethereum:0x15ea32bf738b090d3bc2cb40efa2f3d778d9a60d91b55969bae10438d3d57625']['gain'] == '-399.754883'
    assert set().union(*(set(r['pools']) for r in rows)) == set(audit.POOLS)
