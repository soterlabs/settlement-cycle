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
    # Every headline row carries an explicit ``(gross)`` / ``(net)``
    # suffix so a reader doesn't have to guess what flow each value
    # represents. ``(gross)`` = a one-way flow (yield earned, fee paid,
    # claim made) before any offset. ``(net)`` = after the row's
    # relevant offsets; the meaning is per-row:
    #   * ``sky_revenue (net)``         = BR claim net of intra-Sky
    #                                      spread reimbursements
    #                                      (sUSDS / Curve / PSM3)
    #   * ``prime_agent_revenue (net)`` = the prime's P&L — gross
    #                                      total revenue minus what
    #                                      Sky claims back as
    #                                      ``sky_revenue (net)``
    #                                      (= the legacy ``monthly_pnl``
    #                                      field in provenance.json)
    # Sky's net P&L isn't a row because the right framing varies by
    # counterparty (SDE-heavy primes have sd_revenue redirected to Sky
    # that's already inside ``sky_revenue (net)``). The legend below
    # documents the formula.
    headline_rows = [
        ("prime_agent_revenue (gross)",        r.get("prime_agent_revenue")),
        ("agent_rate (gross)",                 r.get("agent_rate")),
        ("distribution_rewards (gross)",       r.get("distribution_rewards")),
        ("prime_agent_total_revenue (gross)",  r.get("prime_agent_total_revenue")),
        ("sky_revenue (net)",                  r.get("sky_revenue")),
        ("prime_agent_revenue (net)",          r.get("monthly_pnl")),
        ("sde_revenue (gross)",                r.get("sde_revenue")),
        ("susds_spread_reimbursement (gross)", r.get("susds_spread_reimbursement")),
        ("pol_agent_rate (gross)",             r.get("pol_agent_rate")),
        ("curve_susds_spread (gross)",         r.get("curve_susds_spread")),
        ("psm3_susds_spread (gross)",          r.get("psm3_susds_spread")),
        ("sky_revenue (gross, pre-spread-credit)", r.get("sky_revenue_gross")),
    ]
    lines.append("## Headline")
    lines.append("")
    lines.append(
        "Suffix legend: ``(gross)`` = one-way ledger entry, ``(net)`` = "
        "after the row's relevant offsets. Two rows carry ``(net)``: "
        "``sky_revenue (net)`` is the BR claim net of sUSDS / Curve / "
        "PSM3 spread reimbursements (intra-Sky credits); "
        "``prime_agent_revenue (net)`` = ``prime_agent_total_revenue (gross)`` "
        "− ``sky_revenue (net)`` (i.e. the legacy ``monthly_pnl``). "
        "**For non-SDE primes (e.g. OBEX) this equals the prime's profit.** "
        "**For SDE-heavy primes (e.g. Grove) ``sky_revenue (net)`` already "
        "contains ``sde_revenue (gross)``** — that revenue was redirected "
        "out of ``prime_agent_revenue (gross)`` and 100% to Sky, so "
        "subtracting it once via ``sky_revenue (net)`` is correct; but "
        "interpreting ``prime_agent_revenue (net)`` as the prime's true "
        "profit overstates Sky's claim by ``sde_revenue (gross)``. For "
        "those primes the prime's true profit is "
        "``prime_agent_revenue (net) + sde_revenue (gross)``. Sky's net "
        "P&L (not a row) = ``sky_revenue (net)`` − ``agent_rate (gross)`` "
        "− ``distribution_rewards (gross)`` (``pol_agent_rate`` and the "
        "sUSDS / Curve / PSM3 spread reimbursements are NOT subtracted "
        "again because they're already inside ``sky_revenue (net)``)."
    )
    lines.append("")
    lines.append("| Field | USD |")
    lines.append("|---|---:|")
    for label, val in headline_rows:
        # Skip zero rows for non-headline fields so the table stays tight.
        # Always show the five primary fields even when zero.
        if label not in (
            "prime_agent_revenue (gross)", "agent_rate (gross)",
            "prime_agent_total_revenue (gross)", "sky_revenue (net)",
            "prime_agent_revenue (net)",
        ) and _D(val) == 0:
            continue
        lines.append(f"| {label} | {_usd(val)} |")
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
        lines.append("## Position-only venues (PnL aggregated at prime level)")
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
        # Aggregate note — keeps the visible Per-venue ``revenue`` column
        # reconcilable against the ``prime_agent_revenue`` headline above.
        # Without this, a reader summing the Per-venue table would find a
        # gap equal to the suppressed VSR liability with no in-document
        # explanation.
        agg_actual = sum(
            (_D(v.get("actual_revenue")) for v in pnl_hidden), Decimal("0"),
        )
        lines.append("")
        lines.append(
            f"> Aggregated `actual_revenue` from the venues above "
            f"(included in `prime_agent_revenue` at the prime level, not "
            f"shown per-vault): **{_usd(agg_actual)}**. Per-vault values "
            f"remain in `provenance.json` under `venue_breakdown[]`."
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
