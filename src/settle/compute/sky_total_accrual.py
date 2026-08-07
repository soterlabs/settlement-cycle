"""Sky Net Revenue, ACCRUAL basis — the settlement-preview view.

Definition (operator, 2026-08-07): for months ≥ ``accrual_from``,

    Sky Net Revenue(M) = MSC net (accrual)  +  non-MSC net (month M)

    MSC net (accrual)  = Σ_p mint_p − Σ_p send_p        (the NEXT settlement)
    non-MSC net        = non-MSC income(M) − non-MSC expense(M)

i.e. **prime revenue earned in month M but paid in month M+1** (at the MSC
settling cycle M), plus the non-MSC flows of month M. This mirrors the
operator's settlement summary sheet and the MSC forum posts, in contrast
to the paid basis used for months < ``accrual_from`` (which carries the
settlement that EXECUTED in the month).

Per-prime construction (the MSC-post identity, all values from
``settlements/<prime>/<month>/provenance.json`` plus per-month config
adjustments for prior-cycle corrections riding the same settlement):

    sky  = sky_revenue                                  (Sky share)
    dv   = agent_rate + distribution_rewards
           + chronicle_points + gar                     (demand side)
    sv   = prime_agent_revenue − (sky_revenue − sde_revenue)
                                                        (prime supply share)
    sv_t = sv + sv_adj
    mint = round( sky + sky_adj + max(sv_t, 0) )        (ilk primes only)
    send = round( dv + dv_adj + sv_t + send_credit )

A negative supply share (e.g. Osero July −107) nets inside the send and
never mints. Everything is rounded to whole USDS — the settlement
convention (on-chain mints are integral).

**Pinned as-published figures.** The MSC post / summary sheet is the
settlement source of truth, and its whole-dollar per-prime figures come
from adjustment decimals that are not always published. Config can
therefore pin ``mint``/``send`` per prime; the derived values act as a
cross-check and a warning fires when they drift beyond $2. The skybase
``gar_in_dv`` override pins the GAR value that was inside the demand side
when the month's SNR was FROZEN (July: 152,255.89, the pre-redefinition
figure) — the final report's GAR (1% × frozen SNR, July: 105,174.26) is
deliberately different, and re-running this module must keep reproducing
the frozen SNR regardless of later report regenerations.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from ..domain import Month

_log = logging.getLogger(__name__)

# Derived-vs-pinned tolerance (USDS). The published integers round
# unpublished adjustment decimals, so ±$1 per component is expected;
# anything beyond means the report inputs or the config drifted.
_PIN_TOLERANCE = Decimal("2")

_D0 = Decimal("1")  # quantize target: whole USDS

# Accepted per-prime keys in ``msc_preview.<month>.<prime>`` — anything
# else is a typo and raises (these keys move settlement figures).
_PREVIEW_FIELDS = frozenset({
    "mint", "send", "sky_adj", "dv_adj", "sv_adj", "send_credit", "gar_in_dv",
})


def _round0(x: Decimal) -> Decimal:
    return x.quantize(_D0, rounding=ROUND_HALF_UP)


@dataclass
class AccrualPrimeRow:
    prime: str
    has_ilk: bool
    sky: Decimal            # Sky share (sky_revenue)
    dv: Decimal             # demand side (incl. gar_in_dv override if set)
    sv: Decimal             # prime supply share
    sky_adj: Decimal
    dv_adj: Decimal
    sv_adj: Decimal
    send_credit: Decimal
    mint: Decimal           # pinned (or derived-rounded) — whole USDS
    send: Decimal           # pinned (or derived-rounded) — whole USDS
    derived_mint: Decimal   # unrounded derivation, for the cross-check
    derived_send: Decimal


@dataclass
class SkyTotalAccrualMonthly:
    month: str
    rows: list[AccrualPrimeRow]
    non_msc_income: Decimal
    non_msc_expense: Decimal
    # Demand-side Buffer transfer riding the PREVIEWED settlement, when the
    # MSC post announces one (config ``msc_preview.<month>.dsb``). MSC#11
    # has none, so July is 0. Deducted from the MSC leg — the paid basis
    # books its DSB as a non-MSC Operating expense instead, but there the
    # figure comes from the executed settlement block.
    dsb: Decimal = Decimal(0)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_mint(self) -> Decimal:
        return sum((r.mint for r in self.rows), Decimal(0))

    @property
    def total_send(self) -> Decimal:
        return sum((r.send for r in self.rows), Decimal(0))

    @property
    def msc_net(self) -> Decimal:
        return self.total_mint - self.total_send - self.dsb

    @property
    def non_msc_net(self) -> Decimal:
        return self.non_msc_income - self.non_msc_expense

    @property
    def sky_net_revenue(self) -> Decimal:
        return self.msc_net + self.non_msc_net


def _load_prime_components(
    repo_root: Path, prime: str, label: str
) -> tuple[Decimal, Decimal, Decimal]:
    """(sky, dv, sv) from the prime's monthly provenance."""
    p = repo_root / "settlements" / prime / label / "provenance.json"
    if not p.exists():
        raise FileNotFoundError(
            f"sky_total accrual: missing {p} — generate {prime} {label} "
            "first. (If that report itself needs this month's SNR — the "
            "GAR prime — see the bootstrap note in compute/gar.py: pin "
            "gar_in_dv, build sky_total, then re-run the prime.)"
        )
    r = json.loads(p.read_text())["results"]
    sky = Decimal(r["sky_revenue"])
    dv = (
        Decimal(r["agent_rate"])
        + Decimal(r["distribution_rewards"])
        + Decimal(r.get("chronicle_points") or 0)
        + Decimal(r.get("gar") or 0)
    )
    sv = Decimal(r["prime_agent_revenue"]) - (sky - Decimal(r.get("sde_revenue") or 0))
    return sky, dv, sv


