"""Unit tests for `settle.normalize.prices`."""

from __future__ import annotations

from decimal import Decimal

import pytest

from settle.domain import Address, Chain, PricingCategory, Token, Venue
from settle.normalize.prices import (
    UnsupportedPricingError,
    get_unit_price,
    is_par_stable,
    par_stable_price,
)

from ..fixtures.mock_sources import MockConvertToAssetsSource


def _addr(seed: str) -> Address:
    return Address.from_str("0x" + seed.ljust(40, "0"))


def _token(symbol: str, decimals: int = 18) -> Token:
    return Token(Chain.ETHEREUM, _addr("aa"), symbol, decimals)


def _venue(category: PricingCategory, token: Token, underlying: Token | None = None) -> Venue:
    return Venue(
        id="V1",
        chain=Chain.ETHEREUM,
        token=token,
        pricing_category=category,
        underlying=underlying,
    )


# --- par-stable detection ---------------------------------------------------

def test_par_stable_detection():
    assert is_par_stable(_token("USDC", 6))
    assert is_par_stable(_token("USDS", 18))
    assert not is_par_stable(_token("WETH", 18))


def test_par_stable_price_returns_one():
    assert par_stable_price(_token("USDC", 6)) == Decimal("1.00")


def test_par_stable_price_rejects_unknown_token():
    with pytest.raises(ValueError, match="not in the par-stable whitelist"):
        par_stable_price(_token("WETH", 18))


# --- Category A: par stables ------------------------------------------------

def test_unit_price_par_stable():
    venue = _venue(PricingCategory.PAR_STABLE, _token("USDC", 6))
    assert get_unit_price(venue, block=24971074) == Decimal("1.00")


# --- Category B: ERC-4626 ---------------------------------------------------

def test_unit_price_erc4626_uses_convert_to_assets():
    """syrupUSDC has 6 share decimals + 6 underlying (USDC) decimals.
    If `convertToAssets(1e6) = 1_070_000` raw, price = 1.07 USDC × $1 = $1.07."""
    src = MockConvertToAssetsSource(raw_assets=1_070_000)
    syrup = _token("syrupUSDC", 6)
    usdc = _token("USDC", 6)
    venue = _venue(PricingCategory.ERC4626_VAULT, syrup, underlying=usdc)

    price = get_unit_price(venue, block=24971074, erc4626_source=src)
    assert price == Decimal("1.07")

    assert len(src.calls) == 1
    chain, vault, shares, block = src.calls[0]
    assert chain == "ethereum"
    assert shares == 10**6              # 1 whole share in raw units
    assert block == 24971074


def test_unit_price_erc4626_handles_18_decimals():
    """sUSDS: 18-dec shares, 18-dec USDS underlying. convertToAssets(1e18) = 1.094...e18."""
    src = MockConvertToAssetsSource(raw_assets=int(Decimal("1.094233094151478119") * 10**18))
    susds = _token("sUSDS", 18)
    usds = _token("USDS", 18)
    venue = _venue(PricingCategory.ERC4626_VAULT, susds, underlying=usds)

    price = get_unit_price(venue, block=24971074, erc4626_source=src)
    assert price == Decimal("1.094233094151478119")


def test_unit_price_erc4626_requires_underlying():
    venue = _venue(PricingCategory.ERC4626_VAULT, _token("syrupUSDC", 6))  # no underlying
    with pytest.raises(ValueError, match="underlying"):
        get_unit_price(venue, block=0, erc4626_source=MockConvertToAssetsSource())


# --- Category C/D: rebasing aTokens & SparkLend spTokens --------------------

def test_unit_price_aave_atoken_returns_underlying_price():
    aweth_underlying = _token("USDC", 6)
    aweth = _token("aEthUSDC", 6)
    venue = _venue(PricingCategory.AAVE_ATOKEN, aweth, underlying=aweth_underlying)
    assert get_unit_price(venue, block=0) == Decimal("1.00")


def test_unit_price_sparklend_returns_underlying_price():
    sp = _token("spUSDC", 6)
    underlying = _token("USDC", 6)
    venue = _venue(PricingCategory.SPARKLEND_SPTOKEN, sp, underlying=underlying)
    assert get_unit_price(venue, block=0) == Decimal("1.00")


def test_unit_price_aave_atoken_requires_underlying():
    venue = _venue(PricingCategory.AAVE_ATOKEN, _token("aFOO", 18))  # no underlying
    with pytest.raises(ValueError, match="underlying"):
        get_unit_price(venue, block=0)


# --- Categories E/F/G/H: not implemented in Phase 1 -------------------------

@pytest.mark.parametrize("cat", [
    PricingCategory.RWA_TRANCHE,
    PricingCategory.LP_POOL,
    PricingCategory.NATIVE_GAS,
    PricingCategory.GOVERNANCE,
])
def test_unit_price_phase_two_categories_raise(cat: PricingCategory):
    venue = _venue(cat, _token("WHATEVER", 18))
    with pytest.raises(UnsupportedPricingError):
        get_unit_price(venue, block=0)
