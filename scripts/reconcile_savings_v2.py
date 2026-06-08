"""Phase B reconciliation report for Spark Savings V2 vaults.

For a given prime + month, reads the settlement's provenance.json and
the prime config, then prints (and optionally writes to disk) a
per-vault reconciliation between the Phase A pipeline output and the
closed-form surplus implied by on-chain pps movements.

See ``docs/spark/PRD_savings_vaults.md`` §5.2 for the methodology.

Usage:
    python3 scripts/reconcile_savings_v2.py --prime spark --month 2026-05
    python3 scripts/reconcile_savings_v2.py --prime spark --month 2026-05 --write

The ``--write`` flag emits ``savings_v2_reconciliation.md`` alongside
the existing settlement artifacts.

The script does NO on-chain reads — it consumes the existing
``settlements/{prime}/{month}/provenance.json``. Re-run the settlement
pipeline first if the provenance is stale.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from settle.compute.savings_v2_reconcile import (  # noqa: E402
    VaultReconciliation, reconcile_all,
)
from settle.domain.config import load_prime  # noqa: E402


def _usd(x: Decimal) -> str:
    if x < 0:
        return f"-${-x:,.2f}"
    return f"${x:,.2f}"


def _pct(x: Decimal) -> str:
    return f"{x * 100:.3f}%"


def render_report(prime_id: str, month: str, recs: list[VaultReconciliation]) -> str:
    lines: list[str] = []
    lines.append(f"# Spark Savings V2 — per-vault economic view ({prime_id.upper()} {month})")
    lines.append("")
    lines.append(
        "Per `docs/spark/PRD_savings_vaults.md` §5.2. **Display-only.** "
        "The pipeline-yield column is an UPPER BOUND — the mapped venues "
        "hold capital from both savings-vault depositors and "
        "USDS-minted-via-Allocator-Vault, so attributing 100% of their "
        "yield to the vault over-attributes by the Allocator-funded share. "
        "Read `apr_eff` as a maximum yield envelope and treat implausibly "
        "high values (⚠) as co-tenant contamination flags."
    )
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        "| Vault | Underlying | TVL avg | VSR liability | VSR APY "
        "| Pipeline yield (max) | apr_eff (max) | Net upper bound |"
    )
    lines.append(
        "|---|---|---:|---:|---:|---:|---:|---:|"
    )
    total_vsr = Decimal("0")
    total_pipeline_yield = Decimal("0")
    total_net = Decimal("0")
    for r in recs:
        total_vsr += r.vsr_liability
        total_pipeline_yield += r.pipeline_yield
        total_net += r.net_spread_upper_bound
        flag = " ⚠" if r.apr_eff_implausible else ""
        lines.append(
            f"| {r.vault_id} | {r.underlying_symbol} "
            f"| {_usd(r.total_assets_avg)} "
            f"| {_usd(r.vsr_liability)} "
            f"| {_pct(r.vsr_apr_eff)} "
            f"| {_usd(r.pipeline_yield)} "
            f"| {_pct(r.apr_eff)}{flag} "
            f"| {_usd(r.net_spread_upper_bound)} |"
        )
    lines.append(
        f"| **Σ** | — | — "
        f"| {_usd(total_vsr)} | — "
        f"| {_usd(total_pipeline_yield)} | — "
        f"| {_usd(total_net)} |"
    )
    lines.append("")
    if any(r.apr_eff_implausible for r in recs):
        lines.append(
            "**⚠ implausible apr_eff** — a mapped venue's `actual_revenue` "
            "includes yield from non-savings-vault sources (e.g. Anchorage "
            "USDC sweeps landing at S26, PayPal PYUSD rewards landing at "
            "S28). Reduce the mapping or weight by the vault's share of "
            "total ALM underlying."
        )
        lines.append("")

    # Per-vault detail.
    for r in recs:
        lines.append(f"## {r.vault_id} — {r.vault_label}")
        lines.append("")
        lines.append(
            f"Underlying **{r.underlying_symbol}**, "
            f"period {r.n_days} days, "
            f"TVL SoM {_usd(r.total_assets_som)} → EoM {_usd(r.total_assets_eom)} "
            f"(avg {_usd(r.total_assets_avg)})."
        )
        lines.append("")
        lines.append(
            f"- **VSR liability (Phase A — exact):** {_usd(r.vsr_liability)}  "
            f"→ effective rate {_pct(r.vsr_apr_eff)} APY"
        )
        lines.append(
            f"- **Pipeline yield on mapped venues (upper bound):** "
            f"{_usd(r.pipeline_yield)} → implied rate {_pct(r.apr_eff)} APY"
            + (" ⚠ implausibly high — co-tenant attribution likely" if r.apr_eff_implausible else "")
        )
        lines.append(
            f"- **Implied Spark spread (upper bound):** {_pct(r.spread_apr_eff)} APY  "
            f"= apr_eff − vsr_apr_eff"
        )
        lines.append(
            f"- **Net upper bound to Spark:** pipeline_yield − vsr_liability = "
            f"{_usd(r.net_spread_upper_bound)}"
        )
        lines.append("")
        lines.append(
            "Per-venue yield contributions (sum = pipeline yield above):"
        )
        lines.append("")
        lines.append("| Venue | Label | actual_revenue |")
        lines.append("|---|---|---:|")
        for vid, label, contrib in r.per_yield_venue:
            lines.append(f"| {vid} | {label} | {_usd(contrib)} |")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prime", default="spark")
    parser.add_argument("--month", default="2026-05")
    parser.add_argument("--write", action="store_true",
                        help="Write savings_v2_reconciliation.md alongside provenance.json")
    args = parser.parse_args()

    prov_path = REPO / "settlements" / args.prime / args.month / "provenance.json"
    if not prov_path.exists():
        print(f"ERROR: {prov_path} not found. Run settlement first.", file=sys.stderr)
        return 1
    with prov_path.open() as f:
        prov = json.load(f)

    prime = load_prime(REPO / "config" / f"{args.prime}.yaml")
    if not prime.savings_v2_routes:
        print(f"No savings_v2_routes configured for prime {args.prime!r} — nothing to reconcile.",
              file=sys.stderr)
        return 0

    recs = reconcile_all(prime, prov)
    report = render_report(args.prime, args.month, recs)
    print(report)

    if args.write:
        out = prov_path.parent / "savings_v2_reconciliation.md"
        out.write_text(report, encoding="utf-8")
        print(f"Wrote {out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
