"""Unit tests for `settle.normalize.prices`."""

from __future__ import annotations

from decimal import Decimal

import pytest

from settle.domain import Address, Chain, NavOracle, PricingCategory, Token, Venue
from settle.normalize.prices import (
    UnsupportedPricingError,
    get_unit_price,
    is_par_stable,
    par_stable_price,
)

from ..fixtures.mock_sources import MockConvertToAssetsSource, MockNavOracleSource


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


# --- Category E (RWA NAV) — Phase 2.A.3 -------------------------------------

def _rwa_venue(nav_oracle: NavOracle | None = None) -> Venue:
    return Venue(
        id="E9",
        chain=Chain.ETHEREUM,
        token=_token("JTRSY", 6),
        pricing_category=PricingCategory.RWA_TRANCHE,
        nav_oracle=nav_oracle,
    )


def test_cat_e_uses_primary_oracle_when_it_succeeds():
    venue = _rwa_venue(NavOracle(kind="chronicle", address=_addr("aa")))
    src = MockNavOracleSource(nav=Decimal("1.103456"))
    price = get_unit_price(
        venue, block=24971074,
        nav_oracle_resolver=lambda kind: src,
    )
    assert price == Decimal("1.103456")
    assert len(src.calls) == 1


def test_cat_e_falls_back_when_primary_raises():
    """Primary (chronicle) reverts → caller-allowlist; fallback (redstone) succeeds."""
    from settle.extract.oracles.chronicle import ChronicleReadError
    venue = _rwa_venue(NavOracle(
        kind="chronicle", address=_addr("aa"),
        fallback="redstone", fallback_address=_addr("bb"),
    ))
    # Typed oracle failure (matches what live ChronicleNavSource raises on
    # allowlist-revert / empty-data return). The fallback logic in
    # ``_resolve_rwa_nav`` only catches known oracle-failure types; bugs
    # like ``RuntimeError`` from a programming error propagate so they
    # don't masquerade as an oracle revert.
    primary = MockNavOracleSource(nav=ChronicleReadError)
    fallback = MockNavOracleSource(nav=Decimal("1.0312"))

    def _resolver(kind):
        return primary if kind == "chronicle" else fallback

    price = get_unit_price(venue, block=24971074, nav_oracle_resolver=_resolver)
    assert price == Decimal("1.0312")
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


def test_cat_e_raises_when_all_candidates_fail():
    """No silent $1 fall-through — caller wants a clear failure."""
    venue = _rwa_venue(NavOracle(
        kind="chronicle", address=_addr("aa"),
        fallback="redstone", fallback_address=_addr("bb"),
    ))
    from settle.extract.oracles.chronicle import ChronicleReadError
    failing = MockNavOracleSource(nav=ChronicleReadError)
    with pytest.raises(UnsupportedPricingError, match="All NAV oracles failed"):
        get_unit_price(venue, block=0, nav_oracle_resolver=lambda _: failing)


def test_cat_e_const_one_kind():
    """BUIDL-I config: nav_oracle.kind = 'const_one' returns $1.00 with no I/O."""
    from settle.normalize.sources.oracles import ConstOneNavSource
    venue = _rwa_venue(NavOracle(kind="const_one"))
    price = get_unit_price(
        venue, block=0,
        nav_oracle_resolver=lambda _: ConstOneNavSource(),
    )
    assert price == Decimal("1.00")


def test_cat_e_requires_nav_oracle_config():
    venue = _rwa_venue(nav_oracle=None)
    with pytest.raises(ValueError, match="no nav_oracle config"):
        get_unit_price(venue, block=0)


# --- Category F.curve_stableswap — Phase 2.A.4 ------------------------------

def _curve_venue(pool_addr: str = "ee") -> Venue:
    return Venue(
        id="E11",
        chain=Chain.ETHEREUM,
        token=Token(Chain.ETHEREUM, _addr(pool_addr), "AUSDUSDC-CRV", 18),
        pricing_category=PricingCategory.LP_POOL,
        lp_kind="curve_stableswap",
    )


