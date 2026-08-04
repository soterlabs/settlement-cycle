"""Sky Net Revenue — ACCRUAL basis (2026-07 onward).

From July 2026 this repo computes both sides of the MSC ledger (per-prime
revenue AND Sky revenue), so ``sky_total`` no longer anchors on the M+1 MSC
settlement transaction. The buffer basis (``sky_total.py``) remains
authoritative for 2026-01…2026-06; months ≥ ``accrual_from`` in
``config/sky_total.yaml`` use::

    MSC net (accrual) = Σ_primes sky_revenue
                      − Σ_primes agent_rate
                      − Σ_primes distribution_rewards
                      − Σ_primes chronicle_points          (Sky-funded)
                      − grove_tge_penalty[month]          (config override)
    Sky Net Revenue   = MSC net + non_msc_net

``sky_revenue`` / ``agent_rate`` / ``distribution_rewards`` come from each
``settlements/<prime>/<month>/provenance.json`` (the repo is the source of
truth for both sides per the MSC operator decision, 2026-08-04);
``non_msc_net`` from the accrual non-MSC report. The primes' VENUE revenue
is income from third parties, not a Sky expense — Sky's payments to primes
are exactly ``agent_rate + distribution_rewards + chronicle_points``
(Chronicle Points confirmed Sky-funded, MSC operator 2026-08-04). Like the
buffer basis, the headline is PRE Core-Council split.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..domain import Month
from .sky_total import _load_non_msc

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PrimeAccrualRow:
    """One prime's contribution to the accrual MSC net."""

    prime: str
    sky_revenue: Decimal            # income to Sky (BR on utilized + SDE)
    agent_rate: Decimal             # Sky pays: treasury agent rate
    distribution_rewards: Decimal   # Sky pays: demand-side DR
    chronicle_points: Decimal       # Sky pays: Chronicle Points (Grove)

    @property
    def net_to_sky(self) -> Decimal:
        return (
            self.sky_revenue
            - self.agent_rate
            - self.distribution_rewards
            - self.chronicle_points
        )


@dataclass(frozen=True)
class SkyTotalAccrual:
    month: Month
    rows: list[PrimeAccrualRow]
    grove_tge_penalty: Decimal
    grove_tge_penalty_source: str   # "config:<month>" | "unset"
    non_msc_income: Decimal
    non_msc_expense: Decimal
    warnings: list[str] = field(default_factory=list)

    @property
    def non_msc_net(self) -> Decimal:
        return self.non_msc_income - self.non_msc_expense

    @property
    def msc_net(self) -> Decimal:
        return (
            sum((r.net_to_sky for r in self.rows), Decimal(0))
            - self.grove_tge_penalty
        )

    @property
    def sky_net_revenue(self) -> Decimal:
        return self.msc_net + self.non_msc_net


def compute_sky_total_accrual(
    month: Month,
    *,
    repo_root: Path,
    config: dict[str, Any],
) -> SkyTotalAccrual:
    """Accrual-basis Sky Net Revenue for ``month`` from the repo's artifacts.

    ``config`` is the parsed ``config/sky_total.yaml`` dict (must carry
    ``accrual_primes``). Every listed prime's provenance for the month is
    REQUIRED — a missing artifact raises rather than silently contributing
    $0 to the consolidated, counterparty-facing figure.
    """
    label = f"{month.year}-{month.month:02d}"
    primes = list(config.get("accrual_primes") or [])
    if not primes:
        raise ValueError(
            "sky_total accrual basis: config/sky_total.yaml has no "
            "`accrual_primes` list."
        )

    warnings: list[str] = []
    rows: list[PrimeAccrualRow] = []
    for prime in primes:
        p = repo_root / "settlements" / prime / label / "provenance.json"
        if not p.exists():
            raise FileNotFoundError(
                f"sky_total accrual basis: missing {p} — run the {prime} "
                f"settlement for {label} first (every prime in "
                "`accrual_primes` must be settled before sky_total)."
            )
        r = json.loads(p.read_text())["results"]
        rows.append(PrimeAccrualRow(
            prime=prime,
            sky_revenue=Decimal(str(r["sky_revenue"])),
            agent_rate=Decimal(str(r.get("agent_rate") or 0)),
            distribution_rewards=Decimal(str(r.get("distribution_rewards") or 0)),
            chronicle_points=Decimal(str(r.get("chronicle_points") or 0)),
        ))

    # Grove TGE penalty — same per-month config override as the buffer basis.
    tge_map = config.get("grove_tge_penalty") or {}
    tge_raw = tge_map.get(label)
    if tge_raw is None:
        penalty, penalty_src = Decimal(0), "unset"
        warnings.append(
            f"grove_tge_penalty: no override for {label} in "
            "config/sky_total.yaml — treated as $0. Back-fill from the MSC "
            "forum post if a penalty applies to this month."
        )
    else:
        penalty, penalty_src = Decimal(str(tge_raw)), f"config:{label}"

    inc, exp, non_msc_warns = _load_non_msc(repo_root, month)
    warnings.extend(non_msc_warns)

    return SkyTotalAccrual(
        month=month,
        rows=rows,
        grove_tge_penalty=penalty,
        grove_tge_penalty_source=penalty_src,
        non_msc_income=inc,
        non_msc_expense=exp,
        warnings=warnings,
    )


