import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
spec = importlib.util.spec_from_file_location('execution_audit', ROOT/'scripts/audit_grove_execution_shortfalls.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
CURVE = json.loads((ROOT/'tests/fixtures/grove_curve_execution_events.json').read_text())
UNISWAP = json.loads((ROOT/'tests/fixtures/grove_uniswap_execution_events.json').read_text())


def test_canonical_swaps_require_actual_alm_payments_including_router_calls():
    assert len(audit.curve_shortfalls(CURVE)) == 85
    records = audit.uniswap_shortfalls(UNISWAP)
    assert len(records) == 150
    assert {r['holder'] for r in records} == {audit.HOLDER, audit.PAU}
    assert audit.uniswap_shortfalls([*UNISWAP, *UNISWAP]) == records
    with pytest.raises(ValueError, match='actual ALM transfers'):
        audit.uniswap_shortfalls([r for r in UNISWAP if r['topic0'] == audit.UNISWAP_SWAP])


def test_conflicting_duplicate_event_fails_proof():
    r = UNISWAP[0]
    with pytest.raises(ValueError, match='Conflicting'):
        audit.uniswap_shortfalls([*UNISWAP, {**r, 'block_number': 1}])
    r = CURVE[0]
    with pytest.raises(ValueError, match='Conflicting'):
        audit.curve_shortfalls([*CURVE, {**r, 'block_number': 1}])


def test_identification_is_transaction_exact_and_does_not_change_financing():
    record = audit.uniswap_shortfalls(UNISWAP)[0]
    tx = record['transaction_hash']
    key = 'ethereum:' + tx + ':reviewed-adapter'
    finance = {'unmatched_outflows': {key: record['execution_shortfall'],
                                     'ethereum:unrelated': record['execution_shortfall']},
               'input_hashes': {'control': 'unchanged'}}
    original = json.loads(json.dumps(finance))
    result = audit.classify(finance, [record], '2026-08')
    assert finance == original
    assert result['identified_execution_shortfalls'] == 1
    assert result['remaining_residuals'] == {'ethereum:unrelated': record['execution_shortfall']}
    with pytest.raises(ValueError, match='Ambiguous'):
        audit.classify(finance, [record, record], '2026-08')
    finance['unmatched_outflows'][key] = '0'
    assert audit.classify(finance, [record], '2026-08')['identified_execution_shortfalls'] == 0


def test_second_curve_pool_and_combined_swaps_match_whole_transaction():
    rows = json.loads((ROOT/'tests/fixtures/grove_ausd_curve_execution_events.json').read_text())
    records = audit.curve_shortfalls(rows,
        pool='0xe79c1c7e24755574438a26d5e062ad2626c04662',
        coins=(('0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', 6),
               ('0x00000000efe302beaa2b3e6e1b18d08d69a9012a', 6)))
    assert len(records) == 5
    uniswap = audit.uniswap_shortfalls(UNISWAP)
    curve = next(r for r in records if r['execution_shortfall'] == '363.977269')
    other = next(r for r in uniswap if r['transaction_hash'] == curve['transaction_hash'])
    finance = {'unmatched_outflows': {'ethereum:'+curve['transaction_hash']: '485.313107'},
               'input_hashes': {}}
    assert audit.classify(finance, [curve], '2026-08')['identified_execution_shortfalls'] == 0
    result = audit.classify(finance, [curve, other], '2026-08')
    assert result['identified_execution_shortfalls'] == 1
    assert len(result['matched'][0]['swaps']) == 2
    assert result['matched_shortfalls_total'] == '485.313107'
    assert not result['remaining_residuals']
