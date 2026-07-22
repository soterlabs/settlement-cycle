"""Non-MSC Sky protocol P&L — the sixth reporting unit next to the five primes.

Computes, per calendar month, the Sky revenue/expense streams that do NOT flow
through prime agents. Methodology: "Sky Net Revenue — Non-MSC" handoff
(2026-07-16), validated against May and June 2026 published BA/SFF figures
(see PRD §17.13). The two non-MSC legs are:

  income   = stability fees on the non-ALLOCATOR ilk universe (ACCRUAL basis:
             Art × Δr_true integrated across the month, r_true reconstructed
             from `duty` — independent of when jug.drip fired; Crypto Vaults +
             Legacy RWA sections) + Legacy-RWA jar voids (tripwire, ~0) +
             PSM/Coinbase jar burns (cash basis — every burn LANDING in the
             calendar month) + liquidation revenue (Σ clip.take owe − Σ dog.bark
             due) + surplus returns (join→vow moves not attributable to the PSM
             jar or an RWA jar)
  expense  = savings interest on the ACCRUAL basis (each drip's minted amount
             apportioned to the month by chi-boundary interpolation) — sUSDS SSR
             (GROSS: prime-held SSR stays in because MSC sky_revenue carries the
             offsetting BR income; the prime/user split is informational) +
             legacy DSR + stUSDS —
             plus liquidation keeper incentives (Σ clip coin over kicks+redos)
             and Vest (gross suckable DssVest payouts sucked from the vow)

All extraction happens in ONE Dune execution
(``queries/non_msc_streams.sql``); this module buckets the rows and renders
``provenance.json`` + ``summary.md`` under ``settlements/non_msc/<YYYY-MM>/``.
A HyperSync-backed extractor producing the identical buckets lives in
``normalize/sources/hypersync_non_msc.py`` (side-by-side, Dune-parity).

This is deliberately NOT a ``Prime`` — no ilk debt, ALM, BR/CoF or agent-rate
machinery applies; forcing it through ``compute_monthly_pnl`` would be all
special cases.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from ..domain import Month

_log = logging.getLogger(__name__)

_QUERIES = Path(__file__).resolve().parents[1] / "queries"
_SQL = _QUERIES / "non_msc_streams.sql"

def _month_bounds(month: Month) -> tuple[date, date]:
    """(first day of month M, first day of month M+1)."""
    start = date(month.year, month.month, 1)
    if month.month == 12:
        end_excl = date(month.year + 1, 1, 1)
    else:
        end_excl = date(month.year, month.month + 1, 1)
    return start, end_excl


@dataclass
class NonMscMonthly:
    month: str
    pin_block: int
    psm_jar_income: Decimal
    jar_burns: list[dict]                 # [{date, amount}]
    stability_fee_income: Decimal
    stability_fees_by_ilk: dict[str, Decimal]
    susds_expense_gross: Decimal
    susds_prime_carveout: dict[str, Decimal]   # holder label → SSR accrued
    dsr_expense: Decimal
    stusds_expense: Decimal
    # Liquidations (Liquidations 2.0): revenue = Σ owe (clip.take) − Σ due
    # (dog.bark); an under-recovering auction books negative revenue.
    liq_owe: Decimal = Decimal(0)              # Σ clip.take owe (rad → USDS)
    liq_due: Decimal = Decimal(0)              # Σ dog.bark due (rad → USDS)
    liq_expense: Decimal = Decimal(0)          # Σ clip coin over kicks + redos
    # Surplus returns: join→vow moves not attributable to the PSM/RWA jar.
    surplus_returns: list[dict] = field(default_factory=list)   # [{date, amount}]
    # Legacy-RWA jar voids (RwaJar.void → vow) — tripwire, ~0 in 2026.
    rwa_jar_void: Decimal = Decimal(0)
    # Vest: gross suckable DssVest payouts (DAI + USDS vest contracts).
    vest_expense: Decimal = Decimal(0)
    warnings: list[str] = field(default_factory=list)

    @property
    def susds_prime_held(self) -> Decimal:
        return sum(self.susds_prime_carveout.values(), Decimal(0))

    @property
    def susds_expense_to_users(self) -> Decimal:
        """Informational split: SSR accrued to NON-prime holders."""
        return self.susds_expense_gross - self.susds_prime_held

    @property
    def liq_revenue(self) -> Decimal:
        """Realized liquidation penalty — Σ owe (takes) − Σ due (barks)."""
        return self.liq_owe - self.liq_due

    @property
    def surplus_return_income(self) -> Decimal:
        return sum((s["amount"] for s in self.surplus_returns), Decimal(0))

    @property
    def total_income(self) -> Decimal:
        return (
            self.psm_jar_income
            + self.stability_fee_income
            + self.rwa_jar_void
            + self.liq_revenue
            + self.surplus_return_income
        )

    @property
    def total_expense(self) -> Decimal:
        # GROSS sUSDS: the SSR Sky pays on PRIME-held sUSDS must stay in the
        # expense — MSC sky_revenue already carries the offsetting BR income
        # on the debt backing those positions (Rule 5 neutrality), so
        # deducting the prime-held slice here would double-count income at
        # the consolidated (sky_total) level. The prime/user split is kept
        # as an INFORMATIONAL breakdown only.
        return (
            self.susds_expense_gross
            + self.dsr_expense
            + self.stusds_expense
            + self.liq_expense
            + self.vest_expense
        )

    @property
    def net_revenue(self) -> Decimal:
        return self.total_income - self.total_expense


def resolve_pin_block(month: Month) -> int:
    """Block pin for the month's extraction — the last block of month M.

    All streams (incl. the cash-basis jar burns, counted in the month the
    transfer lands) fall within calendar month M, so the pin is the last block
    of M — EoD of the last day, i.e. 00:00 of month M+1. Clamped to a safe head
    when M hasn't fully elapsed. The pin is part of the Dune cache key, so an
    in-month run re-executes automatically when re-run later with a later pin.
    """
    from ..domain import Chain
    from ..extract import rpc

    _, end_excl = _month_bounds(month)
    target = datetime.combine(end_excl, time.min, tzinfo=timezone.utc)  # 00:00 of M+1
    now = datetime.now(tz=timezone.utc)
    if target > now:
        target = now - timedelta(minutes=10)   # small reorg margin
    return rpc.find_block_at_or_before(Chain.ETHEREUM, target)


def _dune_streams(month: Month, pin_block: int):
    """Default extractor — one Dune execution of ``non_msc_streams.sql``."""
    from ..extract.dune import execute_query

    start, end_excl = _month_bounds(month)
    # Widened literal bounds for the savings series: the accrual basis needs the
    # drip intervals straddling the month bounds (incl. the first drip AFTER
    # month_end). These MUST stay date literals — a computed
    # `DATE '…' - INTERVAL '3' DAY` defeats partition pruning and full-scans the
    # drip tables (Dune resource cap).
    return execute_query(
        _SQL,
        params={
            "month_start": start.isoformat(),
            "month_end_excl": end_excl.isoformat(),
            "savings_start": (start - timedelta(days=3)).isoformat(),
            "savings_end": (end_excl + timedelta(days=3)).isoformat(),
        },
        pin_block=pin_block,
    )


def compute_non_msc_monthly(
    month: Month,
    pin_block: int | None = None,
    source: object | None = None,
) -> NonMscMonthly:
    """Bucket the per-month non-MSC stream rows into a :class:`NonMscMonthly`.

    ``source`` is the row extractor: the default is the Dune query
    (:func:`_dune_streams`); pass a ``HyperSyncNonMscSource`` (or any object with
    a ``streams(month, pin_block) -> DataFrame`` method, or a bare callable) to
    run the raw-log backend. Both emit the identical
    ``[stream, label, event_date, amount]`` contract, so this bucketing is
    backend-agnostic (see ``scripts/compare_non_msc_sources.py``).
    """
    start, end_excl = _month_bounds(month)
    if pin_block is None:
        pin_block = resolve_pin_block(month)

    if source is None:
        df = _dune_streams(month, pin_block)
    elif hasattr(source, "streams"):
        df = source.streams(month, pin_block)
    else:
        df = source(month, pin_block)

    burns: list[dict] = []
    fees: dict[str, Decimal] = {}
    carve: dict[str, Decimal] = {}
    surplus: list[dict] = []
    susds_gross = dsr = stusds = Decimal(0)
    liq_owe = liq_due = liq_coin = rwa_void = vest = Decimal(0)
    warnings: list[str] = []

    for _, row in df.iterrows():
        stream = row["stream"]
        amount = Decimal(str(row["amount"]))
        if stream == "income:psm_jar":
            burns.append({"date": str(row["label"]), "amount": amount})
        elif stream == "income:stability_fee":
            fees[row["label"]] = amount
        elif stream == "income:liq_owe":
            liq_owe = amount
        elif stream == "income:liq_due":
            liq_due = amount
        elif stream == "income:surplus_return":
            surplus.append({"date": str(row["label"]), "amount": amount})
        elif stream == "income:rwa_void":
            rwa_void = amount
        elif stream == "expense:susds_drip":
            susds_gross = amount
        elif stream == "expense:susds_prime":
            carve[row["label"]] = amount
        elif stream == "expense:dsr_drip":
            dsr = amount
        elif stream == "expense:stusds_drip":
            stusds = amount
        elif stream == "expense:liq_coin":
            liq_coin = amount
        elif stream == "expense:vest":
            vest = amount
        else:
            raise ValueError(f"non_msc: unknown stream {stream!r} from query")

    # Attribution: cash / transfer-date basis — PSM income for month M is EVERY
    # jar burn that LANDS in calendar month M. Multiple burns in the month all
    # count (e.g. Jan 2026 has two: December's on-slot burn plus November's
    # late one, both landing in January). A month with no burn is $0 income —
    # legitimate only while month M is still in progress (the burn hasn't
    # happened yet); loud once M has fully elapsed.
    burns.sort(key=lambda b: b["date"])
    surplus.sort(key=lambda s: s["date"])
    if not burns:
        warnings.append(
            f"no jar burn landed in [{start}, {end_excl - timedelta(days=1)}] "
            f"at pin {pin_block} — PSM income is $0 for {month}. Expected only "
            "if the month's burn hasn't happened yet; re-run once it lands."
        )
    if rwa_void.quantize(Decimal("0.01")) != 0:
        warnings.append(
            f"RWA jar void booked {_usds(rwa_void)} — the legacy-RWA jar "
            "tripwire fired (normally $0). Confirm the deal/attribution."
        )
    for w in warnings:
        _log.warning("non_msc %s: %s", month, w)

    return NonMscMonthly(
        month=f"{month.year}-{month.month:02d}",
        pin_block=pin_block,
        psm_jar_income=sum((b["amount"] for b in burns), Decimal(0)),
        jar_burns=burns,
        stability_fee_income=sum(fees.values(), Decimal(0)),
        stability_fees_by_ilk=fees,
        susds_expense_gross=susds_gross,
        susds_prime_carveout=carve,
        dsr_expense=dsr,
        stusds_expense=stusds,
        liq_owe=liq_owe,
        liq_due=liq_due,
        liq_expense=liq_coin,
        surplus_returns=surplus,
        rwa_jar_void=rwa_void,
        vest_expense=vest,
        warnings=warnings,
    )


# ── artifacts ────────────────────────────────────────────────────────────────

def _usds(x: Decimal) -> str:
    d = Decimal(x)
    if d.quantize(Decimal("0.01")) == 0:
        return "0.00"
    if d < 0:
        return f"-{-d:,.2f}"
    return f"{d:,.2f}"


def _is_rwa(ilk: str) -> bool:
    return ilk.upper().startswith("RWA")


def render_summary(r: NonMscMonthly) -> str:
    L: list[str] = []
    L.append(f"# NON_MSC — {r.month}")
    L.append("")
    L.append("Sky protocol P&L outside the prime-agent (MSC) perimeter. "
             "Methodology (handoff 2026-07-16): stability fees on the accrual "
             "basis (Art × Δr_true, r_true reconstructed from `duty`); PSM "
             "income at the jar burn's landing month (cash basis); liquidation "
             "revenue = Σ take.owe − Σ bark.due; surplus returns = join→vow "
             "moves not attributable to the PSM/RWA jar; savings interest on "
             "the accrual basis (drips apportioned by chi-boundary "
             "interpolation; sUSDS gross, prime split informational); "
             "liquidation keeper incentives and Vest suckable payouts on the "
             "expense side.")
    L.append("")

    vault_fees = {k: v for k, v in r.stability_fees_by_ilk.items() if not _is_rwa(k)}
    rwa_fees = {k: v for k, v in r.stability_fees_by_ilk.items() if _is_rwa(k)}

    L.append("## Income")
    L.append("")
    L.append("| Section | Line | USDS |")
    L.append("|---|---|---:|")
    # Crypto Vaults
    for ilk, v in sorted(vault_fees.items(), key=lambda kv: -kv[1]):
        L.append(f"| Crypto Vaults | stability fee {ilk} | {_usds(v)} |")
    L.append(f"| Crypto Vaults | **subtotal** | **{_usds(sum(vault_fees.values(), Decimal(0)))}** |")
    # Legacy RWA
    for ilk, v in sorted(rwa_fees.items(), key=lambda kv: -kv[1]):
        L.append(f"| Legacy RWA | stability fee {ilk} | {_usds(v)} |")
    L.append(f"| Legacy RWA | RWA jars (void) | {_usds(r.rwa_jar_void)} |")
    L.append(f"| Legacy RWA | **subtotal** | "
             f"**{_usds(sum(rwa_fees.values(), Decimal(0)) + r.rwa_jar_void)}** |")
    # PSM
    for b in r.jar_burns:
        L.append(f"| PSM | LitePSM jar burn ({b['date']}) | {_usds(b['amount'])} |")
    if not r.jar_burns:
        L.append("| PSM | LitePSM jar burn (none landed this month yet) | 0.00 |")
    L.append(f"| PSM | **subtotal** | **{_usds(r.psm_jar_income)}** |")
    # Liquidations
    L.append(f"| Liquidations | liquidation revenue (Σowe {_usds(r.liq_owe)} − Σdue "
             f"{_usds(r.liq_due)}) | {_usds(r.liq_revenue)} |")
    # Other
    for s in r.surplus_returns:
        L.append(f"| Other | surplus return ({s['date']}) | {_usds(s['amount'])} |")
    if not r.surplus_returns:
        L.append("| Other | surplus returns | 0.00 |")
    L.append(f"| **Total** | | **{_usds(r.total_income)}** |")
    L.append("")

    L.append("## Expense")
    L.append("")
    L.append("| Section | Line | USDS |")
    L.append("|---|---|---:|")
    L.append(f"| Savings | sUSDS SSR (gross, all holders) | {_usds(r.susds_expense_gross)} |")
    L.append(f"| Savings | — of which: non-prime users (informational) | {_usds(r.susds_expense_to_users)} |")
    for holder, v in sorted(r.susds_prime_carveout.items(), key=lambda kv: -kv[1]):
        if v.quantize(Decimal("0.01")) == 0:
            continue   # sub-cent dust holder
        L.append(f"| Savings | — of which: prime-held, {holder} (offset by BR in MSC) | {_usds(v)} |")
    L.append(f"| Savings | stUSDS | {_usds(r.stusds_expense)} |")
    L.append(f"| Savings | DSR (legacy pot) | {_usds(r.dsr_expense)} |")
    L.append(f"| Liquidations | keeper incentives (Σ coin, kicks + redos) | {_usds(r.liq_expense)} |")
    L.append(f"| Vest | gross suckable payouts | {_usds(r.vest_expense)} |")
    L.append(f"| **Total** | | **{_usds(r.total_expense)}** |")
    L.append("")
    L.append("## Net")
    L.append("")
    L.append("| Field | USDS |")
    L.append("|---|---:|")
    L.append(f"| **non-MSC net revenue** | **{_usds(r.net_revenue)}** |")
    L.append("")
    for w in r.warnings:
        L.append(f"> ⚠ {w}")
    if r.warnings:
        L.append("")
    return "\n".join(L)


def write_non_msc(r: NonMscMonthly, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prov = {
        "id": "non_msc",
        "month": r.month,
        "pin_block": r.pin_block,
        "results": {
            "psm_jar_income": str(r.psm_jar_income),
            "jar_burns": [{"date": b["date"], "amount": str(b["amount"])} for b in r.jar_burns],
            "stability_fee_income": str(r.stability_fee_income),
            "stability_fees_by_ilk": {k: str(v) for k, v in r.stability_fees_by_ilk.items()},
            "susds_expense_gross": str(r.susds_expense_gross),
            "susds_prime_carveout": {k: str(v) for k, v in r.susds_prime_carveout.items()},
            "susds_prime_held": str(r.susds_prime_held),
            "susds_expense_to_users": str(r.susds_expense_to_users),
            "dsr_expense": str(r.dsr_expense),
            "stusds_expense": str(r.stusds_expense),
            "liq_owe": str(r.liq_owe),
            "liq_due": str(r.liq_due),
            "liq_revenue": str(r.liq_revenue),
            "liq_expense": str(r.liq_expense),
            "surplus_returns": [{"date": s["date"], "amount": str(s["amount"])} for s in r.surplus_returns],
            "surplus_return_income": str(r.surplus_return_income),
            "rwa_jar_void": str(r.rwa_jar_void),
            "vest_expense": str(r.vest_expense),
            "total_income": str(r.total_income),
            "total_expense": str(r.total_expense),
            "net_revenue": str(r.net_revenue),
        },
        "warnings": r.warnings,
    }
    prov_path = out_dir / "provenance.json"
    prov_path.write_text(json.dumps(prov, indent=2) + "\n")
    summary_path = out_dir / "summary.md"
    summary_path.write_text(render_summary(r))
    return {"provenance": prov_path, "summary": summary_path}
