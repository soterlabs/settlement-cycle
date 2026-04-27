"""Tests for the source registry — config-driven dispatch."""

from __future__ import annotations

import pytest

from settle.normalize import registry
from settle.normalize.registry import UnknownSourceError


def test_default_debt_source_is_dune():
    src = registry.get_debt_source()
    assert type(src).__name__ == "DuneDebtSource"


def test_default_balance_source_is_dune():
    src = registry.get_balance_source()
    assert type(src).__name__ == "DuneBalanceSource"


def test_default_ssr_source_is_dune():
    src = registry.get_ssr_source()
    assert type(src).__name__ == "DuneSSRSource"


def test_get_debt_source_unknown_raises():
    with pytest.raises(UnknownSourceError, match="subgraph"):
        registry.get_debt_source("subgraph")


def test_get_balance_source_unknown_raises():
    with pytest.raises(UnknownSourceError):
        registry.get_balance_source("nope")


def test_get_ssr_source_unknown_raises():
    with pytest.raises(UnknownSourceError):
        registry.get_ssr_source("nope")
