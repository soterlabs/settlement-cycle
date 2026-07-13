"""Per-prime ``sources:`` config block — load-time validation + merge semantics.

The block flips the data backend behind counterparty-facing settlement numbers
(e.g. the OBEX HyperSync pilot), so:
  * a malformed block must fail at config LOAD, not silently no-op to Dune;
  * the override must apply on every entry point — also when a caller passes
    an explicit ``Sources`` with the field left ``None`` (production runners).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from settle.compute.monthly_pnl import Sources, _sources_from_prime
from settle.domain.config import _validate_sources
from settle.normalize.sources.hypersync_debt import HyperSyncDebtSource


# --- load-time validation ---------------------------------------------------

def test_validate_accepts_registered_backends():
    ok = {"debt": "hypersync", "balance": "hypersync", "ssr": "dune"}
    assert _validate_sources(dict(ok), "obex") == ok


def test_validate_rejects_unknown_key():
    with pytest.raises(ValueError, match="unknown sources key 'balances'"):
        _validate_sources({"balances": "hypersync"}, "obex")   # plural typo


def test_validate_rejects_unknown_backend():
    with pytest.raises(ValueError, match="unknown sources.debt backend 'hypersnc'"):
        _validate_sources({"debt": "hypersnc"}, "obex")        # value typo


def test_validate_empty_is_noop():
    assert _validate_sources({}, "obex") == {}


# --- merge semantics ---------------------------------------------------------

def _prime(sources: dict):
    return SimpleNamespace(sources=sources)


def test_override_fills_none_fields_of_explicit_sources():
    """Production runners pass an explicit Sources() with fields left None —
    the prime's YAML override must still land (the OBEX pilot previously
    settled on Dune from its own runner while the CLI used HyperSync)."""
    base = Sources()                                   # what runners now pass
    merged = _sources_from_prime(_prime({"debt": "hypersync"}), base)
    assert isinstance(merged.debt, HyperSyncDebtSource)
    assert merged.balance is None                      # untouched → registry default


def test_explicit_caller_field_wins_over_yaml():
    sentinel = object()
    base = Sources(debt=sentinel)                      # caller override (e.g. a test fake)
    merged = _sources_from_prime(_prime({"debt": "hypersync"}), base)
    assert merged.debt is sentinel


def test_no_override_returns_base_unchanged():
    base = Sources()
    assert _sources_from_prime(_prime({}), base) is base
    assert _sources_from_prime(SimpleNamespace(sources=None), None) is not None
