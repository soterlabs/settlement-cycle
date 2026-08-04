"""Unit tests for `settle.domain`."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from settle.domain import Address, Chain, Month, Period, PricingCategory, Prime, Token, Venue
from settle.domain.config import load_prime


# ----------------------------------------------------------------------------
# Address
# ----------------------------------------------------------------------------

def test_address_from_str_normalizes_case():
    a = Address.from_str("0xb6dD7ae22C9922AFEe0642f9Ac13e58633f715A2")
    assert a.hex == "0xb6dd7ae22c9922afee0642f9ac13e58633f715a2"
    assert len(a.value) == 20


def test_address_from_str_accepts_no_0x_prefix():
    a = Address.from_str("b6dd7ae22c9922afee0642f9ac13e58633f715a2")
    assert a.hex == "0xb6dd7ae22c9922afee0642f9ac13e58633f715a2"


def test_address_rejects_wrong_length():
    with pytest.raises(ValueError):
        Address.from_str("0xb6dd7ae22c9922afee")


def test_address_rejects_wrong_byte_length():
    with pytest.raises(ValueError):
        Address(b"\x00" * 19)


# ----------------------------------------------------------------------------
# Month / Period
# ----------------------------------------------------------------------------

def test_month_parse_yyyymm():
    m = Month.parse("2026-04")
    assert (m.year, m.month) == (2026, 4)
    assert str(m) == "2026-04"


def test_month_parse_yyyymmdd_drops_day():
    m = Month.parse("2026-04-15")
    assert (m.year, m.month) == (2026, 4)


def test_month_first_last_day():
    m = Month(2026, 4)
    assert m.first_day == date(2026, 4, 1)
    assert m.last_day == date(2026, 4, 30)


def test_month_december_last_day():
    m = Month(2025, 12)
    assert m.last_day == date(2025, 12, 31)


def test_period_from_month_n_days():
    p = Period.from_month(Month(2026, 4))
    assert p.n_days == 30
    assert p.start == date(2026, 4, 1)
    assert p.end == date(2026, 4, 30)


def test_period_rejects_inverted_window():
    with pytest.raises(ValueError):
        Period(date(2026, 4, 30), date(2026, 4, 1))


def test_period_end_eod_utc():
    p = Period(date(2026, 4, 1), date(2026, 4, 30))
    eod = p.end_eod_utc
    assert eod.year == 2026 and eod.month == 4 and eod.day == 30
    assert eod.hour == 23 and eod.minute == 59 and eod.second == 59


# ----------------------------------------------------------------------------
# Pricing categories
# ----------------------------------------------------------------------------

def test_pricing_category_values():
    assert PricingCategory.PAR_STABLE == "A"
    assert PricingCategory.ERC4626_VAULT == "B"
    assert PricingCategory("C") == PricingCategory.AAVE_ATOKEN


# ----------------------------------------------------------------------------
# Prime + Venue
# ----------------------------------------------------------------------------

def _sample_prime() -> Prime:
    chain = Chain.ETHEREUM
    addr = Address.from_str("0x" + "11" * 20)
    token = Token(chain, addr, "FOO", 18)
    venue = Venue(id="V1", chain=chain, token=token, pricing_category=PricingCategory.PAR_STABLE)
    return Prime(
        id="test",
        ilk_bytes32=b"\x00" * 32,
        start_date=date(2025, 1, 1),
        subproxy={chain: addr},
        alm={chain: addr},
        venues=[venue],
    )


def test_prime_chains_property():
    p = _sample_prime()
    assert p.chains == {Chain.ETHEREUM}


def test_prime_rejects_wrong_ilk_length():
    with pytest.raises(ValueError):
        Prime(
            id="bad",
            ilk_bytes32=b"\x00" * 31,
            start_date=date(2025, 1, 1),
        )


# ----------------------------------------------------------------------------
# YAML loader
# ----------------------------------------------------------------------------

def test_load_prime_obex(config_dir: Path):
    obex = load_prime(config_dir / "obex.yaml")
    assert obex.id == "obex"
    assert obex.start_date == date(2025, 11, 17)
    # ALLOCATOR-OBEX-A in ASCII followed by 15 zero bytes
    assert obex.ilk_bytes32.startswith(b"ALLOCATOR-OBEX-A")
    assert Chain.ETHEREUM in obex.alm
    assert obex.alm[Chain.ETHEREUM].hex == "0xb6dd7ae22c9922afee0642f9ac13e58633f715a2"
    assert len(obex.venues) == 1
    v = obex.venues[0]
    assert v.id == "V1"
    assert v.pricing_category == PricingCategory.ERC4626_VAULT
    assert v.token.symbol == "syrupUSDC"
    assert v.underlying is not None and v.underlying.symbol == "USDC"


def test_load_prime_grove(config_dir: Path):
    """Grove config: 12 original venues (E1–E12) + 9 idle/multi-chain holdings
    (E13–E18, E26, E27, E25) + 6 alt-holder venues (E30–E35) + E36 EOA
    + E37 Maple syrupUSDC + E38 Agora incentives = 36 total. (E25 is
    display_only but still loads as a Cat B venue.)"""
    grove = load_prime(config_dir / "grove.yaml")
    assert grove.id == "grove"
    assert grove.start_date == date(2025, 5, 14)
    assert grove.ilk_bytes32.startswith(b"ALLOCATOR-BLOOM-A")
    assert grove.alm[Chain.ETHEREUM].hex == "0x491edfb0b8b608044e227225c715981a30f3a44e"
    assert grove.subproxy[Chain.ETHEREUM].hex == "0x1369f7b2b38c76b6478c0f0e66d94923421891ba"

    # Category breakdown.
    by_cat = {c: [v for v in grove.venues if v.pricing_category.value == c]
              for c in ["A", "B", "C", "D", "E", "F", "EOA"]}
    assert len(grove.venues) == 38
    assert len(by_cat["C"]) == 3, "E1+E2+E3 Aave aTokens"
    # E25 (grove-bbqAUSD on Monad) joins Cat B as of 2026-05-14.
    # E37 (Maple syrupUSDC) joins Cat B as of 2026-06-08.
    assert len(by_cat["B"]) == 9, "E4+E5+E6 Morpho 4626 + E18 sUSDS + E19 Base + E23 steakUSDC Base + E24 bbqPYUSD-V2 + E25 Monad bbqAUSD + E37 Maple syrupUSDC"
    assert len(by_cat["E"]) == 7, "E7-E10 ETH RWA + E20 JAAA-avax + E21 GACLO-1 + E22 ACRDX-plume"
    assert len(by_cat["F"]) == 4, "E11 Curve LP + E12 Uni V3 + E30 Uni V3 alt-holder (ETH) + E33 Uni V3 alt-holder (Monad)"
    # E38 (Agora AUSD incentives — cash distribution) joins Cat A as of 2026-06-08.
    # E40/E41 (Diamond PAU ALM idle + JTRSY Basin escrow) join Cat A as of 2026-08-03.
    assert len(by_cat["A"]) == 14, "E13 RLUSD + E14 AUSD + E15 USDC + E16 DAI + E17 USDS + E26 PYUSD + E27 USDC-Base + E31/E32 alt-holder ETH + E34/E35 alt-holder Monad + E38 Agora incentives + E40/E41 Diamond PAU"
    assert len(by_cat["EOA"]) == 1, "E36 OOB principal via 0xd94f → Monad ALM EOA"

    # Multi-chain: E19 is on Base; E20/E21 on Avalanche.
    e19 = next(v for v in grove.venues if v.id == "E19")
    assert e19.chain == Chain.BASE
    assert grove.alm[Chain.BASE].hex == "0x9b746dbc5269e1df6e4193bcb441c0fbbf1cecee"

    e20 = next(v for v in grove.venues if v.id == "E20")
    assert e20.chain == Chain.AVALANCHE_C
    # JAAA-avax NAV is read cross-chain from the Ethereum Centrifuge
    # pricePerShareFeed (same address as E8 JAAA_ETH). Switched 2026-05-14
    # from Chronicle — see grove.yaml E20 comment for the rationale and
    # the verified Feb 2026 boundary-block match within $200.
    assert e20.nav_oracle.oracle_chain == Chain.ETHEREUM
    assert e20.nav_oracle.kind == "price_per_share_feed"
    assert e20.nav_oracle.address.hex == "0x4880799ee5200fc58da299e965df644fbf46780b"

    e21 = next(v for v in grove.venues if v.id == "E21")
    assert e21.chain == Chain.AVALANCHE_C
    assert e21.nav_oracle.kind == "const_one"
    assert grove.alm[Chain.AVALANCHE_C].hex == "0x7107dd8f56642327945294a18a4280c78e153644"

    e22 = next(v for v in grove.venues if v.id == "E22")
    assert e22.chain == Chain.PLUME
    assert e22.nav_oracle.kind == "chronicle"
    assert e22.nav_oracle.oracle_chain == Chain.ETHEREUM
    assert grove.alm[Chain.PLUME].hex == "0x1db91ad50446a671e2231f77e00948e68876f812"


def test_load_prime_grove_nav_oracles(config_dir: Path):
    grove = load_prime(config_dir / "grove.yaml")
    by_id = {v.id: v for v in grove.venues}

    # JTRSY → Centrifuge pricePerShareFeed primary + Chronicle fallback.
    jtrsy = by_id["E9"]
    assert jtrsy.nav_oracle is not None
    assert jtrsy.nav_oracle.kind == "price_per_share_feed"
    assert jtrsy.nav_oracle.address.hex == "0xfe6920eb6c421f1179ca8c8d4170530cdbdfd77a"
    assert jtrsy.nav_oracle.fallback == "chronicle"
    assert jtrsy.nav_oracle.fallback_address.hex == "0x59ef4be3eddf0270c4878b7b945bbee13fb33d0d"

    # STAC → Chronicle primary + Redstone fallback. Redstone publishes the
    # same Securitize NAV via a Chainlink-AggregatorV3 adapter and has been
    # active continuously since early Dec 2025 (before any settlement SoM),
    # agreeing with Chronicle within ~4 bps at recent blocks. Replaces the
    # prior const_1000 static placeholder so pre-deployment reads get the
    # live NAV at the pin block instead of a fixed issue-time estimate.
    stac = by_id["E7"]
    assert stac.nav_oracle.kind == "chronicle"
    assert stac.nav_oracle.fallback == "redstone"
    assert stac.nav_oracle.fallback_address is not None
    assert stac.nav_oracle.fallback_address.hex == \
        "0xedc6287d3d41b322af600317628d7e226dd3add4"

    # BUIDL-I → const_one (yield via rewards, NAV pinned at $1).
    buidl = by_id["E10"]
    assert buidl.nav_oracle.kind == "const_one"
    assert buidl.nav_oracle.address is None


def test_load_prime_grove_lp_fields(config_dir: Path):
    grove = load_prime(config_dir / "grove.yaml")
    by_id = {v.id: v for v in grove.venues}

    # Curve LP — lp_kind set, no NFT manager.
    curve = by_id["E11"]
    assert curve.lp_kind == "curve_stableswap"
    assert curve.nft_position_manager is None

    # Uni V3 — lp_kind set + NFT position manager address present.
    uni = by_id["E12"]
    assert uni.lp_kind == "uniswap_v3"
    assert uni.nft_position_manager is not None
    assert uni.nft_position_manager.hex == "0xc36442b4a4522e871399cd717abdd847ab11fe88"


# ----------------------------------------------------------------------------
# MonthlyPnL — total-revenue property
# ----------------------------------------------------------------------------

def test_prime_agent_total_revenue_sums_components():
    """prime_agent_total_revenue = prime_agent_revenue + agent_rate +
    distribution_rewards. The headline number — what the prime is owed."""
    from decimal import Decimal as _D
    from settle.domain.monthly_pnl import MonthlyPnL

    pnl = MonthlyPnL(
        prime_id="grove", month=Month(2026, 3),
        period=Period(date(2026, 3, 1), date(2026, 3, 31), pin_blocks={Chain.ETHEREUM: 1}),
        sky_revenue=_D("100"),
        agent_rate=_D("5"),
        prime_agent_revenue=_D("50"),
        # 50 + 5 - 100 = -45
        monthly_pnl=_D("-45"),
        venue_breakdown=[],
        pin_blocks_som={Chain.ETHEREUM: 0},
        # default distribution_rewards = 0
    )
    assert pnl.prime_agent_total_revenue == _D("55")    # 50 + 5 + 0


def test_prime_agent_total_revenue_includes_distribution_rewards():
    """Once distribution_rewards is populated (Phase 3+), it flows in."""
    from decimal import Decimal as _D
    from settle.domain.monthly_pnl import MonthlyPnL

    pnl = MonthlyPnL(
        prime_id="skybase", month=Month(2026, 3),
        period=Period(date(2026, 3, 1), date(2026, 3, 31), pin_blocks={Chain.ETHEREUM: 1}),
        sky_revenue=_D("0"), agent_rate=_D("100"),
        prime_agent_revenue=_D("0"),
        # 0 + 100 + 250 - 0 = 350 (invariant now includes distribution_rewards)
        monthly_pnl=_D("350"),
        venue_breakdown=[], pin_blocks_som={Chain.ETHEREUM: 0},
        distribution_rewards=_D("250"),
    )
    assert pnl.prime_agent_total_revenue == _D("350")


def test_load_prime_agent_rate_only(config_dir: Path):
    """Keel / Skybase: no ilk, no alm, no venues — agent rate only.

    ``ilk_bytes32`` is absent from the YAML → ``None`` on the Prime;
    the debt machinery then returns a zero series instead of querying."""
    for prime_id, sub in (
        ("keel",    "0x355cd90ecb1b409fdf8b64c4473c3b858da2c310"),
        ("skybase", "0x08978e3700859e476201c1d7438b3427e3c81140"),
    ):
        prime = load_prime(config_dir / f"{prime_id}.yaml")
        assert prime.id == prime_id
        assert prime.ilk_bytes32 is None
        assert prime.venues == []
        assert prime.alm == {}
        assert prime.subproxy[Chain.ETHEREUM].hex == sub
        assert prime.chains == {Chain.ETHEREUM}


def test_prime_rejects_venues_without_ilk():
    """Supply-side venues need ilk debt for the BR charge — an agent-rate-
    only prime must not silently carry venues."""
    from settle.domain.primes import Token, Venue
    venue = Venue(
        id="V1", chain=Chain.ETHEREUM, label="x",
        pricing_category=PricingCategory.ERC4626_VAULT,
        token=Token(address=Address.from_str("0x" + "11" * 20), symbol="T", decimals=18, chain=Chain.ETHEREUM),
    )
    with pytest.raises(ValueError, match="without an ilk_bytes32"):
        Prime(
            id="bad", ilk_bytes32=None, start_date=date(2025, 1, 1),
            venues=[venue],
        )
