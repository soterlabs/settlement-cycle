"""Unit tests for `settle.normalize.positions`."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from settle.domain import Chain, Month, Period, PricingCategory
from settle.domain.config import load_prime
from settle.normalize.positions import get_position_balance, get_position_value

from ..fixtures.mock_sources import MockConvertToAssetsSource, MockPositionBalanceSource


def _obex(config_dir: Path):
    return load_prime(config_dir / "obex.yaml")


def _eth_pin(block: int = 24971074) -> Period:
    return Period.from_month(Month(2026, 4), pin_blocks={Chain.ETHEREUM: block})


# --- get_position_balance ---------------------------------------------------

def test_position_balance_calls_source_with_alm_holder(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]                                  # syrupUSDC, decimals=6
    src = MockPositionBalanceSource(raw_balance=525_698_254_197_051)

    bal = get_position_balance(obex, venue, block=24971074, source=src)

    assert bal == Decimal("525698254.197051")               # raw / 10^6
    chain, token, holder, block = src.calls[0]
    assert chain == "ethereum"
    assert token == venue.token.address.value
    assert holder == obex.alm[Chain.ETHEREUM].value
    assert block == 24971074


def test_position_balance_zero_when_source_returns_zero(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]
    bal = get_position_balance(obex, venue, block=0, source=MockPositionBalanceSource(raw_balance=0))
    assert bal == Decimal("0")


def test_position_balance_rejects_chain_without_alm(config_dir: Path):
    obex = _obex(config_dir)
    venue = obex.venues[0]
    obex_no_alm = type(obex)(
        id=obex.id, ilk_bytes32=obex.ilk_bytes32, start_date=obex.start_date,
        subproxy=obex.subproxy, alm={}, venues=obex.venues,
    )
    with pytest.raises(ValueError, match="no ALM"):
        get_position_balance(obex_no_alm, venue, block=0, source=MockPositionBalanceSource())


# --- get_position_value -----------------------------------------------------

def test_position_value_for_obex_syrup_usdc(config_dir: Path):
    """OBEX V1: 525_698_254 syrupUSDC × ($1.07 USD/share) ≈ $562.5M.

    Numbers chosen to mirror live MCP/RPC observations from the POC. Math:
    raw_balance = 525_698_254_197_051   (6 share-decimals)
    convertToAssets(1e6) = 1_070_000    (USDC = 6 decimals; pps = 1.07)
    """
    obex = _obex(config_dir)
    venue = obex.venues[0]                                  # cat B
    bal_src = MockPositionBalanceSource(raw_balance=525_698_254_197_051)
    price_src = MockConvertToAssetsSource(raw_assets=1_070_000)

    value = get_position_value(
        obex, venue, block=24971074,
        balance_source=bal_src, erc4626_source=price_src,
    )
    expected = Decimal("525698254.197051") * Decimal("1.07")
    assert value == expected
    # Sanity bound: somewhere between $560M and $565M.
    assert Decimal("560_000_000") < value < Decimal("565_000_000")


def test_position_value_par_stable_uses_const_one(config_dir: Path):
    """For category A, no convertToAssets call should happen — price is $1 const."""
    obex = _obex(config_dir)
    base_venue = obex.venues[0]
    par_venue = type(base_venue)(
        id="V_par",
        chain=Chain.ETHEREUM,
        token=base_venue.underlying,                        # USDC
        pricing_category=PricingCategory.PAR_STABLE,
        underlying=None,
    )
    bal_src = MockPositionBalanceSource(raw_balance=10_000_000)  # 10 USDC raw
    price_src = MockConvertToAssetsSource(raw_assets=999_999_999)  # should NOT be called

    value = get_position_value(
        obex, par_venue, block=0,
        balance_source=bal_src, erc4626_source=price_src,
    )
    assert value == Decimal("10")
    assert price_src.calls == [], "convertToAssets must not be called for par stables"
