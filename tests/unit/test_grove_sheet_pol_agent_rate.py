"""xlsx CoF allocation must include the 20bps POL agent-rate add-back.

``sky_revenue`` is net of ``pol_agent_rate`` (subtracted in the
orchestrator), so the gross-BR allocation base recovered by
``compute_sheet_rows`` must add it back — otherwise the carrying venue's
−20bps never shows and the shortfall (Mar 2026 Spark: $163,247) smears
pro-rata across all venues' ``cof_alloc``. Σ profit_to_sky must still
equal the NET sky_revenue.
"""

from __future__ import annotations

from decimal import Decimal

from settle.load.grove_sheet import compute_sheet_rows


def _prov() -> dict:
    return {
        "period": {"start": "2026-03-01", "end": "2026-03-31", "n_days": "31"},
        "results": {
            "sky_revenue": "1000",            # net of pol_agent_rate
            "prime_agent_revenue": "500",
            "agent_rate": "0",
            "sky_revenue_gross": "1200",
            "susds_spread_reimbursement": "0",
            "pol_agent_rate": "100",
        },
        "venue_breakdown": [
            {"venue_id": "VX1", "label": "carrier", "value_som": "1000000",
             "value_eom": "1000000", "tw_avg_value_usd": "1000000",
             "actual_revenue": "0", "revenue": "0",
             "pol_agent_rate_usd": "100"},
            {"venue_id": "VX2", "label": "other", "value_som": "1000000",
             "value_eom": "1000000", "tw_avg_value_usd": "1000000",
             "actual_revenue": "0", "revenue": "0"},
        ],
    }


def test_cof_total_adds_back_pol_agent_rate():
    rows, totals = compute_sheet_rows(_prov(), "spark")
    # gross base = net sky (1000) + pol add-back (100).
    assert totals["cof_total"] == Decimal("1100")
    assert totals["pol_agent_rate_total"] == Decimal("100")


def test_carrying_venue_shows_minus_20bps_and_identity_holds():
    rows, totals = compute_sheet_rows(_prov(), "spark")
    by_id = {r["venue_id"]: r for r in rows}
    # Equal avg×weight → each venue gets 550 of the 1100 base; the carrier
    # then pays its −100 POL agent rate out of profit_to_sky.
    assert by_id["VX1"]["profit_to_sky"] == Decimal("450")
    assert by_id["VX2"]["profit_to_sky"] == Decimal("550")
    assert "POL agent rate" in by_id["VX1"]["note"]
    # Σ profit_to_sky ≡ NET sky_revenue (the exact-by-construction identity).
    assert totals["sum_p2s"] == totals["sky_revenue"]


def test_legacy_provenance_without_pol_field_unchanged():
    prov = _prov()
    del prov["results"]["pol_agent_rate"]
    del prov["venue_breakdown"][0]["pol_agent_rate_usd"]
    rows, totals = compute_sheet_rows(prov, "spark")
    assert totals["cof_total"] == Decimal("1000")
    assert totals["sum_p2s"] == totals["sky_revenue"]
