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
