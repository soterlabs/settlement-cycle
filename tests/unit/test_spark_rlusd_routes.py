"""Receipt evidence must match real cash and pinned custody balances."""
import importlib.util
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.syspath_prepend(str(Path('scripts').resolve()))
    name = 'audit_spark_rlusd_routes'
    spec = importlib.util.spec_from_file_location(name, 'scripts/audit_spark_rlusd_routes.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def deposit(m):
    def topic(a):
        return '0x' + a[2:].rjust(64, '0')

    def log(i, token, event, sender, receiver, data):
        return {'block_number': 1, 'log_index': i, 'transaction_hash': 'tx',
                'address': token, 'topic0': event, 'topic1': topic(sender),
                'topic2': topic(receiver), 'data': '0x' + data}

    raw = f'{10**18:064x}'
    return [log(1, m.RLUSD, m.TRANSFER, m.ALM, m.SENTORA, raw),
            log(2, m.SENTORA, m.TRANSFER, m.ZERO, m.ALM, raw),
            log(3, m.SENTORA, m.DEPOSIT, m.ALM, m.ALM, raw + raw)]


def run(m, rows, closing='1'):
    return m.analyze(rows, {'opening': '2', 'closing': closing},
                     [{'account': 'unallocated:ethereum:tx', 'cost': D('.01')}])


def test_cash_and_receipt_match_without_counting_joined_logs_twice(audit):
    rows = deposit(audit)
    result = run(audit, rows + rows)
    assert result['log_count'] == 3
    assert result['routes'][audit.SENTORA]['matched_transactions'] == 1
    assert result['associated_residual_cost'] == D('.01')
    assert result['publishable'] is False


def test_cash_outflow_without_receipt_is_not_attributed(audit):
    rows = deposit(audit)
    result = run(audit, [rows[0], rows[2]])
    assert result['routes'][audit.SENTORA]['failed_matches'] == ['tx']
    assert result['associated_residual_cost'] == 0


def test_wrong_deposit_assets_not_certified(audit):
    rows = deposit(audit)
    rows[2]['data'] = '0x' + f'{2 * 10**18:064x}' + f'{10**18:064x}'
    assert run(audit, rows)['routes'][audit.SENTORA]['failed_matches'] == ['tx']


def test_missing_transfers_rejected_by_balance_control(audit):
    with pytest.raises(ValueError, match='pinned balances'):
        run(audit, deposit(audit), closing='0')


def test_conflicting_duplicate_log_rejected(audit):
    rows = deposit(audit)
    with pytest.raises(ValueError, match='Conflicting duplicate'):
        run(audit, [*rows, {**rows[0], 'data': '0x00'}])