def _load_non_msc(repo_root: Path, label: str) -> tuple[Decimal, Decimal, list[str]]:
    p = repo_root / "settlements" / "non_msc" / label / "provenance.json"
    if not p.exists():
        raise FileNotFoundError(
            f"sky_total accrual: missing non_msc provenance at {p} — run "
            "`scripts/run_non_msc_2026.py` first"
        )
    prov = json.loads(p.read_text())
    r = prov["results"]
    return (
        Decimal(r["total_income"]),
        Decimal(r["total_expense"]),
        list(prov.get("warnings") or []),
    )


def compute_sky_total_accrual(
    month: Month,
    *,
    repo_root: Path,
    config: dict[str, Any],
) -> SkyTotalAccrualMonthly:
    """Build the accrual-basis month from the per-prime artifacts + the
    per-month ``msc_preview`` config block (raw ``sky_total.yaml`` dict)."""
    label = f"{month.year}-{month.month:02d}"
    primes: list[str] = list(config.get("accrual_primes") or [])
    if not primes:
        raise ValueError("sky_total accrual: config has no accrual_primes list")
    ilk_primes = set((config.get("allocator_ilks") or {}).keys())
    preview: dict[str, Any] = (config.get("msc_preview") or {}).get(label) or {}

    warnings: list[str] = []
    # Fail loud on a typo'd prime or field name — the paid path raises on
    # unknown streams / one_off primes for the same reason: these keys move
    # counterparty-facing settlement figures, so a silently-ignored typo
    # would publish an unadjusted number.
    for key in preview:
        if key not in primes and key != "dsb" and key != "non_msc":
            raise ValueError(
                f"sky_total accrual {label}: msc_preview has unknown key "
                f"{key!r} — expected one of {sorted(primes)} (or 'dsb' / "
                "'non_msc')"
            )
    for prime, adj in preview.items():
        if prime in ("dsb", "non_msc") or not isinstance(adj, dict):
            continue
        unknown = set(adj) - _PREVIEW_FIELDS
        if unknown:
            raise ValueError(
                f"sky_total accrual {label}: msc_preview[{prime}] has unknown "
                f"field(s) {sorted(unknown)} — expected {sorted(_PREVIEW_FIELDS)}"
            )
    if not preview:
        warnings.append(
            f"msc_preview: no entry for {label} in config/sky_total.yaml — "
            "every prime's mint/send is DERIVED from the monthly reports and "
            "cross-checks against nothing. Pin the MSC post's published "
            "figures (and any prior-cycle corrections riding the settlement) "
            "before treating this month as reconciled."
        )
    rows: list[AccrualPrimeRow] = []
    for prime in primes:
        sky, dv, sv = _load_prime_components(repo_root, prime, label)
        adj = preview.get(prime) or {}
        gar_pin = adj.get("gar_in_dv")
        if gar_pin is not None:
            # Replace the report's CURRENT gar with the value that was in
            # the demand side when this month's SNR was frozen — the report
            # is later regenerated with gar = share × frozen SNR, and this
            # module must keep reproducing the frozen SNR.
            p = repo_root / "settlements" / prime / label / "provenance.json"
            cur_gar = Decimal(json.loads(p.read_text())["results"].get("gar") or 0)
            dv = dv - cur_gar + Decimal(str(gar_pin))
        sky_adj = Decimal(str(adj.get("sky_adj") or 0))
        dv_adj = Decimal(str(adj.get("dv_adj") or 0))
        sv_adj = Decimal(str(adj.get("sv_adj") or 0))
        send_credit = Decimal(str(adj.get("send_credit") or 0))

        has_ilk = prime in ilk_primes
        sv_t = sv + sv_adj
        if not has_ilk and (sky != 0 or sky_adj != 0):
            # A prime with no allocator ilk cannot mint, so a non-zero Sky
            # share would silently vanish from both mint and send.
            raise ValueError(
                f"sky_total accrual {label}: {prime} has no allocator ilk but "
                f"a non-zero Sky share (sky={sky}, sky_adj={sky_adj}) — it "
                "cannot be minted. Register the prime's ilk in "
                "allocator_ilks or fix its monthly report."
            )
        derived_mint = (sky + sky_adj + max(sv_t, Decimal(0))) if has_ilk else Decimal(0)
        derived_send = dv + dv_adj + sv_t + send_credit

        mint = Decimal(str(adj["mint"])) if "mint" in adj else _round0(derived_mint)
        send = Decimal(str(adj["send"])) if "send" in adj else _round0(derived_send)
        for kind, pinned, derived in (
            ("mint", mint, derived_mint), ("send", send, derived_send)
        ):
            if abs(pinned - derived) > _PIN_TOLERANCE:
                warnings.append(
                    f"{prime} {kind}: pinned {pinned:,.0f} vs derived "
                    f"{derived:,.2f} (Δ {pinned - derived:+,.2f}) — beyond the "
                    f"±{_PIN_TOLERANCE} rounding tolerance. Cross-check the "
                    "monthly report and the msc_preview config against the "
                    "MSC post."
                )
        rows.append(AccrualPrimeRow(
            prime=prime, has_ilk=has_ilk, sky=sky, dv=dv, sv=sv,
            sky_adj=sky_adj, dv_adj=dv_adj, sv_adj=sv_adj,
            send_credit=send_credit, mint=mint, send=send,
            derived_mint=derived_mint, derived_send=derived_send,
        ))

    inc, exp, non_msc_warns = _load_non_msc(repo_root, label)
    for w in non_msc_warns:
        warnings.append(f"non_msc: {w}")

    # A FROZEN month pins its non-MSC leg too: the MSC pins alone don't make
    # the published SNR reproducible, because the non-MSC artifact is
    # regenerated whenever its sources are refreshed. Drift beyond $2 warns
    # (same tolerance as the MSC pins) and the pinned values win.
    non_msc_pin = preview.get("non_msc") or {}
    if non_msc_pin:
        for name, live, pinned_raw in (
            ("income", inc, non_msc_pin.get("income")),
            ("expense", exp, non_msc_pin.get("expense")),
        ):
            if pinned_raw is None:
                continue
            pinned = Decimal(str(pinned_raw))
            if abs(pinned - live) > _PIN_TOLERANCE:
                warnings.append(
                    f"non_msc {name}: pinned {pinned:,.2f} vs live artifact "
                    f"{live:,.2f} (Δ {pinned - live:+,.2f}) — the frozen month "
                    "no longer matches settlements/non_msc. Re-freeze "
                    "deliberately or investigate the drift."
                )
            if name == "income":
                inc = pinned
            else:
                exp = pinned

    dsb = Decimal(str(preview.get("dsb") or 0))
    for w in warnings:
        _log.warning("sky_total accrual %s: %s", label, w)

    return SkyTotalAccrualMonthly(
        month=label, rows=rows,
        non_msc_income=inc, non_msc_expense=exp, dsb=dsb,
        warnings=warnings,
    )