def test_curve_lp_unit_price_with_par_stable_coins():
    """Pool: 12.46M USDC + 12.55M AUSD, totalSupply 25M LP. Both coins are
    par stables → unit price = (12.46 + 12.55) / 25 ≈ $1.0004 per LP."""
    from settle.normalize.sources.curve_pool import CurvePoolState

    USDC = bytes.fromhex("a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    AUSD = bytes.fromhex("00000000efe302beaa2b3e6e1b18d08d69a9012a")

    class _MockPool:
        def __init__(self): self.calls = []
        def read_pool(self, chain, pool, block):
            self.calls.append((chain, pool, block))
            return CurvePoolState(
                virtual_price_raw=10**18,
                total_supply=25_000_000 * 10**18,
                coins=[Address(USDC), Address(AUSD)],
                balances=[12_460_000 * 10**6, 12_540_000 * 10**6],   # 6 decimals each
            )

    venue = _curve_venue()
    src = _MockPool()
    price = get_unit_price(venue, block=24971074, curve_pool_source=src)
    expected = Decimal("25000000") / Decimal("25000000")  # = $1.00 in this rounded case
    assert price == expected
    assert len(src.calls) == 1


def test_curve_lp_unit_price_above_one_when_pool_holds_yield():
    """If the pool reserves exceed total supply (in $ terms), unit price > $1.
    Mimics sUSDS-bearing pools; here we just inflate USDC reserves."""
    from settle.normalize.sources.curve_pool import CurvePoolState

    USDC = bytes.fromhex("a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    AUSD = bytes.fromhex("00000000efe302beaa2b3e6e1b18d08d69a9012a")

    class _MockPool:
        def read_pool(self, chain, pool, block):
            # 25.5M of $-equivalent reserves backing 25M LP → price $1.02
            return CurvePoolState(
                virtual_price_raw=10**18,
                total_supply=25_000_000 * 10**18,
                coins=[Address(USDC), Address(AUSD)],
                balances=[13_000_000 * 10**6, 12_500_000 * 10**6],
            )

    price = get_unit_price(_curve_venue(), block=0, curve_pool_source=_MockPool())
    assert price == Decimal("25500000") / Decimal("25000000") == Decimal("1.02")


def test_curve_lp_raises_when_coin_in_neither_registry():
    """A pool with a coin missing from BOTH the par-stable and yield-bearing
    4626 registries must raise — recursive pricing of unknown LP underlyings
    is Phase 2.B+. (sUSDS itself is now in the yield-bearing registry and
    prices via convertToAssets — see the dedicated sUSDS test.)"""
    from settle.normalize.sources.curve_pool import CurvePoolState

    USDC = bytes.fromhex("a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    UNKNOWN = bytes.fromhex("0123456789abcdef0123456789abcdef01234567")  # not registered

    class _MockPool:
        def read_pool(self, chain, pool, block):
            return CurvePoolState(
                virtual_price_raw=10**18, total_supply=10**18,
                coins=[Address(UNKNOWN), Address(USDC)],
                balances=[5_000_000 * 10**18, 5_000_000 * 10**6],
            )

    with pytest.raises(UnsupportedPricingError, match="par-stable or yield-bearing"):
        get_unit_price(_curve_venue(), block=0, curve_pool_source=_MockPool())


def test_curve_lp_zero_supply_returns_zero():
    """Empty pool — unit price defined as 0, not divide-by-zero."""
    from settle.normalize.sources.curve_pool import CurvePoolState

    USDC = bytes.fromhex("a0b86991c6218b36c1d19d4a2e9eb0ce3606eb48")
    AUSD = bytes.fromhex("00000000efe302beaa2b3e6e1b18d08d69a9012a")

    class _MockPool:
        def read_pool(self, chain, pool, block):
            return CurvePoolState(
                virtual_price_raw=10**18, total_supply=0,
                coins=[Address(USDC), Address(AUSD)],
                balances=[0, 0],
            )

    assert get_unit_price(_curve_venue(), block=0, curve_pool_source=_MockPool()) == Decimal("0")


# --- Category F.uniswap_v3 — Phase 2.A.5 -----------------------------------

def test_uniswap_v3_unit_price_not_defined():
    """V3 positions are non-fungible — there's no meaningful unit price.
    Callers must use ``get_position_value`` which has a dedicated NFT-enum branch."""
    venue = Venue(
        id="E12", chain=Chain.ETHEREUM,
        token=_token("AUSDUSDC-UNI3", 0),
        pricing_category=PricingCategory.LP_POOL,
        lp_kind="uniswap_v3",
    )
    with pytest.raises(UnsupportedPricingError, match="get_position_value"):
        get_unit_price(venue, block=0)


def test_curve_lp_unknown_lp_kind_raises():
    venue = Venue(
        id="EX", chain=Chain.ETHEREUM,
        token=_token("WHATEVER", 18),
        pricing_category=PricingCategory.LP_POOL,
        lp_kind="balancer",        # not yet implemented
    )
    with pytest.raises(UnsupportedPricingError, match="lp_kind"):
        get_unit_price(venue, block=0)


# --- Categories G/H: not implemented in Phase 2 -----------------------------

@pytest.mark.parametrize("cat", [
    PricingCategory.NATIVE_GAS,
    PricingCategory.GOVERNANCE,
])
def test_unit_price_phase_two_categories_raise(cat: PricingCategory):
    venue = _venue(cat, _token("WHATEVER", 18))
    with pytest.raises(UnsupportedPricingError):
        get_unit_price(venue, block=0)
