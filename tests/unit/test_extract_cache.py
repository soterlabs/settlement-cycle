"""Unit tests for the on-disk Extract cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from settle.extract.cache import cache_dir, cached


def test_cache_dir_uses_env(tmp_cache_dir: Path):
    assert cache_dir() == tmp_cache_dir


def test_cached_memoizes(tmp_cache_dir: Path):
    n_calls = {"value": 0}

    @cached(source_id="test")
    def expensive(x: int) -> int:
        n_calls["value"] += 1
        return x * 2

    assert expensive(5) == 10
    assert expensive(5) == 10
    assert n_calls["value"] == 1, "second call should hit the cache"

    assert expensive(6) == 12
    assert n_calls["value"] == 2, "different args should miss the cache"


def test_cached_disabled_via_env(tmp_cache_dir: Path, monkeypatch: pytest.MonkeyPatch):
    n_calls = {"value": 0}

    @cached(source_id="test")
    def expensive(x: int) -> int:
        n_calls["value"] += 1
        return x * 2

    monkeypatch.setenv("SETTLE_NO_CACHE", "1")
    assert expensive(5) == 10
    assert expensive(5) == 10
    assert n_calls["value"] == 2, "cache should be bypassed when SETTLE_NO_CACHE=1"


def test_cached_handles_kwargs(tmp_cache_dir: Path):
    n_calls = {"value": 0}

    @cached(source_id="test")
    def fn(a: int, *, b: int = 0) -> int:
        n_calls["value"] += 1
        return a + b

    assert fn(1, b=2) == 3
    assert fn(1, b=2) == 3
    assert n_calls["value"] == 1
    assert fn(1, b=3) == 4
    assert n_calls["value"] == 2


def test_cached_handles_complex_args(tmp_cache_dir: Path):
    """bytes / Path / dicts must all be hashable in the cache key."""
    from datetime import date

    n_calls = {"value": 0}

    @cached(source_id="test")
    def fn(blob: bytes, path: Path, when: date, params: dict) -> int:
        n_calls["value"] += 1
        return len(blob)

    blob = b"\x01\x02\x03"
    p = Path("/tmp/x.sql")
    d = date(2026, 4, 27)
    params = {"a": 1, "b": 2}

    assert fn(blob, p, d, params) == 3
    assert fn(blob, p, d, params) == 3
    assert n_calls["value"] == 1
