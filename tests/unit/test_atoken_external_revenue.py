"""Unit tests for ``_atoken_external_revenue_usd``.

Covers the guard paths only — the Dune integration itself is exercised
end-to-end when DUNE_API_KEY is set + a settlement cell is run. Here we
pin:

  1. Empty ``external_alm_sources`` → 0 (most venues today).
  2. No DUNE_API_KEY → 0 + warning (graceful degradation).
  3. Missing ``venue.underlying`` → raise (config bug, refuse to misprice).
  4. Non-par-stable underlying → raise (we don't have a price oracle path).

The "queries Dune, sums per-sender" happy path needs a Dune fixture
infrastructure we don't have for ad-hoc queries — it's covered by the
integration tests that run against the live API."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from settle.domain import (
    Address,
    Chain,
    Period,
    PricingCategory,
    Prime,
    Token,
    Venue,
)
from settle.normalize.positions import _atoken_external_revenue_usd
from settle.normalize.prices import UnsupportedPricingError


def _addr(seed: str) -> Address:
    return Address.from_str("0x" + seed * 20)


def _period() -> Period:
    return Period(
        start=date(2026, 2, 1),
        end=date(2026, 2, 28),
        pin_blocks={Chain.ETHEREUM: 24558867},
    )


def _grove_atoken_venue(*, with_underlying: bool = True, par_underlying: bool = True) -> Venue:
    """Cat C aToken venue mirroring E1 (aHorRwaRLUSD on RLUSD)."""
    token = Token(Chain.ETHEREUM, _addr("a1"), "aHorRwaRLUSD", 18)
    underlying = None
    if with_underlying:
        sym = "RLUSD" if par_underlying else "WETH"  # RLUSD is par, WETH isn't
        underlying = Token(Chain.ETHEREUM, _addr("b2"), sym, 18)
    return Venue(
        id="E1", chain=Chain.ETHEREUM, token=token,
        pricing_category=PricingCategory.AAVE_ATOKEN,
        underlying=underlying,
        label="Aave Horizon RWA RLUSD (aToken)",
    )


def _prime(external_sources: list[Address] | None) -> Prime:
    return Prime(
        id="grove",
        ilk_bytes32=b"\x00" * 32,
        start_date=date(2025, 5, 14),
        alm={Chain.ETHEREUM: _addr("c3")},
        external_alm_sources=(
            {Chain.ETHEREUM: external_sources} if external_sources else {}
        ),
    )


def test_returns_zero_when_no_external_sources_configured():
    """Default path for any prime/venue without an off-pool yield channel —
    no Dune call, just an early return. This is what almost every Cat C
    venue does today (Grove on Ethereum is the exception)."""
    prime = _prime(external_sources=None)
    venue = _grove_atoken_venue()
    result = _atoken_external_revenue_usd(prime, venue, _period())
    assert result == Decimal("0")


def test_returns_zero_with_warning_when_dune_api_key_unset(monkeypatch, caplog):
    """Graceful degradation: if the operator hasn't set DUNE_API_KEY (e.g.
    a CI without secrets), don't crash the settlement — log once and
    return 0 so the rest of the closed-form revenue still applies."""
    monkeypatch.delenv("DUNE_API_KEY", raising=False)
    import logging
    caplog.set_level(logging.WARNING)
    prime = _prime(external_sources=[_addr("d4")])
    venue = _grove_atoken_venue()
    assert _atoken_external_revenue_usd(prime, venue, _period()) == Decimal("0")
    # Pin the exact warning marker so a future refactor that loses the
    # warning fails the test (silent degradation is the failure mode we
    # specifically want to catch).
    assert any(
        "DUNE_API_KEY unset" in r.getMessage() and r.levelname == "WARNING"
        for r in caplog.records
    )


def test_raises_on_non_cat_c_venue(monkeypatch):
    """Category guard: only Cat C (Aave aToken) / D (SparkLend spToken)
    should reach this helper. Any other category passing through is a
    routing bug — the rebased-amount=USD shortcut only holds for aTokens."""
    monkeypatch.setenv("DUNE_API_KEY", "stub")
    prime = _prime(external_sources=[_addr("d4")])
    # Build a Cat A venue (par-stable raw holding) instead of an aToken.
    token = Token(Chain.ETHEREUM, _addr("a1"), "RLUSD", 18)
    venue = Venue(
        id="E13", chain=Chain.ETHEREUM, token=token,
        pricing_category=PricingCategory.PAR_STABLE,
        label="RLUSD raw (ALM idle)",
    )
    with pytest.raises(UnsupportedPricingError, match="not Cat C/D"):
        _atoken_external_revenue_usd(prime, venue, _period())


def test_raises_on_missing_underlying(monkeypatch):
    """A Cat C venue without ``venue.underlying`` configured can't be priced
    — we can't tell if the rebased aToken amount equals USD without
    knowing the underlying. Refuse loudly rather than guess."""
    monkeypatch.setenv("DUNE_API_KEY", "stub")
    prime = _prime(external_sources=[_addr("d4")])
    venue = _grove_atoken_venue(with_underlying=False)
    with pytest.raises(UnsupportedPricingError, match="no underlying"):
        _atoken_external_revenue_usd(prime, venue, _period())


def test_raises_on_non_par_stable_underlying(monkeypatch):
    """If the aToken's underlying isn't par-stable (e.g. WETH), the rebased
    aToken amount is in underlying-units, not USD. We don't have a price-
    oracle path for that yet — refuse to silently use a wrong scaling."""
    monkeypatch.setenv("DUNE_API_KEY", "stub")
    prime = _prime(external_sources=[_addr("d4")])
    venue = _grove_atoken_venue(par_underlying=False)
    with pytest.raises(UnsupportedPricingError, match="not par-stable"):
        _atoken_external_revenue_usd(prime, venue, _period())


# --- Sender dispatch (Merkl Claimed-event vs generic Transfer) ----------
#
# The helper dispatches per sender: addresses in ``_MERKL_DISTRIBUTORS``
# route to ``_merkl_claims_revenue_usd`` (Claimed event source); everything
# else routes to ``_atoken_transfer_revenue_usd`` (generic ERC20 Transfer
# source). The pure-dispatch behaviour is unit-testable by stubbing
# ``execute_query`` and asserting which SQL path the helper exercised.

def _merkl_eth() -> Address:
    """The address registered in ``_MERKL_DISTRIBUTORS[Chain.ETHEREUM]``."""
    return Address.from_str("0x3ef3d8ba38ebe18db133cec108f4d14ce00dd9ae")


def test_merkl_sender_uses_claimed_event_sql(monkeypatch):
    """A sender that matches ``_MERKL_DISTRIBUTORS[Chain.ETHEREUM]`` should
    cause the helper to fire the ``merkl_claims_*.sql`` query, NOT the
    generic ``atoken_external_inflow.sql``. We stub ``execute_query`` and
    record the SQL filename + params hit on each call — including a check
    that ``atoken`` is passed (used by the SQL's Mint-event JOIN to attribute
    the Claimed amount to this venue without configuring the staticAToken
    address)."""
    import pandas as pd
    monkeypatch.setenv("DUNE_API_KEY", "stub")
    prime = _prime(external_sources=[_merkl_eth()])
    venue = _grove_atoken_venue()

    hits: list[tuple[str, dict]] = []

    def _stub(sql_path, *, params, pin_block):
        hits.append((sql_path.name, params))
        # Return one row with a known raw uint256 — 1.5 aToken = $1.5 USD
        # for an 18-decimal token. (Just a sentinel; this test cares about
        # routing, not the value math.)
        return pd.DataFrame({"total_amount_raw": [int(Decimal("1.5") * 10**18)]})

    from settle.normalize import positions
    monkeypatch.setattr(positions, "execute_query", _stub)

    out = _atoken_external_revenue_usd(prime, venue, _period())
    assert [name for name, _ in hits] == ["merkl_claims_ethereum.sql"]
    # JOIN-attribution param contract: ``atoken`` carries the venue's
    # aToken address (raw 20-byte value), no padded-hex token field.
    _, params = hits[0]
    assert params["atoken"] == venue.token.address.value
    assert "token_padded_hex" not in params
    assert out == Decimal("1.5")


def test_non_merkl_sender_uses_transfer_sql(monkeypatch):
    """A sender NOT in ``_MERKL_DISTRIBUTORS`` should fall through to the
    generic Transfer-based helper — suitable for direct-sweep flows like
    Anchorage / BUIDL yield mints."""
    import pandas as pd
    monkeypatch.setenv("DUNE_API_KEY", "stub")
    # An arbitrary non-Merkl address (Anchorage-style direct-sweep stand-in).
    not_merkl = Address.from_str("0x" + "ab" * 20)
    prime = _prime(external_sources=[not_merkl])
    venue = _grove_atoken_venue()

    hits: list[str] = []

    def _stub(sql_path, *, params, pin_block):
        hits.append(sql_path.name)
        # Generic path returns a decimal-adjusted ``total_amount`` (NOT
        # raw uint256), per the existing ``atoken_external_inflow.sql``
        # output contract.
        return pd.DataFrame({"total_amount": [Decimal("42")]})

    from settle.normalize import positions
    monkeypatch.setattr(positions, "execute_query", _stub)

    out = _atoken_external_revenue_usd(prime, venue, _period())
    assert hits == ["atoken_external_inflow.sql"]
    assert out == Decimal("42")


def test_mixed_senders_run_both_paths(monkeypatch):
    """Two senders in the allowlist, one Merkl + one direct-sweep → the
    helper runs BOTH paths and sums the results. Sanity check for the
    per-sender dispatch loop."""
    import pandas as pd
    monkeypatch.setenv("DUNE_API_KEY", "stub")
    not_merkl = Address.from_str("0x" + "cd" * 20)
    prime = _prime(external_sources=[_merkl_eth(), not_merkl])
    venue = _grove_atoken_venue()

    hits: list[tuple[str, dict]] = []

    def _stub(sql_path, *, params, pin_block):
        hits.append((sql_path.name, params))
        if sql_path.name == "merkl_claims_ethereum.sql":
            return pd.DataFrame({"total_amount_raw": [10 * 10**18]})  # $10
        return pd.DataFrame({"total_amount": [Decimal("5")]})         # $5

    from settle.normalize import positions
    monkeypatch.setattr(positions, "execute_query", _stub)

    out = _atoken_external_revenue_usd(prime, venue, _period())
    assert {name for name, _ in hits} == {
        "merkl_claims_ethereum.sql", "atoken_external_inflow.sql",
    }
    # Merkl path receives the aToken address (used by the Mint-event JOIN);
    # the generic path receives ``token`` instead (legacy contract).
    merkl_params = next(p for n, p in hits if n == "merkl_claims_ethereum.sql")
    assert merkl_params["atoken"] == venue.token.address.value
    assert out == Decimal("15")
