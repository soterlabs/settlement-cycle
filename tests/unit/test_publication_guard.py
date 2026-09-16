from concurrent.futures import ThreadPoolExecutor

import pytest

from settle.extract import rpc
from settle.extract.publication import (
    PublicationGuard, RequiredInputFailure, optional_revert, source_operation,
)


def test_guard_is_thread_visible_and_restores_monthly_behavior():
    @source_operation
    def read(): raise rpc.RPCError('provider details must not be public')
    with pytest.raises(RequiredInputFailure, match='RPCError'), PublicationGuard():
        with ThreadPoolExecutor() as pool:
            try:
                pool.submit(read).result()
            except Exception:
                pytest.fail('monthly fallback caught a required input failure')
    with pytest.raises(rpc.RPCError):
        read()


def test_only_typed_execution_reverts_can_be_optional(monkeypatch):
    monkeypatch.setattr(rpc, '_eth_call_response', lambda *a: rpc._ContractRevert())
    with PublicationGuard(), optional_revert(), pytest.raises(rpc.EVMRevert):
        rpc.eth_call(None, None, None, 1)
    with pytest.raises(RequiredInputFailure), PublicationGuard():
        rpc.eth_call(None, None, None, 1)
    def unavailable(*args): raise rpc.RPCError('execution reverted while gateway is unavailable')
    monkeypatch.setattr(rpc, '_eth_call_response', unavailable)
    with pytest.raises(RequiredInputFailure), PublicationGuard(), optional_revert():
        rpc.eth_call(None, None, None, 1)


def test_nested_guards_cannot_disable_outer_guard():
    with pytest.raises(RequiredInputFailure), PublicationGuard():
        with pytest.raises(RuntimeError, match='separate worker processes'), PublicationGuard():
            pass
        @source_operation
        def read(): raise ValueError('bad input')
        # Even a caller incorrectly catching BaseException cannot publish.
        with pytest.raises(RequiredInputFailure):
            read()
        # Outer context exit still checks its sticky failure.


@pytest.mark.parametrize('error', [ValueError('bad ABI'), rpc.RPCError('outage')])
def test_curve_malformed_probe_is_not_a_valid_coin_count(monkeypatch, error):
    from settle.extract import curve
    monkeypatch.setenv('SETTLE_NO_CACHE', '1')
    monkeypatch.delenv('SETTLE_REQUIRE_POSTGRES', raising=False)
    def coin(*args):
        if args[2] == 2: raise error
        return bytes(20)
    monkeypatch.setattr(curve, 'coin_at', coin)
    with pytest.raises(RequiredInputFailure), PublicationGuard():
        curve.n_coins(None, None, 1)


def test_curve_typed_probe_termination_is_supported(monkeypatch):
    from settle.extract import curve
    monkeypatch.setenv('SETTLE_NO_CACHE', '1')
    monkeypatch.delenv('SETTLE_REQUIRE_POSTGRES', raising=False)
    def coin(*args):
        if args[2] == 2: raise rpc.EVMRevert('execution reverted')
        return bytes(20)
    monkeypatch.setattr(curve, 'coin_at', coin)
    with PublicationGuard():
        assert curve.n_coins(None, None, 1) == 2


@pytest.mark.parametrize('recover', [True, False])
def test_hypersync_owns_rate_limit_retries(monkeypatch, recover):
    import requests
    from settle.extract import hypersync
    from settle.revenue.verification import ProviderAudit
    monkeypatch.setattr('time.sleep', lambda _: None)
    calls = []
    def send(session, request, **kwargs):
        calls.append(request)
        response = requests.Response()
        response.status_code = 200 if recover and len(calls) == 2 else 429
        response._content = b'{"data": []}'
        response.headers['Retry-After'] = '1'
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    def query():
        with PublicationGuard(), ProviderAudit():
            return hypersync._execute('ethereum', {}, {}, requests.post)
    if recover:
        assert query() == {'data': []}
        assert len(calls) == 2
    else:
        with pytest.raises(RequiredInputFailure):
            query()
        assert len(calls) == 4


def test_optional_probe_cannot_swallow_exhausted_transport(monkeypatch):
    import requests
    from settle.domain.primes import Address, Chain
    from settle.revenue.verification import ProviderAudit
    monkeypatch.setenv('SETTLE_NO_CACHE', '1')
    monkeypatch.delenv('SETTLE_REQUIRE_POSTGRES', raising=False)
    monkeypatch.setenv('ETH_RPC', 'https://fixture.invalid')
    monkeypatch.setattr(rpc, 'DEFAULT_RETRY_ATTEMPTS', 1)
    def send(session, request, **kwargs):
        response = requests.Response()
        response.status_code = 503
        response._content = b'unavailable'
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    address = Address.from_str('0x' + '01' * 20)
    with pytest.raises(RequiredInputFailure), PublicationGuard(), ProviderAudit():
        rpc.scaled_balance_of(Chain('ethereum'), address, address, 1)


@pytest.mark.parametrize('method', ['native_balance', 'is_contract_deployed'])
def test_malformed_rpc_result_is_fatal_after_transport_success(monkeypatch, method):
    from settle.domain.primes import Address, Chain
    monkeypatch.setenv('SETTLE_NO_CACHE', '1')
    monkeypatch.delenv('SETTLE_REQUIRE_POSTGRES', raising=False)
    monkeypatch.setenv('ETH_RPC', 'https://fixture.invalid')
    monkeypatch.setattr(rpc, '_post', lambda *a: 'malformed')
    address = Address.from_str('0x' + '01' * 20)
    with pytest.raises(RequiredInputFailure), PublicationGuard():
        getattr(rpc, method)(Chain('ethereum'), address, 1)
