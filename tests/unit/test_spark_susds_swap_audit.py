"""Historical rates authenticate non-par swap values independently of replay."""
import copy
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('susds_swap_audit', SCRIPTS / 'audit_spark_susds_swaps.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def data(*values):
    return '0x' + ''.join(n.to_bytes(32).hex() for n in values)


def addr(value):
    return '0x' + value[2:].rjust(64, '0')


def evidence():
    ray = audit.RAY
    meta = {'susds': audit.SUSDS, 'curve': audit.POOL, 'curve_coins': [audit.SUSDS, audit.USDT],
            'initial_block': 10, 'pin': 12, 'states': {
                '10': {'chi()': str(ray), 'rho()': '100', 'ssr()': str(2 * ray),
                       'timestamp': 100, 'convertToAssets(1e18)': str(10**18)},
                '12': {'chi()': str(2 * ray), 'rho()': '101', 'ssr()': str(2 * ray),
                       'timestamp': 101, 'convertToAssets(1e18)': str(2 * 10**18)}}}
    base = {'block_number': 12, 'block_time': 101, 'transaction_hash': '0x1234'}
    rows = [dict(base, log_index=2, address=audit.POOL, topic0=audit.SWAP,
                 topic1=addr(audit.HOLDER), data=data(0, 10**18, 1, 1_900_000)),
            dict(base, log_index=3, address=audit.SUSDS, topic0=audit.TRANSFER,
                 topic1=addr(audit.HOLDER), topic2=addr(audit.POOL), data=data(10**18)),
            dict(base, log_index=4, address=audit.USDT, topic0=audit.TRANSFER,
                 topic1=addr(audit.POOL), topic2=addr(audit.HOLDER), data=data(1_900_000))]
    return {'metadata': meta,
            'rate_fields': ['block_number', 'log_index', 'block_time', 'topic0', 'topic1', 'data'],
            'rate_rows': [[11, 1, 101, audit.DRIP, None, data(2 * ray, 0)]], 'swap_rows': rows}


def test_share_nav_proves_shortfall_and_duplicate_logs_do_not_double_count():
    p = evidence()
    p['swap_rows'].append(copy.deepcopy(p['swap_rows'][0]))
    result = audit.audit(p, {'outflows': [{'identity': 'ethereum:0x1234', 'amount': '0.1'}]})
    assert result['drips_verified'] == 1
    assert result['outflow_matches'] == 1
    assert result['matched_outflow_value'] == '0.1'
    assert result['rows'][0]['quote'] == str(2 * 10**18)


def test_wrong_rate_or_closing_state_cannot_be_called_execution_loss():
    p = evidence()
    p['rate_rows'][0][-1] = data(2 * audit.RAY + 1, 0)
    with pytest.raises(ValueError, match='Drip differs'):
        audit.audit(p)
    p = evidence()
    p['metadata']['states']['12']['convertToAssets(1e18)'] = str(3 * 10**18)
    with pytest.raises(ValueError, match='Closing sUSDS quote'):
        audit.audit(p)


def test_missing_cash_leg_is_excluded_and_conflicting_event_rejected():
    p = evidence()
    p['swap_rows'].pop()
    assert audit.audit(p)['swap_transactions'] == 0
    p = evidence()
    changed = dict(p['swap_rows'][0], data=data(0, 2 * 10**18, 1, 1_900_000))
    p['swap_rows'].append(changed)
    with pytest.raises(ValueError, match='Conflicting swap'):
        audit.audit(p)


def test_rate_change_requires_current_accrual_and_right_parameter():
    p = evidence()
    p['rate_rows'] = [[11, 0, 101, audit.FILE, '0x' + b'ssr'.ljust(32, b'\0').hex(), data(audit.RAY)]]
    with pytest.raises(ValueError, match='Invalid sUSDS rate change'):
        audit.audit(p)


def with_lp_withdrawal():
    p = evidence()
    base = {'block_number': 12, 'block_time': 101, 'transaction_hash': '0x1234'}
    p['swap_rows'].extend([
        dict(base, log_index=5, address=audit.SUSDS, topic0=audit.TRANSFER,
             topic1=addr(audit.POOL), topic2=addr(audit.HOLDER), data=data(3*10**18)),
        dict(base, log_index=6, address=audit.USDT, topic0=audit.TRANSFER,
             topic1=addr(audit.POOL), topic2=addr(audit.HOLDER), data=data(4_000_000)),
        dict(base, log_index=7, address=audit.POOL, topic0=audit.TRANSFER,
             topic1=addr(audit.HOLDER), topic2='0x'+'0'*64, data=data(10**18)),
        dict(base, log_index=8, address=audit.POOL, topic0=audit.REMOVE_LIQUIDITY,
             topic1=addr(audit.HOLDER), data=data(96, 192, 10**20, 2, 3*10**18, 4_000_000, 0))])
    return p


def test_proportional_lp_withdrawal_does_not_pollute_same_transaction_swap_value():
    result = audit.audit(with_lp_withdrawal())
    assert result['swap_transactions'] == 1
    row = result['rows'][0]
    assert row['gain'] == '-0.1'
    assert row['proportional_withdrawal_logs'] == [8]
    assert row['lp_cash_excluded_from_swap_gain'] == {
        audit.SUSDS: str(3*10**18), audit.USDT: '4000000'}


def test_lp_cash_requires_both_burn_and_returned_coins():
    p = with_lp_withdrawal()
    p['swap_rows'] = [r for r in p['swap_rows'] if r['log_index'] != 7]
    with pytest.raises(ValueError, match='matching LP burn'):
        audit.audit(p)
    p = with_lp_withdrawal()
    p['swap_rows'] = [r for r in p['swap_rows'] if r['log_index'] != 5]
    assert audit.audit(p)['swap_transactions'] == 0
