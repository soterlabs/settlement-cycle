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
