"""Spark Savings V2 — Phase B per-vault economic report.

For each S2 vault (S56 spUSDC ETH, S57 spUSDT ETH, S59 spPYUSD ETH,
S60 spUSDC AVAX), surface a per-vault transparency view:

* ``vsr_liability``: depositor accrual Spark owes (= ``−actual_revenue``
  of the S2 vault row; Phase A computes this as ``Σ supply(d-1) × Δpps``).
* ``pipeline_yield_on_yield_venues``: sum of ``actual_revenue`` on the
  venues listed in ``prime.savings_v2_routes[vault_id]`` (the
  hand-curated mapping in ``config/spark.yaml``).
* ``vault_share``: this vault's share of the mapped venues' total TVL
  (= ``vault_TVL_avg / Σ venue_TVL_avg``). Used to scale the pipeline
  yield down to the savings-vault-attributable slice.
* ``apr_eff_weighted``, ``vsr_apr_eff``: period-annualised effective rates.
* ``net_spread_weighted``: ``vault_share × pipeline_yield − vsr_liability``.

**Two contamination paths handled:**

1. **External-yield venues (FIXED in config):** Cat A par-stable venues
   whose ``actual_revenue`` comes exclusively from ``external_alm_sources``
   sweeps (S26 USDC raw — Anchorage tri-party loan interest; S28 PYUSD
   raw — PayPal/Paxos rewards) are EXCLUDED from the yield_venues lists
   in ``config/spark.yaml``. Including them attributes Spark-side
   off-chain program yield to savings-vault depositors — economically
   incorrect.

2. **Co-tenant capital (HANDLED via vault_share weighting):** The
   remaining yield venues (SparkLend, Aave, Morpho, Maple, Curve LP)
   hold pooled underlying from BOTH savings-vault depositors AND
   USDS-minted-via-Allocator-Vault. The ``vault_share`` weight scales
   the pipeline yield down to the savings-vault-attributable slice.

After both corrections, ``apr_eff_weighted`` should sit in a plausible
band (~3-6% APY for stable-coin deployment in 2026). The flag remains
as a safety net.

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

    # Co-tenancy weighting. The mapped venues' actual_revenue is yield
    # on the ENTIRE pooled underlying held at the ALM (savings-vault
    # depositors + USDS-minted-via-Allocator + ...). The savings-vault
    # share = vault_TVL / total_yield_venue_TVL — a per-period scalar
    # in [0, 1] that scales the attribution down to the
    # vault-attributable slice.
    yield_venue_tvl_avg: Decimal = Decimal("0")
    vault_share: Decimal = Decimal("0")
    pipeline_yield_weighted: Decimal = Decimal("0")

    # Effective rates (annualised from period totals).
    vsr_apr_eff: Decimal = Decimal("0")
    apr_eff_raw: Decimal = Decimal("0")           # apr from unweighted pipeline_yield (UPPER BOUND)
    apr_eff_weighted: Decimal = Decimal("0")      # apr from vault-share-weighted pipeline_yield
    spread_apr_weighted: Decimal = Decimal("0")

    # Net spread to Spark. ``_upper_bound`` assumes 100% attribution
    # (= old behaviour, for backward comparison). ``_weighted`` uses
    # the vault-share weight (= our best per-period estimate).
    net_spread_upper_bound: Decimal = Decimal("0")
    net_spread_weighted: Decimal = Decimal("0")

    # True when ``apr_eff_weighted`` exceeds the sanity cap — operator
    # review needed (typically signals a residual co-tenant venue we
    # still need to drop from the mapping).
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

    # Per-venue yield contributions + per-venue TVL avg (needed for the
    # co-tenancy weight). Missing entries contribute 0 to both.
    per_yield_venue = []
    pipeline_yield = Decimal("0")
    yield_venue_tvl_avg = Decimal("0")
    for vid in route:
        row = by_id.get(vid)
        if row is None:
            per_yield_venue.append((vid, "(not in provenance)", Decimal("0")))
            continue
        contrib = _D(row.get("actual_revenue"))
        per_yield_venue.append((vid, row.get("label", ""), contrib))
        pipeline_yield += contrib
        v_som = _D(row.get("value_som"))
        v_eom = _D(row.get("value_eom"))
        yield_venue_tvl_avg += (v_som + v_eom) / 2

    # Period day count for annualisation.
    period = prov.get("period", {})
    start = date.fromisoformat(period["start"])
    end   = date.fromisoformat(period["end"])
    n_days = (end - start).days + 1

    # Co-tenancy weight: the mapped yield venues hold pooled underlying
    # from multiple sources. The vault's share of the pool is roughly
    # ``vault_TVL / total_pool_TVL``. We use ``yield_venue_tvl_avg`` as
    # the pool denominator. Clamped to [0, 1] — a vault TVL larger than
    # the venues it routes to would mean some deployed capital sits
    # at unmapped destinations (cross-currency PSM3 swaps, etc.), in
    # which case the unweighted attribution is already an upper bound.
    if yield_venue_tvl_avg > 0:
        raw_share = total_avg / yield_venue_tvl_avg
        vault_share = min(Decimal("1"), raw_share)
    else:
        vault_share = Decimal("0")
    pipeline_yield_weighted = pipeline_yield * vault_share

    # Effective rates (period-annualised). Guard against zero-vault months
    # (a vault that hadn't yet been deployed produces zero TVL — APRs are
    # mathematically undefined, so we report 0 rather than divide-by-zero).
    if total_avg > 0 and n_days > 0:
        scale = Decimal(365) / Decimal(n_days)
        vsr_apr_eff      = (vsr_liability / total_avg) * scale
        apr_eff_raw      = (pipeline_yield / total_avg) * scale
        apr_eff_weighted = (pipeline_yield_weighted / total_avg) * scale
    else:
        vsr_apr_eff = apr_eff_raw = apr_eff_weighted = Decimal("0")
    spread_weighted = apr_eff_weighted - vsr_apr_eff

    net_spread_upper    = pipeline_yield          - vsr_liability
    net_spread_weighted = pipeline_yield_weighted - vsr_liability

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
        yield_venue_tvl_avg=yield_venue_tvl_avg,
        vault_share=vault_share,
        pipeline_yield_weighted=pipeline_yield_weighted,
        vsr_apr_eff=vsr_apr_eff,
        apr_eff_raw=apr_eff_raw,
        apr_eff_weighted=apr_eff_weighted,
        spread_apr_weighted=spread_weighted,
        net_spread_upper_bound=net_spread_upper,
        net_spread_weighted=net_spread_weighted,
        apr_eff_implausible=apr_eff_weighted > _APR_EFF_SANITY_CAP,
    )


def reconcile_all(prime: Prime, prov: dict) -> list[VaultReconciliation]:
    """Reconcile every vault in ``prime.savings_v2_routes`` for one period."""
    return [reconcile_vault(prime, prov, vid) for vid in prime.savings_v2_routes]
