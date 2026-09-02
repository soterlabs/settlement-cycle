"""``summary.md`` must surface the time-weighted average venue value.

Cost of funds is allocated on that figure
(``cof_alloc = avg_value x weight / Sigma(avg x weight) x CoF_total``), and
before this it appeared only in the xlsx — so a reader reconciling a venue's
CoF share from the markdown report had no way to see it. SoM and EoM alone
can be badly misleading: Grove E41 went $1M -> $12.5M in August but averaged
$3.37M.
"""
from __future__ import annotations

from settle.load.summary import _tw_avg, render_summary


def test_renders_the_value_when_present():
    assert _tw_avg({"tw_avg_value_usd": "3369516.77"}) == "$3,369,516.77"


def test_renders_an_em_dash_when_absent():
    """Legacy provenance has no tw_avg. The SoM/EoM midpoint is a DIFFERENT
    quantity — printing it in this column would read as a computed average
    (E41's midpoint is $6.75M against a true $3.37M)."""
    assert _tw_avg({}) == "—"
    assert _tw_avg({"tw_avg_value_usd": None}) == "—"
    assert _tw_avg({"tw_avg_value_usd": ""}) == "—"


def test_column_is_in_the_header_and_rows():
    prov = {
        "prime_id": "grove",
        "month": "2026-08",
        "period": {"start": "2026-08-01", "end": "2026-08-31", "n_days": 31},
        "results": {
            "prime_agent_revenue": "0", "agent_rate": "0",
            "distribution_rewards": "0", "chronicle_points": "0", "gar": "0",
            "sky_revenue": "0", "monthly_pnl": "0",
        },
        "venue_breakdown": [
            {"venue_id": "E41", "label": "JTRSY Basin escrow",
             "value_som": "1000000", "value_eom": "12500000",
             "tw_avg_value_usd": "3369516.77", "period_inflow": "11500000",
             "actual_revenue": "0", "revenue": "0", "sd_revenue": "0"},
            {"venue_id": "E99", "label": "legacy, no tw",
             "value_som": "5", "value_eom": "5", "period_inflow": "0",
             "actual_revenue": "0", "revenue": "0", "sd_revenue": "0"},
        ],
    }
    md = render_summary(prov)
    lines = md.splitlines()
    hi = next(i for i, ln in enumerate(lines) if ln.startswith("| Venue |"))
    header = lines[hi]
    assert "tw_avg_value" in header
    # header column count must match the alignment row and every data row —
    # a mismatch renders the table as literal text in every markdown viewer
    n = header.count("|")
    assert lines[hi + 1].count("|") == n, "alignment row lost a column"
    e41 = next(ln for ln in lines if ln.startswith("| E41 |"))
    assert e41.count("|") == n
    assert "$3,369,516.77" in e41
    e99 = next(ln for ln in lines if ln.startswith("| E99 |"))
    assert "—" in e99
