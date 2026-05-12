"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Override the on-disk cache to a per-test temp dir.

    Also disables the Postgres cache layer so unit tests of the cache
    decorator don't pick up state from a real DB — they should be hermetic
    against the local pickle only. Integration tests that exercise the PG
    layer set their own ``DATABASE_URL`` explicitly."""
    monkeypatch.setenv("SETTLE_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from settle.extract import postgres_store
    postgres_store._reset_for_tests()
    return tmp_path


@pytest.fixture
def repo_root() -> Path:
    """Filesystem root of the settlement-cycle repo."""
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def config_dir(repo_root: Path) -> Path:
    return repo_root / "config"


@pytest.fixture
def queries_dir(repo_root: Path) -> Path:
    return repo_root / "src" / "settle" / "queries"
