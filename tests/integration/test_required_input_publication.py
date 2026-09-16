from dataclasses import replace
from decimal import Decimal

import psycopg
import pytest
import requests

from settle.extract import rpc
from settle.revenue import store, worker
from tests.integration.test_input_cache_postgres import database  # noqa: F401
from tests.integration.test_revenue_store import example


@pytest.mark.parametrize('failure', ['http', 'timeout', 'jsonrpc', 'malformed', 'unregistered', 'unregistered_rpc'])
def test_swallowed_provider_failure_cannot_publish(database, monkeypatch, failure):  # noqa: F811
    monkeypatch.setattr(rpc, 'DEFAULT_RETRY_ATTEMPTS', 1)
    def send(session, request, **kwargs):
        if failure == 'timeout': raise requests.Timeout('provider private token')
        response = requests.Response()
        response.status_code = 503 if failure in {'http', 'unregistered'} else 200
        response._content = (b'{"error":{"code":-32603,"message":"missing trie node"}}'
                             if failure in {'jsonrpc', 'unregistered_rpc'} else b'{}')
        response.request = request
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    def compute(*args, **kwargs):
        try:
            if failure == 'unregistered_rpc':
                requests.post('https://fixture.invalid/rpc', json={'method': 'eth_getBalance'}).json()
            elif failure == 'unregistered':
                requests.get('https://fixture.invalid/rpc').raise_for_status()
            else:
                rpc._post('https://fixture.invalid/rpc', 'eth_getBalance', ['0xholder', '0x1'])
        except Exception:
            # The historical monthly behavior that used to publish a zero.
            return replace(example(), revenue=Decimal(0))
        raise AssertionError('failure fixture unexpectedly succeeded')
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        report = worker.run_prime(conn, 'obex', compute=compute,
                                  capture=lambda: store.Versions('test', 'test', '0'), attempts=1)
        assert report['status'] == 'failed'
        assert report['results'][0]['error_type'] == 'RequiredInputFailure'
        assert store.read(conn, 'obex') is None
        assert conn.execute('SELECT status FROM revenue_attempts').fetchall() == [('failed',)]


def test_successful_internal_retry_can_publish_and_worker_retry_retains_prior_result(database, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rpc, 'DEFAULT_RETRY_ATTEMPTS', 2)
    monkeypatch.setattr('time.sleep', lambda _: None)
    statuses = iter([503, 200])
    def send(session, request, **kwargs):
        response = requests.Response()
        response.status_code = next(statuses)
        response._content = b'{"result":"0x2a"}'
        response.request = request
        return response
    monkeypatch.setattr(requests.Session, 'send', send)
    def compute(*args, **kwargs):
        value = rpc._post('https://fixture.invalid/rpc', 'eth_getBalance', ['holder','0x1'])
        return replace(example(), revenue=Decimal(int(value, 16)))
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        versions = store.Versions('test', 'test', '0')
        report = worker.run_prime(conn, 'obex', compute=compute, capture=lambda: versions, attempts=1)
        assert report['status'] == 'ok'
        prior = store.read(conn, 'obex')
        assert prior['result']['revenue'] == '42'
        statuses = iter([503, 503, 503, 503])
        report = worker.run_prime(conn, 'obex', compute=compute,
                                  capture=lambda: replace(versions, inputs='corrected'), attempts=2)
        assert report['status'] == 'failed'
        assert store.read(conn, 'obex')['revision_id'] == prior['revision_id']
        assert conn.execute('SELECT status FROM revenue_attempts ORDER BY started_at').fetchall() == [
            ('succeeded',), ('failed',), ('failed',)]
        # A fresh worker attempt clears the sticky failure and can recover.
        statuses = iter([503, 503, 200])
        report = worker.run_prime(conn, 'obex', compute=compute,
                                  capture=lambda: replace(versions, inputs='corrected'),
                                  attempts=2, pause=lambda _: None)
        assert report['status'] == 'ok'
        assert store.read(conn, 'obex')['revision_id'] != prior['revision_id']
        assert conn.execute('SELECT status FROM revenue_attempts ORDER BY started_at').fetchall()[-2:] == [
            ('failed',), ('succeeded',)]


def test_full_grove_calculation_cannot_swallow_cash_distribution_failure(database):  # noqa: F811
    import os
    import subprocess
    import sys
    program = """
import os, requests, psycopg
from datetime import date
from unittest.mock import patch
from settle.compute import monthly_pnl
from settle.extract import rpc
from settle.revenue import store, worker
from tests.fixtures.revenue_transport import send
for name in rpc.RPC_ENV_VARS.values(): os.environ[name] = 'http://fixture.invalid'
original = monthly_pnl.get_position_value
seen = []
def position(prime, venue, *args, **kwargs):
 if venue.cash_distributions:
  seen.append(venue.id)
  return rpc._post('https://failure.invalid', 'eth_call', [])
 return original(prime, venue, *args, **kwargs)
def transport(session, request, **kwargs):
 if 'failure.invalid' in request.url:
  response = requests.Response()
  response.status_code = 503
  response._content = b'provider unavailable'
  return response
 return send(session, request, **kwargs)
with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as conn:
 store.apply_schema(conn)
 with patch.object(requests.Session, 'send', transport), patch.object(monthly_pnl, 'get_position_value', position), patch.object(rpc, 'DEFAULT_RETRY_ATTEMPTS', 1):
  report = worker.run_prime(conn, 'grove', today=date(2026,8,2), attempts=1, prepare=lambda *a: None, capture=lambda: store.Versions('test','test','0'))
 assert seen, 'must exercise the actual monthly cash-distribution fallback'
 assert report['results'][0]['error_type'] == 'RequiredInputFailure', report
 assert store.read(conn, 'grove') is None
 assert conn.execute('SELECT status FROM revenue_attempts').fetchall() == [('failed',)]
"""
    env = dict(os.environ, PYTHON_DOTENV_DISABLED='1', DATABASE_URL=database,
               SETTLE_REQUIRE_POSTGRES='1', ENVIO_API_TOKEN='fixture')
    result = subprocess.run([sys.executable, '-c', program], env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
