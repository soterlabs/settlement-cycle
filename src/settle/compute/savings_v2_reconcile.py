"""Spark Savings V2 — Phase B per-vault economic report.

For each S2 vault (S56 spUSDC ETH, S57 spUSDT ETH, S59 spPYUSD ETH,
S60 spUSDC AVAX), surface a per-vault transparency view:

* ``vsr_liability``: depositor accrual Spark owes (= ``−actual_revenue``
  of the S2 vault row; Phase A computes this as ``Σ supply(d-1) × Δpps``).
* ``pipeline_yield_on_yield_venues``: sum of ``actual_revenue`` on the
  venues listed in ``prime.savings_v2_routes[vault_id]`` (the
  hand-curated mapping in ``config/spark.yaml``).
* ``apr_eff``, ``vsr_apr_eff``: period-annualised effective rates.
* ``net_spread``: ``pipeline_yield − vsr_liability``.

**Important caveat — co-tenant attribution.** The yield venues at the
Spark ALM (S26 USDC raw, S2 SparkLend USDC, etc.) hold capital from
*both* savings-vault depositors AND USDS-minted-via-Allocator-Vault.
Attributing 100% of each venue's yield to the savings vaults
**over-attributes** by the Allocator-Vault-funded share. Read the
``apr_eff`` column as an upper bound, not as the actual yield earned
on the deployed savings-vault capital. The PRD's original Phase B
plan addressed this via independent ``apr_d`` from a Dune
deployment-metrics table — that table is no longer accessible.

What the report still gives you, despite the co-tenant caveat:
1. The exact VSR liability per vault (Phase A's settlement-affecting number).
2. The maximum-attributable yield envelope per vault.
3. A loud signal when ``apr_eff`` is implausibly high (e.g., S56 at
   ~20% APY in May 2026 → flags the Anchorage USDC sweeps landing at
   S26 that aren't pps-on-deployed-capital).

This is **display-only**: no settlement number is changed.

Inputs: parsed ``provenance.json`` dict (from a Spark settlement run)
plus the ``Prime`` config (for the routes mapping). No on-chain reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..domain.primes import Prime


def _D(x) -> Decimal:
    if x is None or x == "":
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


# Plausibility band for ``apr_eff``. Stable-coin venue yields above this
# almost always signal co-tenant contamination (a non-pro-rata yield
# source landing at one of the mapped venues — e.g. Anchorage USDC sweeps
# at S26). Flagged in the report but not an error.
_APR_EFF_SANITY_CAP = Decimal("0.10")  # 10% APY


@dataclass(frozen=True, slots=True)
class VaultReconciliation:
    """Phase B per-vault report (one period)."""
    vault_id: str
    vault_label: str
    underlying_symbol: str
    n_days: int
    yield_venue_ids: tuple[str, ...]

    # On-chain / provenance scalars (positive USD).
    total_assets_som: Decimal     # = vault.value_som (depositor liability at SoM)
    total_assets_eom: Decimal     # = vault.value_eom (depositor liability at EoM)
    total_assets_avg: Decimal     # = mean(SoM, EoM)
    vsr_liability: Decimal        # = -vault.actual_revenue (always >= 0 per chi-style pps)

    # Yield aggregation across the mapped pipeline venues.
    per_yield_venue: tuple[tuple[str, str, Decimal], ...] = field(default_factory=tuple)
    pipeline_yield: Decimal = Decimal("0")

    # Effective rates (annualised from period totals).
    vsr_apr_eff: Decimal = Decimal("0")
    apr_eff: Decimal = Decimal("0")
    spread_apr_eff: Decimal = Decimal("0")

    # Net spread to Spark if 100% of the mapped venues' yield were
    # attributable to savings-vault-deployed capital. UPPER bound only —
    # see module docstring for the co-tenant caveat.
    net_spread_upper_bound: Decimal = Decimal("0")

    # True when ``apr_eff`` exceeds ``_APR_EFF_SANITY_CAP`` — a heuristic
    # that surfaces likely co-tenant contamination (e.g. Anchorage USDC
    # sweeps inflating S26's actual_revenue beyond the deployed-capital
    # share). Operator review needed; not an error.
    apr_eff_implausible: bool = False


def reconcile_vault(
    prime: Prime,
    prov: dict,
    vault_id: str,
) -> VaultReconciliation:
    """Reconcile one vault's Phase A output against the closed-form surplus.

    Raises ``ValueError`` if the vault isn't in ``prime.savings_v2_routes``
    or isn't found in ``prov['venue_breakdown']``.
    """
    if vault_id not in prime.savings_v2_routes:
        raise ValueError(
            f"reconcile_vault: {vault_id!r} not in prime.savings_v2_routes; "
            f"available: {sorted(prime.savings_v2_routes)}"
        )
    route = prime.savings_v2_routes[vault_id]

    by_id = {v["venue_id"]: v for v in prov.get("venue_breakdown", [])}
    if vault_id not in by_id:
        raise ValueError(
            f"reconcile_vault: vault {vault_id!r} not found in provenance "
            f"venue_breakdown"
        )

    vault_row = by_id[vault_id]
    total_som = _D(vault_row.get("value_som"))
    total_eom = _D(vault_row.get("value_eom"))
    total_avg = (total_som + total_eom) / 2 if (total_som + total_eom) > 0 else Decimal("0")

    # ``actual_revenue`` for an S2 vault is the NEGATED VSR liability
    # (Phase A injects ``-vsr_liability`` as ``actual_revenue_override``).
    # Flip sign here so the report displays the liability as a positive number.
    vsr_liability = -_D(vault_row.get("actual_revenue"))

    # Resolve the underlying symbol from the prime config (cheaper than
    # threading it through the YAML route block — and the route's
    # ``underlying:`` label is documentation, not data).
    vault_venue = next((v for v in prime.venues if v.id == vault_id), None)
    underlying_sym = (
        vault_venue.underlying.symbol if (vault_venue and vault_venue.underlying)
        else "?"
    )

    # Per-venue yield contributions. Missing entries (e.g. a yield venue
    # that wasn't priced this month because it was skipped) contribute 0;
    # we record them with an empty label so the operator sees the gap.
    per_yield_venue = []
    pipeline_yield = Decimal("0")
    for vid in route:
        row = by_id.get(vid)
        if row is None:
            per_yield_venue.append((vid, "(not in provenance)", Decimal("0")))
            continue
        contrib = _D(row.get("actual_revenue"))
        per_yield_venue.append((vid, row.get("label", ""), contrib))
        pipeline_yield += contrib

    # Period day count for annualisation.
    period = prov.get("period", {})
    start = date.fromisoformat(period["start"])
    end   = date.fromisoformat(period["end"])
    n_days = (end - start).days + 1

    # Effective rates (period-annualised). Guard against zero-vault months
    # (a vault that hadn't yet been deployed produces zero TVL — APRs are
    # mathematically undefined, so we report 0 rather than divide-by-zero).
    if total_avg > 0 and n_days > 0:
        scale = Decimal(365) / Decimal(n_days)
        vsr_apr_eff = (vsr_liability / total_avg) * scale
        apr_eff     = (pipeline_yield / total_avg) * scale
    else:
        vsr_apr_eff = Decimal("0")
        apr_eff     = Decimal("0")
    spread = apr_eff - vsr_apr_eff

    # Net spread upper bound (= LHS, since `pipeline_yield − vsr_liability`
    # algebraically equals the "closed-form" `deployed × (apr − vsr) × t/T`
    # when apr is derived from `pipeline_yield / deployed`; the two views
    # are not independent. See module docstring on the co-tenant caveat.
    net_spread_upper = pipeline_yield - vsr_liability

    return VaultReconciliation(
        vault_id=vault_id,
        vault_label=vault_row.get("label", ""),
        underlying_symbol=underlying_sym,
        n_days=n_days,
        yield_venue_ids=tuple(route),
        total_assets_som=total_som,
        total_assets_eom=total_eom,
        total_assets_avg=total_avg,
        vsr_liability=vsr_liability,
        per_yield_venue=tuple(per_yield_venue),
        pipeline_yield=pipeline_yield,
        vsr_apr_eff=vsr_apr_eff,
        apr_eff=apr_eff,
        spread_apr_eff=spread,
        net_spread_upper_bound=net_spread_upper,
        apr_eff_implausible=apr_eff > _APR_EFF_SANITY_CAP,
    )


def reconcile_all(prime: Prime, prov: dict) -> list[VaultReconciliation]:
    """Reconcile every vault in ``prime.savings_v2_routes`` for one period."""
    return [reconcile_vault(prime, prov, vid) for vid in prime.savings_v2_routes]
