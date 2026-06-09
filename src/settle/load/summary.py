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

    | Headline | USD |
    | prime_agent_revenue        | $... |
    | agent_rate                 | $... |
    | prime_agent_total_revenue  | $... |
    | sky_revenue (net)          | $... |
    | sde_revenue                | $... |
    | susds_spread_reimbursement | $... |
    | sky_revenue_gross          | $... |   (when non-zero)

    ## Per-venue
    | Venue | Label | value_som | value_eom | actual_rev | revenue | sd_revenue |
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


def _usd(x) -> str:
    """Fixed 2-decimal USD format with thousands separators. Negative values
    use a plain ASCII minus so the markdown diff stays stable across
    locales / editors."""
    d = _D(x)
    if d < 0:
        return f"-${-d:,.2f}"
    return f"${d:,.2f}"


def _usds(x) -> str:
    """Same as ``_usd`` but with no leading currency sign — used in the
    headline tables where the column header carries the unit (``USDS``)."""
    d = _D(x)
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
    # Two-sided breakdown: prime-side P&L vs Sky-side P&L. Each side
    # decomposes into a "regular" line plus a SDE (Sky Direct Exposure)
    # line, and the two sub-totals (``prime agent profit`` and
    # ``sky revenue``) sum the components above them.
    #
    # Definitions:
    #   prime agent net revenue   = Σ revenue across non-SDE venues
    #                               (sd_share == 0 in venue_breakdown).
    #                               Includes external_revenue (Merkl /
    #                               Agora / Anchorage sweeps) for those
    #                               venues.
    #   prime side SDE            = Σ revenue across SDE venues
    #                               (sd_share > 0). For fixed SDE
    #                               (sd_share = 1) this is just the
    #                               external_revenue portion; for capped
    #                               SDE it is the (1 − sd_share) ×
    #                               actual_revenue residual plus
    #                               external_revenue.
    #   prime agent profit        = agent_rate + distribution_rewards
    #                              + prime_agent_net_revenue
    #                              + prime_side_sde
    #                             ≡ agent_rate + distribution_rewards
    #                              + prime_agent_revenue
    #                              (= prime_agent_total_revenue)
    #
    #   prime cost of funds       = sky_revenue (net) − sde_revenue
    #                               Net of all intra-Sky credits (sUSDS /
    #                               Curve / PSM3 spread reimbursements
    #                               + pol_agent_rate) — i.e. what the
    #                               prime actually pays as interest.
    #   sky side SDE              = sde_revenue (the Sky-redirected
    #                               portion of venue actual_revenue,
    #                               summed across SDE venues).
    #   sky revenue               = prime_cof + sky_side_sde
    #                             ≡ sky_revenue (net) (already includes
    #                               sde_revenue minus intra-Sky credits).
    venues_for_split = [
        v for v in (prov.get("venue_breakdown") or [])
        if not v.get("hide_per_venue_pnl")
    ]
    sky_revenue_total = _D(r.get("sky_revenue"))
    sky_side_sde      = _D(r.get("sde_revenue"))
    prime_cof         = sky_revenue_total - sky_side_sde

    # ``prime agent net revenue`` is the prime's net take from non-SDE
    # venues AFTER paying the BR cost of funds. Gross venue yield minus
    # prime_cof, so the line reads as actual P&L rather than gross take.
    # The FULL ``prime_cof`` is charged against the non-SDE bucket (no
    # per-venue or per-bucket CoF allocation); for SDE-heavy primes
    # (Grove May 2026 is the limit case — E9 JTRSY + E10 BUIDL together
    # carry ~$1.5B of fixed-SDE principal with sd_share=1) this can push
    # ``prime_agent_net_revenue`` materially negative while
    # ``prime_side_sde`` stays small/zero. The ``prime agent profit``
    # total is still correct algebraically (it equals ``agent_rate +
    # distribution_rewards + prime_agent_revenue − prime_cof``), but the
    # decomposition is asymmetric and emphasises non-SDE economics. The
    # alternative (allocating CoF pro-rata across SDE / non-SDE based on
    # utilized USDS) is intentionally not done — Sky charges BR on the
    # ilk-debt aggregate, not per-venue, so any allocation would be a
    # display choice without a defensible formula. Operators looking at
    # an SDE-heavy prime's breakdown should focus on the bold totals
    # (``prime agent profit`` + ``sky revenue``); the intermediate lines
    # are informational.
    if not venues_for_split:
        # Empty venue_breakdown shouldn't happen in production — every
        # prime has at least one venue. Render zeros rather than a
        # misleading ``-prime_cof`` on the net-revenue line.
        non_sde_revenue_gross = Decimal("0")
        prime_side_sde        = Decimal("0")
        prime_agent_net_revenue = Decimal("0")
    else:
        non_sde_revenue_gross = sum(
            (_D(v.get("revenue")) for v in venues_for_split if _D(v.get("sd_share")) == 0),
            Decimal("0"),
        )
        prime_side_sde = sum(
            (_D(v.get("revenue")) for v in venues_for_split if _D(v.get("sd_share")) != 0),
            Decimal("0"),
        )
        prime_agent_net_revenue = non_sde_revenue_gross - prime_cof
    agent_rate     = _D(r.get("agent_rate"))
    dist_rewards   = _D(r.get("distribution_rewards"))
    prime_profit   = agent_rate + dist_rewards + prime_agent_net_revenue + prime_side_sde

    def _row(label: str, val) -> str:
        if isinstance(val, str):
            return f"| {label} | {val} |"
        return f"| {label} | {_usds(val)} |"

    lines.append("## Headline")
    lines.append("")
    lines.append("### Prime side")
    lines.append("")
    lines.append("| Field | USDS |")
    lines.append("|---|---:|")
    lines.append(_row("agent rate", agent_rate))
    lines.append(_row("distribution rewards", "TBD" if dist_rewards == 0 else _usds(dist_rewards)))
    lines.append(_row("prime agent net revenue", prime_agent_net_revenue))
    lines.append(_row("prime side sky direct exposure", prime_side_sde))
    lines.append(_row("**prime agent profit**", f"**{_usds(prime_profit)}**"))
    lines.append("")

    lines.append("### Sky side")
    lines.append("")
    lines.append("| Field | USDS |")
    lines.append("|---|---:|")
    lines.append(_row("prime cost of funds", prime_cof))
    lines.append(_row("sky side sky direct exposure", sky_side_sde))
    lines.append(_row("**sky revenue**", f"**{_usds(sky_revenue_total)}**"))
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
            "| Venue | Label | value_som | value_eom | period_inflow | "
            "actual_rev | revenue | sd_revenue | sd_share | spread_reimb |"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for v in venues:
            sd_share_d = _D(v.get("sd_share"))
            sd_share_str = f"{sd_share_d * 100:.2f}%" if sd_share_d != 0 else "0%"
            spread = _D(v.get("susds_spread_reimbursement"))
            lines.append(
                f"| {v.get('venue_id', '')} | "
                f"{v.get('label', '')} | "
                f"{_usd(v.get('value_som'))} | "
                f"{_usd(v.get('value_eom'))} | "
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