# ── artifacts ────────────────────────────────────────────────────────────────

def _usds(x: Decimal) -> str:
    d = Decimal(x)
    if d.quantize(Decimal("0.01")) == 0:
        return "0.00"
    return f"-{-d:,.2f}" if d < 0 else f"{d:,.2f}"


def render_summary(r: SkyTotalAccrualMonthly) -> str:
    L: list[str] = []
    L.append(f"# SKY_TOTAL — {r.month}")
    L.append("")
    L.append(
        f"Consolidated Sky Net Revenue, ACCRUAL basis (operator definition "
        f"2026-08-07): prime revenue EARNED in {r.month} — paid the "
        f"following month at the MSC settling this cycle — plus the "
        f"month's non-MSC flows. The per-prime mint/send figures preview "
        f"that settlement (incl. prior-cycle corrections riding it) and "
        f"are pinned to the MSC post / settlement sheet where published; "
        f"derived values from the monthly reports serve as a cross-check."
    )
    L.append("")
    L.append("## MSC leg (accrual — next settlement preview)")
    L.append("")
    L.append("| Prime | MSC debt (mint) | Send to prime |")
    L.append("|---|---:|---:|")
    for row in r.rows:
        # _usds() already carries the sign — negate rather than prefixing a
        # literal '-', which would double-sign a negative net send.
        L.append(f"| {row.prime} | {_usds(row.mint)} | {_usds(-row.send)} |")
    L.append(f"| **total** | **{_usds(r.total_mint)}** | **{_usds(-r.total_send)}** |")
    if r.dsb != 0:
        L.append(f"| Demand-side Buffer (rides the settlement) | | {_usds(-r.dsb)} |")
    L.append(f"| **MSC net (accrual)** | | **{_usds(r.msc_net)}** |")
    L.append("")
    L.append("## Non-MSC leg")
    L.append("")
    L.append("| Line | USDS |")
    L.append("|---|---:|")
    L.append(f"| non-MSC income | {_usds(r.non_msc_income)} |")
    L.append(f"| non-MSC expense | -{_usds(r.non_msc_expense)} |")
    L.append(f"| **non-MSC net** | **{_usds(r.non_msc_net)}** |")
    L.append("")
    L.append("## Sky Net Revenue")
    L.append("")
    L.append("| Field | USDS |")
    L.append("|---|---:|")
    L.append(f"| MSC net (accrual) | {_usds(r.msc_net)} |")
    L.append(f"| non-MSC net | {_usds(r.non_msc_net)} |")
    L.append(f"| **Sky Net Revenue** | **{_usds(r.sky_net_revenue)}** |")
    L.append("")
    L.append(
        "*Below the line (not deducted above): the Core Council Buffer "
        "transfer — Step 1 Capital (20% of this SNR) plus any genesis / "
        "expense repayments — buybacks, the Aligned Delegates Buffer, GAR "
        "allocations, and prime capital seedings. On the accrual basis "
        "those figures are only known once the settlement executes; the "
        "paid-basis months itemise them.*"
    )
    L.append("")
    for w in r.warnings:
        L.append(f"> ⚠ {w}")
    if r.warnings:
        L.append("")
    return "\n".join(L)


