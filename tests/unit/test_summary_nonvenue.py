"""Unit tests for the non-venue supply-side breakdown in ``render_summary``.

The 2026-07 Spark reconciliation (§8 item 2) flagged a "non-venue layer"
in the published reports — orchestrator-level credits (PSM3 sUSDS SSR
appreciation + sUSDS 30bps spread reimbursements) that carry no per-venue
row and so were invisible. These tests pin the itemisation that surfaces
them.
"""

from __future__ import annotations

from settle.load.summary import render_summary


def _prov(**results):
    base = {
        "sky_revenue": "10681122.55",
        "sde_revenue": "40533.96",
        "prime_agent_revenue": "9000000.00",
        "agent_rate": "118578.18",
        "distribution_rewards": "1632540.80",
        "psm3_susds_appreciation": "0",
        "psm3_susds_spread": "0",
        "curve_susds_spread": "0",
        "susds_spread_reimbursement": "0",
    }
    base.update(results)
    return {
        "prime_id": "spark",
        "month": "2026-05",
        "period": {"start": "2026-05-01", "end": "2026-05-31", "n_days": 31},
        "results": base,
        "venue_breakdown": [{
            "venue_id": "S1", "label": "x", "value_som": "1", "value_eom": "1",
            "period_inflow": "0", "actual_revenue": "1", "revenue": "1",
            "sd_revenue": "0", "sd_share": "0", "susds_spread_reimbursement": "0",
        }],
    }


def test_nonvenue_section_itemises_psm3_and_spreads():
    out = render_summary(_prov(
        psm3_susds_appreciation="1069000.00",
        psm3_susds_spread="88000.00",
        curve_susds_spread="15000.00",
        susds_spread_reimbursement="184911.00",
    ))
    assert "##### Non-venue sUSDS credits" in out
    assert "PSM3 sUSDS SSR appreciation | 1,069,000.00" in out
    assert "PSM3 sUSDS BR-spread reimbursement | 88,000.00" in out
    assert "Curve sUSDS BR-spread reimbursement | 15,000.00" in out
    # Cat B L2 = total − psm3 − curve = 184,911 − 88,000 − 15,000 = 81,911.
    assert "Cat B L2 sUSDS BR-spread reimbursement | 81,911.00" in out
    assert "total sUSDS spread reimbursement** | **184,911.00**" in out


def test_nonvenue_section_omitted_when_no_susds_activity():
    """A prime with no PSM3/Curve/Cat-B sUSDS (e.g. Obex) must not render the
    section at all — no empty table, no zero rows."""
    out = render_summary(_prov())  # all sUSDS fields 0
    assert "Non-venue sUSDS" not in out


def test_nonvenue_section_appreciation_only():
    """Spread reimbursement can be 0 while appreciation is non-zero — the
    section still renders with just the appreciation row."""
    out = render_summary(_prov(psm3_susds_appreciation="500000.00"))
    assert "##### Non-venue sUSDS credits" in out
    assert "PSM3 sUSDS SSR appreciation | 500,000.00" in out
    assert "spread reimbursement | 88,000.00" not in out


def test_nonvenue_negative_catb_l2_row_omitted():
    """If total_spread < psm3+curve (definitional drift / sky_only zeroing),
    the derived Cat B L2 residual is negative and must NOT render as a
    nonsensical negative 'reimbursement' row."""
    out = render_summary(_prov(
        psm3_susds_spread="120000.00",
        curve_susds_spread="80000.00",
        susds_spread_reimbursement="150000.00",   # < 120k + 80k → catb = -50k
    ))
    assert "##### Non-venue sUSDS credits" in out
    assert "Cat B L2 sUSDS BR-spread reimbursement" not in out
    assert "-50,000" not in out and "-$50,000" not in out


def test_nonvenue_renders_when_total_zero_but_component_present():
    """Upstream inconsistency: aggregate susds_spread_reimbursement is 0 but a
    component spread is populated → the section must still render it (gate must
    not key on the aggregate alone)."""
    out = render_summary(_prov(
        psm3_susds_spread="88000.00",
        susds_spread_reimbursement="0",            # aggregate missing/zero
    ))
    assert "##### Non-venue sUSDS credits" in out
    assert "PSM3 sUSDS BR-spread reimbursement | 88,000.00" in out


def test_nonvenue_appreciation_only_has_no_total_row():
    """Appreciation-only prime (no spread) must NOT print a spurious
    'total sUSDS spread reimbursement | 0.00' row."""
    out = render_summary(_prov(psm3_susds_appreciation="500000.00"))
    assert "PSM3 sUSDS SSR appreciation | 500,000.00" in out
    assert "total sUSDS spread reimbursement" not in out