def _usds(x: Decimal) -> str:
    return f"{x:,.2f}"


def write_sky_total_accrual(r: SkyTotalAccrual, out_dir: Path) -> dict[str, Path]:
    """Write ``summary.md`` + ``provenance.json`` for an accrual-basis month."""
    out_dir.mkdir(parents=True, exist_ok=True)
    label = f"{r.month.year}-{r.month.month:02d}"

    L: list[str] = []
    L.append(f"# SKY_TOTAL — {label}")
    L.append("")
    L.append(
        "Consolidated Sky Net Revenue, **accrual basis** (from 2026-07 this "
        "repo computes both sides of the MSC ledger, so the report is "
        "derived from the per-prime settlement artifacts instead of the "
        "M+1 MSC settlement transaction — MSC operator decision 2026-08-04; "
        "2026-01…2026-06 remain on the buffer basis). "
        "MSC net = Σ sky_revenue − Σ agent_rate − Σ distribution_rewards − "
        "Σ chronicle_points − Grove TGE penalty. Sky's payments to primes "
        "are exactly agent_rate + distribution_rewards + chronicle_points "
        "(Chronicle Points are Sky-funded); venue revenue is prime income "
        "from third "
        "parties, not a Sky expense. The figure is PRE Core-Council split."
    )
    L.append("")
    L.append("## MSC leg (accrual basis)")
    L.append("")
    L.append("| Prime | sky_revenue | − agent_rate | − distribution_rewards | − chronicle_points | net to Sky |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for row in r.rows:
        L.append(
            f"| {row.prime} | {_usds(row.sky_revenue)} "
            f"| {_usds(-row.agent_rate)} | {_usds(-row.distribution_rewards)} "
            f"| {_usds(-row.chronicle_points)} | {_usds(row.net_to_sky)} |"
        )
    subtotal = sum((x.net_to_sky for x in r.rows), Decimal(0))
    L.append(f"| **subtotal** | | | | | **{_usds(subtotal)}** |")
    L.append(
        f"| Grove TGE penalty ({r.grove_tge_penalty_source}) | | | | "
        f"| {_usds(-r.grove_tge_penalty)} |"
    )
    L.append(f"| **MSC net (accrual basis)** | | | | | **{_usds(r.msc_net)}** |")
    L.append("")
    L.append("## Non-MSC leg")
    L.append("")
    L.append("| Line | USDS |")
    L.append("|---|---:|")
    L.append(f"| non-MSC income | {_usds(r.non_msc_income)} |")
    L.append(f"| non-MSC expense | {_usds(-r.non_msc_expense)} |")
    L.append(f"| **non-MSC net** | **{_usds(r.non_msc_net)}** |")
    L.append("")
    L.append("## Sky Net Revenue")
    L.append("")
    L.append("| Field | USDS |")
    L.append("|---|---:|")
    L.append(f"| MSC net (accrual basis) | {_usds(r.msc_net)} |")
    L.append(f"| non-MSC net | {_usds(r.non_msc_net)} |")
    L.append(f"| **Sky Net Revenue** | **{_usds(r.sky_net_revenue)}** |")
    if r.warnings:
        L.append("")
        L.append("## Warnings")
        L.append("")
        for w in r.warnings:
            L.append(f"- ⚠ {w}")
    L.append("")

    summary = out_dir / "summary.md"
    summary.write_text("\n".join(L))

    prov = {
        "id": "sky_total",
        "month": label,
        "basis": "accrual",
        "results": {
            "per_prime": {
                row.prime: {
                    "sky_revenue": str(row.sky_revenue),
                    "agent_rate": str(row.agent_rate),
                    "distribution_rewards": str(row.distribution_rewards),
                    "chronicle_points": str(row.chronicle_points),
                    "net_to_sky": str(row.net_to_sky),
                }
                for row in r.rows
            },
            "grove_tge_penalty": str(r.grove_tge_penalty),
            "grove_tge_penalty_source": r.grove_tge_penalty_source,
            "msc_net": str(r.msc_net),
            "non_msc_income": str(r.non_msc_income),
            "non_msc_expense": str(r.non_msc_expense),
            "non_msc_net": str(r.non_msc_net),
            "sky_net_revenue": str(r.sky_net_revenue),
        },
        "warnings": r.warnings,
    }
    prov_path = out_dir / "provenance.json"
    prov_path.write_text(json.dumps(prov, indent=2) + "\n")
    return {"summary": summary, "provenance": prov_path}
