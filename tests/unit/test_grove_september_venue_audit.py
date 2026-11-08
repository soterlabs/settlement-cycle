"""Pinned September evidence validates the two new venues against existing books."""
import importlib.util
import json
from copy import deepcopy
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def audit_module():
    spec = importlib.util.spec_from_file_location(
        'grove_september_audit', ROOT / 'scripts/audit_grove_september_2026.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope='module')
def evidence(audit_module):
    return json.loads(audit_module.EVIDENCE.read_text())


@pytest.fixture(scope='module')
def replay(audit_module, evidence):
    return audit_module.audit(evidence)


def test_existing_booked_venues_are_reproduced(replay):
    # Negative controls: these positions are already in September's report.
    assert D(replay['vaults']['E19']['revenue_existing_method']).quantize(D('.01')) == D('281614.44')
    assert D(replay['vaults']['E23']['revenue_existing_method']).quantize(D('.01')) == D('710966.35')
    assert D(replay['uniswap']['E12']['revenue']).quantize(D('.01')) == D('12817.36')


def test_missing_venues_separate_principal_from_revenue(replay):
    vault = replay['vaults']['E43']
    assert D(vault['cash_deposited_net']) == D('45000000')
    assert D(vault['revenue_existing_method']).quantize(D('.01')) == D('7219.49')
    lp = replay['uniswap']['E44']
    assert lp['token_ids'] == [1352494]
    assert D(lp['capital_net_of_collections']) == D('18978957.122864')
    assert D(lp['revenue']) == D('1002.553047')


def test_replay_never_reads_or_writes_live_caches(audit_module, evidence, monkeypatch):
    # A settlement operator can run this from the same shell as a live worker.
    monkeypatch.setenv('DATABASE_URL', 'postgresql://unused.invalid/production')
    monkeypatch.setenv('SETTLE_REQUIRE_POSTGRES', '1')
    monkeypatch.setenv('SETTLE_NO_CACHE', '0')

    def forbidden(*args, **kwargs):
        pytest.fail('Offline replay touched a cache or network transport')

    monkeypatch.setattr('settle.extract.postgres_store.get', forbidden)
    monkeypatch.setattr('settle.extract.postgres_store.put', forbidden)
    monkeypatch.setattr('settle.extract.cache.cache_dir', forbidden)
    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    result = audit_module.audit(evidence)
    assert result['uniswap']['E44']['revenue'] == '1002.553047'
    # Local overrides must not change the operator's subsequent live runs.
    import os
    assert os.environ['SETTLE_REQUIRE_POSTGRES'] == '1'
    assert os.environ['SETTLE_NO_CACHE'] == '0'


@pytest.mark.parametrize('case', [
    'duplicate_nfpm', 'before_period', 'after_period', 'wrong_emitter',
    'truncated_nfpm', 'missing_nfpm', 'wrong_topic', 'wrong_period',
    'conflicting_rpc', 'missing_transfer', 'duplicate_transfer', 'wrong_holder',
    'malformed_transfer', 'incomplete_scan',
])
def test_invalid_evidence_cannot_change_financial_results(audit_module, evidence, case):
    changed = deepcopy(evidence)
    row = changed['nfpm_logs'][0]
    if case == 'duplicate_nfpm':
        changed['nfpm_logs'].append(deepcopy(row))
    elif case == 'before_period':
        row['blockNumber'] = hex(changed['blocks']['ethereum'][0])
    elif case == 'after_period':
        row['blockNumber'] = hex(changed['blocks']['ethereum'][1] + 1)
    elif case == 'wrong_emitter':
        row['address'] = '0x' + '12' * 20
    elif case == 'truncated_nfpm':
        row['data'] = row['data'][:130]
    elif case == 'missing_nfpm':
        changed['nfpm_logs'].pop()
    elif case == 'wrong_topic':
        row['topics'][0] = '0x' + '12' * 32
    elif case == 'wrong_period':
        changed['period'] = '2026-10'
    elif case == 'conflicting_rpc':
        extra = deepcopy(changed['rpc_calls'][0])
        extra['result'] = '0x0'
        changed['rpc_calls'].append(extra)
    elif case == 'missing_transfer':
        changed['transfers']['ethereum'].pop()
    elif case == 'duplicate_transfer':
        changed['transfers']['base'].append(deepcopy(changed['transfers']['base'][0]))
    elif case == 'wrong_holder':
        changed['holders']['base']['legacy'] = '0x' + '12' * 20
    elif case == 'malformed_transfer':
        changed['transfers']['base'][0]['topics'][0] = '0x' + '12' * 32
    elif case == 'incomplete_scan':
        changed['coverage']['ethereum']['completed_chunks'] -= 1
    with pytest.raises(ValueError):
        audit_module.audit(changed)


def test_missing_pinned_rpc_fails_without_network_fallback(audit_module, evidence, monkeypatch):
    changed = deepcopy(evidence)
    changed['rpc_calls'].clear()

    def forbidden(*args, **kwargs):
        pytest.fail('Missing evidence attempted a live request')

    monkeypatch.setattr('requests.sessions.Session.request', forbidden)
    with pytest.raises(KeyError):
        audit_module.audit(changed)
