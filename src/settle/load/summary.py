"""Render a deterministic ``summary.md`` from a parsed ``provenance.json``.

The summary is the **PR-review surface** for monthly settlements. The xlsx
remains the canonical artifact (shared with the prime/Sky), and
``provenance.json`` remains the machine-readable source of truth. The
summary is a small (~5 KB), text-only, deterministic markdown that:

* Diffs cleanly in the GitHub UI (markdown tables render natively in
  the diff view).
* Skips all volatile fields (timestamps, source manifest, pipeline
  version) — only commits when actual numbers change.
* Lists venues in a stable order (by ``venue_id`` ascending,
  alphanumeric).
* Uses fixed-decimal formatting so a one-cent shift produces a one-line
  diff, not a re-layout.

Structure:
    # {PRIME} — {YYYY-MM}

    ## Headline
    ### Prime side
    #### Demand-Side revenue        (agent rate + distribution rewards)
    #### Supply-Side revenue        (venue P&L net of CoF, incl. SDE-venue residual)
    ### Sky side                    (prime CoF + SDE → supply-side revenue)

    ## Per-venue
    | Venue | Label | value_som | value_eom | tw_avg_value | period_inflow |
    | actual_rev | revenue | sd_revenue | sd_share | spread_reimb |
    ...

    ## Position-only venues (PnL aggregated at prime level)
                                                (only when hide_per_venue_pnl set)
    | Venue | Label | value_som | value_eom |
    ...
    > Aggregated actual_revenue from the venues above: $...

    ## Off-protocol holdings           (only when display_only_breakdown non-empty)
    | Venue | Label | value_som | value_eom |
    ...
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path


def _D(x) -> Decimal:
    if x is None or x == "":
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def _tw_avg(v: dict) -> str:
    """Time-weighted average venue value, as rendered in the per-venue table.

    This is the figure cost of funds is allocated on
    (``cof_attribution``: ``cof_alloc = avg_value x weight / Sigma(avg x
    weight) x CoF_total``), so a reader reconciling a venue's CoF share
    needs it visible rather than having to open the xlsx. SoM and EoM alone
    can be badly misleading for it: Grove E41 went $1M -> $12.5M in August
    but only crossed $8.98M on the 29th and $12.5M on the 31st itself, for
    a true average near $3.4M.

    Renders an em dash when ``tw_avg_value_usd`` is absent (legacy
    provenance) rather than substituting the SoM/EoM midpoint — the
    midpoint is a different quantity, and printing it in this column would
    read as a computed average.
    """
    raw = v.get("tw_avg_value_usd")
    if raw in (None, ""):
        return "—"
    return _usd(raw)


def _usd(x) -> str:
    """Fixed 2-decimal USD format with thousands separators. Negative values
    use a plain ASCII minus so the markdown diff stays stable across
    locales / editors. Sub-cent magnitudes collapse to $0.00 — float dust
    from flow netting would otherwise render as "-$0.00". Quantize-based so
    the exact ±0.005 tie (which banker's-rounds to 0.00 in the f-string)
    collapses too."""
    d = _D(x)
    if d.quantize(Decimal("0.01")) == 0:
        return "$0.00"
    if d < 0:
        return f"-${-d:,.2f}"
    return f"${d:,.2f}"


def _usds(x) -> str:
    """Same as ``_usd`` but with no leading currency sign — used in the
    headline tables where the column header carries the unit (``USDS``)."""
    d = _D(x)
    if d.quantize(Decimal("0.01")) == 0:
        return "0.00"
    if d < 0:
        return f"-{-d:,.2f}"
    return f"{d:,.2f}"


def _venue_sort_key(venue_id: str):
    """Stable order: numeric prefix first (E1, E2, ..., E10, ...), then
    alphabetic (SPREAD, PSM_CURVE_DEDUCT, etc.). Falls back to the raw
    string for ids that don't match the ``<letter><number>`` pattern."""
    if not venue_id:
        return (2, "")
    head = venue_id[0]
    rest = venue_id[1:]
    if head.isalpha() and rest.isdigit():
        return (0, head, int(rest))
    return (1, venue_id)


def render_summary(prov: dict) -> str:
    """Build the summary.md text from a parsed provenance.json dict."""
    lines: list[str] = []
    prime_id = prov.get("prime_id", "?")
    month    = prov.get("month", "?")
    period   = prov.get("period", {})
    n_days   = period.get("n_days", "?")

    lines.append(f"# {prime_id.upper()} — {month}")
    lines.append("")
    lines.append(
        f"Period: {period.get('start', '?')} → {period.get('end', '?')} "
        f"({n_days} days)"
    )
    lines.append("")

    # ── Headline ────────────────────────────────────────────────────
    r = prov.get("results", {})
    # Two-sided breakdown: prime-side P&L split into Demand-Side vs
    # Supply-Side revenue, and the Sky-side P&L.
    #
    # Definitions:
    #   demand-side revenue (prime) = agent_rate + distribution_rewards
    #                                 (income streams independent of the
    #                                 supply-side venue book).
    #   supply-side revenue (prime) = prime_agent_revenue − prime_cof
    #                                 — the venue book's P&L net of the
    #                                 BR cost of funds. Includes the
    #                                 prime's residual take on SDE venues
    #                                 (the (1 − sd_share) slice +
    #                                 external_revenue). Note: SDE is a
    #                                 SKY concept — the venue residual is
    #                                 ordinary prime venue revenue, so it
    #                                 is folded in here without a
    #                                 dedicated line.
    #
    #   prime cost of funds         = sky_revenue (net) − sde_revenue
    #                                 Net of all intra-Sky credits (sUSDS /
    #                                 Curve / PSM3 spread reimbursements)
    #                                 — i.e. what the prime actually pays
    #                                 as interest.
    #   sky direct exposure         = sde_revenue (the Sky-redirected
    #                                 portion of venue actual_revenue,
    #                                 summed across SDE venues).
    #   supply-side revenue (sky)   = prime_cof + sde → what Sky earns
    #                                 from this prime (the former
    #                                 ``sky revenue`` line).
    venues_for_split = [
        v for v in (prov.get("venue_breakdown") or [])
        if not v.get("hide_per_venue_pnl")
    ]
    sky_revenue_total = _D(r.get("sky_revenue"))
    sky_side_sde      = _D(r.get("sde_revenue"))
    prime_cof         = sky_revenue_total - sky_side_sde

    # ``supply-side revenue`` (prime) is the whole venue book net of the
    # FULL ``prime_cof`` (no per-venue CoF allocation — Sky charges BR on
    # the ilk-debt aggregate, not per-venue, so any allocation would be a
    # display choice without a defensible formula). For SDE-heavy primes
    # (Grove: E9 JTRSY + E10 BUIDL carry ~$1.5B of fixed-SDE principal
    # with sd_share=1) this line can be materially negative — the yield on
    # that principal goes to Sky while the funding cost stays with the
    # prime's book. That asymmetry is the economics, not a rendering bug.
    if not venues_for_split:
        # Empty venue_breakdown shouldn't happen in production — every
        # prime has at least one venue. Render zero rather than a
        # misleading ``-prime_cof``.
        supply_side_revenue = Decimal("0")
    else:
        # Derived from the prime-level total rather than summing venue
        # rows: ``prime_agent_revenue`` can exceed Σ vr.revenue by
        # prime-level additions that have no venue row (today:
        # ``psm3_susds_appreciation``, the Case-3a SSR booking on the
        # PSM3 sUSDS slice). Summing rows would silently drop those.
        supply_side_revenue = _D(r.get("prime_agent_revenue")) - prime_cof
    agent_rate     = _D(r.get("agent_rate"))
    dist_rewards   = _D(r.get("distribution_rewards"))
    # Chronicle Points (Grove only today) — Demand-Side component; the row
    # renders only when non-zero so other primes' summaries are unchanged.
    chron_points   = _D(r.get("chronicle_points"))
    # Governance Accessibility Rewards (Skybase only today) — Demand-Side
    # component (share × the month's Sky Net Revenue, paid at the MSC
    # executing the following month). Renders when non-zero.
    gar            = _D(r.get("gar"))
    dr_rows        = prov.get("dr_breakdown") or []
    demand_side_revenue = agent_rate + dist_rewards + chron_points + gar

    def _row(label: str, val) -> str:
        if isinstance(val, str):
            return f"| {label} | {val} |"
        return f"| {label} | {_usds(val)} |"

    lines.append("## Headline")
    lines.append("")
    lines.append("### Prime side")
    lines.append("")
    lines.append("#### Demand-Side revenue")
    lines.append("")
    lines.append("| Field | USDS |")
    lines.append("|---|---:|")
    lines.append(_row("agent rate", agent_rate))
    # Primes with no DR source (e.g. obex — no group in the settle-dr-dune
    # workbook) earn no DR by definition: render 0.00, not "TBD".
    lines.append(_row("distribution rewards", dist_rewards))
    if chron_points:
        lines.append(_row("chronicle points", chron_points))
    if gar:
        lines.append(_row("governance accessibility rewards", gar))
    lines.append(_row("**demand-side revenue**", f"**{_usds(demand_side_revenue)}**"))
    lines.append("")
    lines.append("#### Supply-Side revenue")
    lines.append("")
    lines.append("| Field | USDS |")
    lines.append("|---|---:|")
    lines.append(_row("**supply-side revenue**", f"**{_usds(supply_side_revenue)}**"))
    lines.append("")

    # Non-venue supply-side components. These are booked at the orchestrator
    # level and carry NO per-venue row, so they don't appear in the Per-venue
    # table below and were previously invisible in the published report (the
    # "non-venue layer" flagged in the 2026-07 Spark reconciliation, §8 item 2).
    # PSM3 is a basket contract (USDC + USDS + sUSDS legs), not a venue: its
    # sUSDS-leg SSR appreciation is credited straight into prime_agent_revenue
    # and its BR-spread reimbursement reduces cost of funds. The Curve sUSDS
    # spread is the same shape. The Cat B L2 sUSDS proxies' spread DOES show
    # per-venue (spread_reimb column) — surfaced here as a total for tie-out.
    psm3_appreciation = _D(r.get("psm3_susds_appreciation"))
    psm3_spread       = _D(r.get("psm3_susds_spread"))
    curve_spread      = _D(r.get("curve_susds_spread"))
    total_spread      = _D(r.get("susds_spread_reimbursement"))
    catb_l2_spread    = total_spread - psm3_spread - curve_spread
    # Gate on ANY component, not just the aggregate: an upstream inconsistency
    # that leaves ``total_spread`` 0 while a component spread is populated must
    # still render the section (else the very layer this surfaces stays hidden).
    if psm3_appreciation or total_spread or psm3_spread or curve_spread:
        lines.append("##### Non-venue sUSDS credits")
        lines.append("")
        lines.append("| Component | USDS | Effect |")
        lines.append("|---|---:|---|")
        if psm3_appreciation:
            lines.append(
                f"| PSM3 sUSDS SSR appreciation | {_usds(psm3_appreciation)} "
                f"| adds to prime revenue |"
            )
        if psm3_spread:
            lines.append(
                f"| PSM3 sUSDS BR-spread reimbursement | {_usds(psm3_spread)} "
                f"| reduces cost of funds |"
            )
        if curve_spread:
            lines.append(
                f"| Curve sUSDS BR-spread reimbursement | {_usds(curve_spread)} "
                f"| reduces cost of funds |"
            )
        # Only the POSITIVE residual is a real per-venue reimbursement. A
        # non-positive value is definitional drift (e.g. sky_only zeroes the
        # component spreads while retaining the aggregate, or partial fixtures)
        # — omit it rather than print a nonsensical negative "reimbursement".
        if catb_l2_spread > 0:
            lines.append(
                f"| Cat B L2 sUSDS BR-spread reimbursement | "
                f"{_usds(catb_l2_spread)} | reduces cost of funds (per-venue) |"
            )
        # The total row only makes sense when there IS a reimbursement; an
        # appreciation-only prime (total_spread == 0) would otherwise print a
        # spurious "0.00 (netted into cost of funds)".
        if total_spread:
            lines.append(
                f"| **total sUSDS spread reimbursement** | "
                f"**{_usds(total_spread)}** | (netted into cost of funds above) |"
            )
        lines.append("")

    lines.append("### Sky side")
    lines.append("")
    lines.append("| Field | USDS |")
    lines.append("|---|---:|")
    lines.append(_row("prime cost of funds", prime_cof))
    lines.append(_row("sky direct exposure", sky_side_sde))
    lines.append(_row("**supply-side revenue**", f"**{_usds(sky_revenue_total)}**"))
    lines.append("")

    # ── Per-venue ───────────────────────────────────────────────────
    # Venues with ``hide_per_venue_pnl`` are excluded from the PnL table
    # and rendered position-only in a dedicated sub-section below — see
    # the field docstring on ``Venue.hide_per_venue_pnl`` for rationale.
    all_venues = sorted(
        prov.get("venue_breakdown") or [],
        key=lambda v: _venue_sort_key(v.get("venue_id", "")),
    )
    venues       = [v for v in all_venues if not v.get("hide_per_venue_pnl")]
    pnl_hidden   = [v for v in all_venues if     v.get("hide_per_venue_pnl")]
    if venues:
        lines.append("## Per-venue")
        lines.append("")
        lines.append(
            "| Venue | Label | value_som | value_eom | tw_avg_value | "
            "period_inflow | actual_rev | revenue | sd_revenue | sd_share | "
            "spread_reimb |"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for v in venues:
            sd_share_d = _D(v.get("sd_share"))
            sd_share_str = f"{sd_share_d * 100:.2f}%" if sd_share_d != 0 else "0%"
            spread = _D(v.get("susds_spread_reimbursement"))
            lines.append(
                f"| {v.get('venue_id', '')} | "
                f"{v.get('label', '')} | "
                f"{_usd(v.get('value_som'))} | "
                f"{_usd(v.get('value_eom'))} | "
                f"{_tw_avg(v)} | "
                f"{_usd(v.get('period_inflow'))} | "
                f"{_usd(v.get('actual_revenue'))} | "
                f"{_usd(v.get('revenue'))} | "
                f"{_usd(v.get('sd_revenue'))} | "
                f"{sd_share_str} | "
                f"{_usd(spread)} |"
            )
        lines.append("")

    # ── PnL-suppressed venues (positions only) ──────────────────────
    if pnl_hidden:
        lines.append("## Position-only venues (excluded from `prime_agent_revenue`)")
        lines.append("")
        lines.append("| Venue | Label | value_som | value_eom |")
        lines.append("|---|---|---:|---:|")
        for v in pnl_hidden:
            lines.append(
                f"| {v.get('venue_id', '')} | "
                f"{v.get('label', '')} | "
                f"{_usd(v.get('value_som'))} | "
                f"{_usd(v.get('value_eom'))} |"
            )
        lines.append("")
        lines.append(
            f"> Position values above are **excluded from `prime_agent_revenue`** "
            f"per the Savings V2 scope decision (the depositor-side VSR liability "
            f"is outside the MSC/ALM accounting boundary — see "
            f"`docs/spark/PRD_savings_vaults.md` §3 and `QUESTIONS.md` S30). "
            f"Reconciliations against Spark / BA Labs dashboards, which net the "
            f"VSR, will differ by ≈ the period's VSR accrual. "
            f"Per-vault values remain in `provenance.json` under `venue_breakdown[]`."
        )
        lines.append("")

    # ── DR per ref code ─────────────────────────────────────────────
    # Distribution Rewards breakdown for the period, from the settle-dr-dune
    # reconciliation workbook (Summary tab). Sums to the "distribution
    # rewards" headline row above.
    # Zero-amount codes are omitted: a code that earned nothing this period
    # contributes nothing to read, and rendering it makes the report churn
    # every time the upstream workbook starts carrying a zero row for a
    # previously-absent code (spark 2026-06/07 gained a bare `223 | $0.00`
    # line on the settle-dr-dune 5bb3d4a bump, with no economic change).
    # ``dr_breakdown`` in provenance.json keeps every row, so the machine-
    # readable audit trail is unaffected — this is a display filter only.
    dr_rows_nonzero = [d for d in dr_rows if _D(d.get("amount")) != 0]
    if dr_rows_nonzero:
        lines.append("## DR per ref code")
        lines.append("")
        lines.append("| ref_code | DR (USD) | notes |")
        lines.append("|---|---:|---|")
        for d in dr_rows_nonzero:
            amt = _D(d.get("amount"))
            note = (d.get("notes") or "").replace("|", "／").replace("\n", " ").strip()
            lines.append(f"| {d.get('ref_code', '')} | {_usd(amt)} | {note} |")
        # Total = the authoritative group total (the workbook's Total row,
        # = the "distribution rewards" headline). Matches the headline exactly;
        # may differ from the visible row sum by sub-cent workbook rounding.
        lines.append(f"| **Total** | **{_usd(dist_rewards)}** | |")
        lines.append("")

    # ── Off-protocol (display-only) ─────────────────────────────────
    display_only = sorted(
        prov.get("display_only_breakdown") or [],
        key=lambda v: _venue_sort_key(v.get("venue_id", "")),
    )
    if display_only:
        lines.append("## Off-protocol holdings")
        lines.append("")
        lines.append("| Venue | Label | value_som | value_eom |")
        lines.append("|---|---|---:|---:|")
        for v in display_only:
            lines.append(
                f"| {v.get('venue_id', '')} | "
                f"{v.get('label', '')} | "
                f"{_usd(v.get('value_som'))} | "
                f"{_usd(v.get('value_eom'))} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


def write_summary(provenance_path: Path, dest: Path) -> Path:
    """Read ``provenance.json``, render the summary, write to ``dest``."""
    with provenance_path.open() as f:
        prov = json.load(f)
    dest.write_text(render_summary(prov), encoding="utf-8")
    return dest
