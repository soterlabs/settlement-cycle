from types import SimpleNamespace

import pytest

from settle.extract import hypersync


def response(status, headers=None):
    return SimpleNamespace(status_code=status, ok=status == 200, text="limited",
                           headers=headers or {}, json=lambda: {"next_block": 42})


def test_rate_limit_obeys_provider_reset_and_replays_same_request(monkeypatch):
    sleeps, calls = [], []
    replies = iter([response(429, {"x-ratelimit-reset": "11"}), response(200)])
    monkeypatch.setattr(hypersync.time, "sleep", sleeps.append)
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return next(replies)
    assert hypersync._execute("base", {"from_block": 12}, {}, post) == {"next_block": 42}
    assert sleeps == [11]
    assert calls[0] == calls[1]


@pytest.mark.parametrize("status,headers,expected_sleeps", [
    (429, {}, [15, 30, 60]), (429, {"Retry-After": "120"}, []), (401, {}, []),
])
def test_retries_are_bounded_and_auth_errors_fail_immediately(monkeypatch, status, headers, expected_sleeps):
    sleeps = []
    monkeypatch.setattr(hypersync.time, "sleep", sleeps.append)
    with pytest.raises(hypersync.HyperSyncError, match=f"HTTP {status}"):
        hypersync._execute("base", {}, {}, lambda *a, **kw: response(status, headers))
    assert sleeps == expected_sleeps