def write_sky_total_accrual(
    r: SkyTotalAccrualMonthly, out_dir: Path
) -> dict[str, Path]:
    from datetime import datetime, timezone

    out_dir.mkdir(parents=True, exist_ok=True)
    prov = {
        "id": "sky_total",
        "basis": "accrual",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "month": r.month,
        "results": {
            "per_prime": {
                row.prime: {
                    "sky": str(row.sky), "dv": str(row.dv), "sv": str(row.sv),
                    "sky_adj": str(row.sky_adj), "dv_adj": str(row.dv_adj),
                    "sv_adj": str(row.sv_adj), "send_credit": str(row.send_credit),
                    "mint": str(row.mint), "send": str(row.send),
                    "derived_mint": str(row.derived_mint),
                    "derived_send": str(row.derived_send),
                }
                for row in r.rows
            },
            "total_mint": str(r.total_mint),
            "total_send": str(r.total_send),
            "dsb": str(r.dsb),
            "msc_net": str(r.msc_net),
            "non_msc_income": str(r.non_msc_income),
            "non_msc_expense": str(r.non_msc_expense),
            "non_msc_net": str(r.non_msc_net),
            "sky_net_revenue": str(r.sky_net_revenue),
        },
        "warnings": r.warnings,
    }
    (out_dir / "provenance.json").write_text(json.dumps(prov, indent=2) + "\n")
    (out_dir / "summary.md").write_text(render_summary(r))
    return {
        "provenance": out_dir / "provenance.json",
        "summary": out_dir / "summary.md",
    }
