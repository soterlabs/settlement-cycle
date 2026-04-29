"""Render `MonthlyPnL` to Markdown."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .. import __version__
from ..domain.monthly_pnl import MonthlyPnL


def _fmt_usd(amount: Decimal) -> str:
    """``$1,234,567.89`` (or ``-$...``) — six trailing decimals collapsed to two."""
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}"


# Default link to RULES.md from the canonical settlement output path.
# `settlements/<prime>/<month>/pnl.md` → 3 levels up to repo root → docs/RULES.md.
# Callers writing to non-default destinations should override `rules_url` to keep
# the cross-link valid in their PR-review diff.
DEFAULT_RULES_URL: str = "../../../docs/RULES.md"


def render_markdown(pnl: MonthlyPnL, *, rules_url: str = DEFAULT_RULES_URL) -> str:
    """Render a settlement artifact as a single Markdown string.

    `rules_url` is the relative (or absolute) link inserted in the formula-reference
    footer. Default assumes artifacts land at `settlements/<prime>/<month>/pnl.md`
    relative to a checkout of this repo. Override when writing elsewhere.
    """
    lines: list[str] = []
    p = lines.append
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    p(f"# {pnl.prime_id.upper()} — Monthly settlement {pnl.month}")
    p("")
    p(f"- **Generated:** {now}")
    p(f"- **Pipeline:** `settle` v{__version__}")
    p(f"- **Period:** {pnl.period.start} → {pnl.period.end} ({pnl.period.n_days} days)")
    p("")

    # --- Headline ---
    p("## Headline")
    p("")
    p("| Component | Amount (USD) |")
    p("|---|---:|")
    p(f"| prime_agent_revenue           | {_fmt_usd(pnl.prime_agent_revenue)} |")
    p(f"| agent_rate                    | {_fmt_usd(pnl.agent_rate)} |")
    p(f"| distribution_rewards          | {_fmt_usd(pnl.distribution_rewards)} |")
    p(f"| **prime_agent_total_revenue** | **{_fmt_usd(pnl.prime_agent_total_revenue)}** |")
    p(f"| sky_revenue (net)             | {_fmt_usd(pnl.sky_revenue)} |")
    if pnl.sky_direct_shortfall != 0:
        p(f"| ↳ sky_direct_shortfall (absorbed) | {_fmt_usd(pnl.sky_direct_shortfall)} |")
    p("")
    p("`prime_agent_total_revenue = prime_agent_revenue + agent_rate + distribution_rewards`.")
    p("Settlement reports the prime's total revenue (positive cash to the prime) and `sky_revenue` (cost owed to Sky) separately — they're not netted at this layer.")
    p("")

    # --- Pin blocks ---
    p("## Pin blocks")
    p("")
    p("| Chain | SoM block | EoM block |")
    p("|---|---:|---:|")
    chains = sorted(set(pnl.pin_blocks_som) | set(pnl.period.pin_blocks),
                    key=lambda c: c.value)
    for chain in chains:
        som = pnl.pin_blocks_som.get(chain, "—")
        eom = pnl.period.pin_blocks.get(chain, "—")
        p(f"| {chain.value} | {som} | {eom} |")
    p("")

    # --- Per-venue ---
    if pnl.venue_breakdown:
        p("## Per-venue breakdown")
        p("")
        p("| Venue | Label | value_som | value_eom | period_inflow | revenue |")
        p("|---|---|---:|---:|---:|---:|")
        for v in pnl.venue_breakdown:
            p(
                f"| {v.venue_id} | {v.label} "
                f"| {_fmt_usd(v.value_som)} | {_fmt_usd(v.value_eom)} "
                f"| {_fmt_usd(v.period_inflow)} | {_fmt_usd(v.revenue)} |"
            )
        p("")

        # Sky Direct breakdown — only if any venue has a non-zero BR_charge.
        sd_venues = [v for v in pnl.venue_breakdown if v.br_charge != 0]
        if sd_venues:
            p("## Sky Direct (Step 4) breakdown")
            p("")
            p("Per the prime-settlement-methodology, Sky Direct venues book BR_charge to Sky and floor the prime's revenue at zero. When the venue underperforms BR, Sky absorbs the shortfall.")
            p("")
            p("| Venue | actual_revenue | BR_charge | sky_direct_shortfall | prime_keeps |")
            p("|---|---:|---:|---:|---:|")
            for v in sd_venues:
                p(
                    f"| {v.venue_id} {v.label} "
                    f"| {_fmt_usd(v.actual_revenue)} | {_fmt_usd(v.br_charge)} "
                    f"| {_fmt_usd(v.sky_direct_shortfall)} | {_fmt_usd(v.revenue)} |"
                )
            p("")

    # --- Definitions ---
    p("## Formula reference")
    p("")
    p("```")
    p("prime_agent_total_revenue = prime_agent_revenue + agent_rate + distribution_rewards")
    p("prime_agent_revenue = Σ_venues (value_eom − value_som − period_inflow)")
    p("                      with period_inflow = 0 for Cat A idle ALM holdings")
    p("                      (off-chain venue gains land as ALM transfers and would")
    p("                       be silently netted out otherwise)")
    p("agent_rate          = Σ_days subproxy_usds × ((1 + ssr + 0.20%)^(1/365) − 1)")
    p("                    + Σ_days subproxy_susds × ((1 + 0.20%)^(1/365) − 1)")
    p("distribution_rewards = referral / liquidity-program payouts (placeholder; Phase 3+)")
    p("sky_revenue         = Σ_days max(utilized, 0) × ((1 + ssr + 0.30%)^(1/365) − 1)")
    p("utilized            = cum_debt − cum_subproxy_usds − cum_subproxy_susds − cum_alm_usds")
    p("```")
    p("")
    p(f"See [`docs/RULES.md`]({rules_url}) for SSR history and rate conventions.")
    p("")
    return "\n".join(lines)


def write_markdown(pnl: MonthlyPnL, dest: Path, *, rules_url: str = DEFAULT_RULES_URL) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_markdown(pnl, rules_url=rules_url))
    return dest
