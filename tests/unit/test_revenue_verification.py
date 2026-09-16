import dataclasses
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
import requests

from settle.revenue.verification import ProviderAudit, canonical, compare, digest


def report(result, calls=()):
    return dict(prime="obex", cutoff="2026-08-01", result=result,
                calls=list(calls), dune_attempts=0)


def test_comparison_checks_every_field_and_exact_decimal():
    @dataclasses.dataclass
    class Result:
        total: Decimal
        daily: list
    first = canonical(Result(Decimal("1.000000000000000001"), [{"date": date(2026, 8, 1)}]))
    assert compare(report(first), report(first))["passed"]
    changed = dict(first, total="1.000000000000000002")
    assert not compare(report(first), report(changed))["passed"]
    assert digest(first) != digest(changed)
    assert not compare(report(first), report(dict(first, daily=[])))["passed"]


def test_historical_attempt_fails_even_when_result_matches():
    call = dict(category="historical", status=None)
    assert not compare(report({}), report({}, [call]))["passed"]
    assert compare(report({}), report({}, [dict(category="head"), dict(category="boundary")]))["passed"]


def test_transport_counts_retry_attempts_without_exposing_url_or_token():
    from settle.extract import hypersync
    calls = []
    def send(self, request, **kwargs):
        calls.append(1)
        response = requests.Response()
        response.status_code = 429 if len(calls) == 1 else 200
        response._content = b'{"archive_height":1000}'
        return response
    with patch.object(requests.Session, "send", send), patch.object(hypersync.time, "sleep"):
        with ProviderAudit() as audit:
            hypersync._execute("ethereum", {"from_block": 2}, {"Authorization": "secret"}, requests.post)
    assert len(audit.calls) == 2
    assert all(c["category"] == "historical" for c in audit.calls)
    assert "secret" not in str(audit.calls)
    assert "http" not in str(audit.calls)


def test_swallowed_dune_attempt_is_still_recorded():
    from settle.extract import dune
    with ProviderAudit() as audit:
        with pytest.raises(RuntimeError, match="Dune is forbidden"):
            dune.execute_query("unused")
    assert audit.dune_attempts == 1


def test_comparison_rejects_mismatched_prime_or_cutoff():
    first = report({})
    assert not compare(first, dict(first, prime="grove"))["passed"]
    assert not compare(first, dict(first, cutoff="2026-08-02"))["passed"]


def test_reference_refresh_is_allowed_but_a_changed_snapshot_fails_comparison():
    first = dict(report({}), reference_snapshot='one')
    second = dict(first, calls=[{'category': 'reference_refresh'}])
    assert compare(first, second)['passed']
    assert not compare(first, dict(second, reference_snapshot='two'))['passed']


def test_only_explicit_official_reference_refresh_is_exempt():
    from settle.revenue import verification
    def send(self, request, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = b'{}'
        return response
    with patch.object(requests.Session, 'send', send), ProviderAudit() as audit:
        requests.get('https://markets.newyorkfed.org/api/rates/secured/sofr/search.json')
        token = verification._refreshing_rates.set(True)
        try:
            requests.get('https://markets.newyorkfed.org/api/rates/secured/sofr/search.json?startDate=2026-09-01')
            requests.post('https://fixture.invalid', json={'method': 'eth_call'})
        finally:
            verification._refreshing_rates.reset(token)
    assert [c['category'] for c in audit.calls] == ['historical', 'reference_refresh', 'historical']


def test_fleet_baseline_precedes_any_next_day_measurement(monkeypatch, tmp_path):
    from settle.revenue import verification as v
    from datetime import timedelta
    cutoff = date.today() - timedelta(days=3)
    calls = []
    def worker(prime, day, output):
        calls.append((prime, day))
        return dict(report({}), prime=prime, cutoff=str(day))
    monkeypatch.setattr(v, 'worker', worker)
    monkeypatch.setattr(v, 'summarize', lambda r: {'cutoff': r['cutoff']})
    monkeypatch.setattr(v, 'PRIMES', ('grove', 'spark'))
    monkeypatch.setenv('DATABASE_URL', 'unused')
    monkeypatch.setattr('sys.argv', ['verify', '--as-of', str(cutoff), '--advance', '--output', str(tmp_path)])
    v.main()
    assert calls == [('grove', cutoff), ('spark', cutoff), ('grove', cutoff), ('spark', cutoff),
                     ('grove', cutoff + timedelta(days=1)), ('spark', cutoff + timedelta(days=1))]
