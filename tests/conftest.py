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


@pytest.fixture
def nominal_rate_convention(monkeypatch):
    """Pin the rate convention to NOMINAL (APR) regardless of the test's dates.

    ``_helpers.APR_CONVENTION_START`` gates ``compose_rate`` / ``daily_slice``
    so that re-running a month settled before 2026-08 reproduces what was
    published (see the cutover note in ``_helpers``). Tests that assert the
    nominal mechanics themselves generally use an arbitrary period — March
    2026 and friends — chosen long before the cutover existed. Rather than
    restate their dates and hand-recomputed expectations, they declare the
    regime they mean:

        pytestmark = pytest.mark.usefixtures("nominal_rate_convention")

    The pre-cutover branch is covered separately, by
    ``test_rate_convention.py`` and by re-running the settled months.
    """
    from datetime import date as _date

    from settle.compute import _helpers

    monkeypatch.setattr(_helpers, "APR_CONVENTION_START", _date(1970, 1, 1))
