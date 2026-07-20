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


# --- production runners must not pin migratable fields -----------------------
#
# Regression guard for the silent no-op the review surfaced: a runner that
# pre-set ``position_balance`` (or any migratable field) to a concrete source
# made ``position_balance: hypersync`` in a prime YAML a no-op, because
# ``_sources_from_prime`` fills only ``None`` fields. Every runner's
# ``_live_sources()`` must therefore leave these fields ``None`` and let
# ``compute_monthly_pnl`` default them per call site.

import importlib.util
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"

# Fields that have (or will have) a per-prime backend pilot; a runner pinning
# any of these here silently disables the YAML override for it.
_MIGRATABLE = ("debt", "balance", "ssr", "position_balance",
               "convert_to_assets", "block_resolver", "psm3")


def _load_runner(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "runner", ["run_live_2026", "run_obex_2026", "_run_agent_rate_prime"]
)
def test_runner_live_sources_leaves_migratable_fields_none(runner):
    src = _load_runner(runner)._live_sources()
    pinned = [f for f in _MIGRATABLE if getattr(src, f) is not None]
    assert not pinned, (
        f"{runner}._live_sources() pins {pinned}; leave them None so a prime's "
        f"YAML `sources:` override applies (see _sources_from_prime)."
    )
