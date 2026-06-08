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


def render_report(
    prime_id: str,
    month: str,
    recs: list[VaultReconciliation],
    pol_agent_rate_total: Decimal = Decimal("0"),
) -> str:
    lines: list[str] = []
    lines.append(f"# Spark Savings V2 — per-vault economic view ({prime_id.upper()} {month})")
    lines.append("")
    lines.append(
        "Per `docs/spark/PRD_savings_vaults.md` §5.2. **Display-only.** "
        "Contamination handling: (1) Cat A par-stable venues whose yield "
        "comes from `external_alm_sources` sweeps (S26 USDC raw → Anchorage, "
        "S28 PYUSD raw → PayPal) are excluded from the mapping in "
        "`config/spark.yaml`. (2) The remaining lending venues are "
        "weighted by `vault_share = vault_TVL_avg / Σ venue_TVL_avg` to "
        "scale out the USDS-minted-via-Allocator co-tenant capital."
    )
    lines.append("")
    if pol_agent_rate_total > 0:
        lines.append(
            f"**Note — S32 POL agent rate (this period): "
            f"{_usd(pol_agent_rate_total)}.** Sky pays Spark the agent rate "
            f"(+20bps over SSR) on the pooled sUSDS POL at the Spark ETH ALM "
            f"(funded by both USDS-via-Allocator and savings-vault deposits "
            f"swapped through PSM3). Routed as a Sky Revenue reduction "
            f"(parallel to the 30bps `susds_spread_reimbursement`), it "
            f"reduces what Spark owes Sky by this amount. It is NOT "
            f"attributed to any single vault in the per-vault table below — "
            f"surfacing it here for context so the reader can compute "
            f"Spark's all-in position on savings vaults as "
            f"`net_spread_weighted_total + pol_agent_rate_total`."
        )
        lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(
        "| Vault | Underlying | TVL avg | Share | VSR liability "
        "| VSR APY | Pipeline yield (raw) | Yield (weighted) "
        "| apr_eff (weighted) | Net (weighted) |"
    )
    lines.append(
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    )
    total_vsr = Decimal("0")
    total_pipeline_yield_w = Decimal("0")
    total_net_w = Decimal("0")
    for r in recs:
        total_vsr += r.vsr_liability
        total_pipeline_yield_w += r.pipeline_yield_weighted
        total_net_w += r.net_spread_weighted
        flag = " ⚠" if r.apr_eff_implausible else ""
        lines.append(
            f"| {r.vault_id} | {r.underlying_symbol} "
            f"| {_usd(r.total_assets_avg)} "
            f"| {_pct(r.vault_share)} "
            f"| {_usd(r.vsr_liability)} "
            f"| {_pct(r.vsr_apr_eff)} "
            f"| {_usd(r.pipeline_yield)} "
            f"| {_usd(r.pipeline_yield_weighted)} "
            f"| {_pct(r.apr_eff_weighted)}{flag} "
            f"| {_usd(r.net_spread_weighted)} |"
        )
    lines.append(
        f"| **Σ** | — | — | — "
        f"| {_usd(total_vsr)} | — | — "
        f"| {_usd(total_pipeline_yield_w)} | — "
        f"| {_usd(total_net_w)} |"
    )
    lines.append("")
    if any(r.apr_eff_implausible for r in recs):
        lines.append(
            "**⚠ implausible apr_eff_weighted** — a residual non-vault yield "
            "source remains in the mapping after the external-yield + "
            "co-tenancy corrections. Investigate the per-venue table below."
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
            f"- **Mapped yield venues TVL avg:** {_usd(r.yield_venue_tvl_avg)}  "
            f"→ vault_share = {_pct(r.vault_share)}"
        )
        lines.append(
            f"- **Pipeline yield (raw, all co-tenants):** {_usd(r.pipeline_yield)}  "
            f"→ implied rate {_pct(r.apr_eff_raw)} APY (upper bound, ignore)"
        )
        lines.append(
            f"- **Pipeline yield (weighted to vault share):** "
            f"{_usd(r.pipeline_yield_weighted)} → implied rate "
            f"{_pct(r.apr_eff_weighted)} APY"
            + (" ⚠ implausible — investigate" if r.apr_eff_implausible else "")
        )
        lines.append(
            f"- **Implied Spark spread (weighted):** {_pct(r.spread_apr_weighted)} APY  "
            f"= apr_eff_weighted − vsr_apr_eff"
        )
        lines.append(
            f"- **Net spread to Spark (weighted):** vault_share × pipeline_yield − vsr_liability = "
            f"{_usd(r.net_spread_weighted)}"
        )
        lines.append("")
        lines.append(
            "Per-venue yield contributions (raw, pre-weighting):"
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
    pol_total = Decimal(str(prov.get("results", {}).get("pol_agent_rate") or 0))
    report = render_report(args.prime, args.month, recs, pol_total)
    print(report)

    if args.write:
        out = prov_path.parent / "savings_v2_reconciliation.md"
        out.write_text(report, encoding="utf-8")
        print(f"Wrote {out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
