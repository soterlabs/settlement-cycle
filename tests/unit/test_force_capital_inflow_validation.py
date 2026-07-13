"""Unit tests for the ``force_capital_inflow`` flag validation on ``Venue``.

The flag synthesises ``inflow_ts`` so that period revenue collapses to 0 —
appropriate ONLY for par-stable Cat A venues on chains without reliable
transfer-event coverage (e.g. Monad). Setting it on Cat B/C/D/E/F venues
would silently zero out yield that legitimately accrues via NAV/price/
share appreciation.

``Venue.__post_init__`` rejects the misuse at config-load time so a YAML
typo can't ship revenue-losing config.
"""

from __future__ import annotations

from datetime import date

import pytest

from settle.domain import Address, Chain, PricingCategory, Token, Venue


_CHAIN = Chain.ETHEREUM
_ADDR = Address.from_str("0x" + "11" * 20)
_TOKEN = Token(_CHAIN, _ADDR, "TEST", 18)


def _venue(
    pricing_category: PricingCategory,
    *,
    force_capital_inflow: bool = False,
    vid: str = "V1",
) -> Venue:
    return Venue(
        id=vid,
        chain=_CHAIN,
        token=_TOKEN,
        pricing_category=pricing_category,
        force_capital_inflow=force_capital_inflow,
    )


def test_par_stable_accepts_force_capital_inflow():
    """The flag's intended target — Cat A par-stable venues. Should construct
    cleanly and round-trip the field."""
    v = _venue(PricingCategory.PAR_STABLE, force_capital_inflow=True)
    assert v.force_capital_inflow is True
    assert v.pricing_category == PricingCategory.PAR_STABLE


def test_par_stable_default_no_flag():
    """Default constructed Cat A venue has flag = False (opt-in)."""
    v = _venue(PricingCategory.PAR_STABLE)
    assert v.force_capital_inflow is False


@pytest.mark.parametrize(
    "category",
    [
        PricingCategory.ERC4626_VAULT,
        PricingCategory.AAVE_ATOKEN,
        PricingCategory.SPARKLEND_SPTOKEN,
        PricingCategory.RWA_TRANCHE,
        PricingCategory.LP_POOL,
        PricingCategory.EOA,
    ],
)
def test_non_par_stable_rejects_force_capital_inflow(category: PricingCategory):
    """Setting the flag on a category whose value moves via NAV/price/share
    appreciation must raise — otherwise we'd silently zero legitimate yield."""
    with pytest.raises(ValueError, match="force_capital_inflow"):
        _venue(category, force_capital_inflow=True)


def test_error_message_names_offending_venue_and_category():
    """Validation message should pinpoint both the venue id and the
    offending category so a YAML typo is debuggable from the traceback
    alone."""
    with pytest.raises(ValueError) as exc:
        _venue(PricingCategory.ERC4626_VAULT, force_capital_inflow=True, vid="E99")
    msg = str(exc.value)
    assert "E99" in msg
    assert "ERC4626_VAULT" in msg
    assert "PAR_STABLE" in msg


# ── external_yield_source validation (same fail-at-load philosophy) ──


def _venue_eys(
    pricing_category: PricingCategory,
    *,
    external_yield_source: bool = False,
    force_capital_inflow: bool = False,
    vid: str = "V1",
) -> Venue:
    return Venue(
        id=vid,
        chain=_CHAIN,
        token=_TOKEN,
        pricing_category=pricing_category,
        external_yield_source=external_yield_source,
        force_capital_inflow=force_capital_inflow,
    )


def test_par_stable_accepts_external_yield_source():
    v = _venue_eys(PricingCategory.PAR_STABLE, external_yield_source=True)
    assert v.external_yield_source is True


def test_non_par_stable_rejects_external_yield_source():
    """The flag routes into the Cat A classifier; on Cat B/C/D/E venues it is
    meaningless and a YAML typo must not silently no-op."""
    with pytest.raises(ValueError, match="external_yield_source"):
        _venue_eys(PricingCategory.ERC4626_VAULT, external_yield_source=True, vid="E98")


def test_external_yield_source_and_force_capital_inflow_conflict():
    """force wins the compute short-circuit, silently zeroing the yield the
    other flag promises to classify — reject the contradiction at load."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        _venue_eys(
            PricingCategory.PAR_STABLE,
            external_yield_source=True,
            force_capital_inflow=True,
        )


def test_prime_rejects_flagged_venue_without_external_sources():
    """external_yield_source with an empty external_alm_sources allowlist for
    the venue's chain nets every inflow to capital — real yield silently
    becomes $0. Must fail at config load, not at settlement time."""
    from settle.domain import Prime

    v = _venue_eys(PricingCategory.PAR_STABLE, external_yield_source=True, vid="S98")
    with pytest.raises(ValueError, match="external_alm_sources"):
        Prime(
            id="testprime",
            ilk_bytes32=b"\x00" * 32,
            start_date=date(2026, 1, 1),
            alm={_CHAIN: _ADDR},
            venues=[v],
        )


def test_prime_accepts_flagged_venue_with_external_sources():
    from settle.domain import Prime

    v = _venue_eys(PricingCategory.PAR_STABLE, external_yield_source=True, vid="S98")
    p = Prime(
        id="testprime",
        ilk_bytes32=b"\x00" * 32,
        start_date=date(2026, 1, 1),
        alm={_CHAIN: _ADDR},
        venues=[v],
        external_alm_sources={_CHAIN: [Address.from_str("0x" + "22" * 20)]},
    )
    assert p.venues[0].external_yield_source is True
