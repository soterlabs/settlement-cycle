"""Shared pytest fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def tmp_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Override the on-disk cache to a per-test temp dir."""
    monkeypatch.setenv("SETTLE_CACHE_DIR", str(tmp_path))
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
    return repo_root / "queries"
