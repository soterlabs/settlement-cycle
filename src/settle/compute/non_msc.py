"""Non-MSC Sky protocol P&L — the sixth reporting unit next to the five primes.

Computes, per calendar month, the Sky revenue/expense streams that do NOT flow
through prime agents (methodology:
https://hackmd.io/@W57nO5PyRMKhcLqjvsLifw/S1zxTDpXMg — every line validated to
the dollar for May 2026, see PRD §17.13):

  income   = PSM/Coinbase jar burns (cash at burn, attributed to the month the
             burn follows) + stability fees on the 9 core-vault ilks (Art × Δrate
             at each vat.fold — what fold credits to the vow)
  expense  = sUSDS SSR at drip (GROSS — prime-held SSR stays in the expense
             because MSC sky_revenue carries the offsetting BR income; the
             prime/user split is informational) + legacy DSR + stUSDS

All extraction happens in ONE Dune execution
(``queries/non_msc_streams.sql``); this module buckets the rows and renders
``provenance.json`` + ``summary.md`` under ``settlements/non_msc/<YYYY-MM>/``.

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

# The jar burn for month M lands ~10 days into M+1; scan through the end of
# M+1 so the whole attribution window (m_end, next_m_end] is covered, plus a
# little slack for the pin resolution.
_BURN_WINDOW_SLACK_DAYS = 3


def _month_bounds(month: Month) -> tuple[date, date, date]:
    start = date(month.year, month.month, 1)
    if month.month == 12:
        end_excl = date(month.year + 1, 1, 1)
    else:
        end_excl = date(month.year, month.month + 1, 1)
    if end_excl.month == 12:
        burn_end_excl = date(end_excl.year + 1, 1, 1)
    else:
        burn_end_excl = date(end_excl.year, end_excl.month + 1, 1)
    return start, end_excl, burn_end_excl


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
    warnings: list[str] = field(default_factory=list)

    @property
    def susds_prime_held(self) -> Decimal:
        return sum(self.susds_prime_carveout.values(), Decimal(0))

    @property
    def susds_expense_to_users(self) -> Decimal:
        """Informational split: SSR accrued to NON-prime holders."""
        return self.susds_expense_gross - self.susds_prime_held

    @property
    def total_income(self) -> Decimal:
        return self.psm_jar_income + self.stability_fee_income

    @property
    def total_expense(self) -> Decimal:
        # GROSS sUSDS: the SSR Sky pays on PRIME-held sUSDS must stay in the
        # expense — MSC sky_revenue already carries the offsetting BR income
        # on the debt backing those positions (Rule 5 neutrality), so
        # deducting the prime-held slice here would double-count income at
        # the consolidated (sky_total) level. The prime/user split is kept
        # as an INFORMATIONAL breakdown only.
        return self.susds_expense_gross + self.dsr_expense + self.stusds_expense

    @property
    def net_revenue(self) -> Decimal:
        return self.total_income - self.total_expense


def resolve_pin_block(month: Month) -> int:
    """Block pin for the month's extraction.

    Must cover the jar-burn attribution window (through end of month+1), so:
    EoD of ``burn_end_excl`` when that is in the past, else the current safe
    head. The pin is part of the Dune cache key — an incomplete window pinned
    today re-executes automatically when re-run later with a later pin.
    """
    from ..domain import Chain
    from ..extract import rpc

    _, _, burn_end_excl = _month_bounds(month)
    target = datetime.combine(
        burn_end_excl + timedelta(days=_BURN_WINDOW_SLACK_DAYS),
        time.min, tzinfo=timezone.utc,
    )
    now = datetime.now(tz=timezone.utc)
    if target > now:
        target = now - timedelta(minutes=10)   # small reorg margin
    return rpc.find_block_at_or_before(Chain.ETHEREUM, target)


def compute_non_msc_monthly(month: Month, pin_block: int | None = None) -> NonMscMonthly:
    from ..extract.dune import execute_query

    start, end_excl, burn_end_excl = _month_bounds(month)
    if pin_block is None:
        pin_block = resolve_pin_block(month)

    df = execute_query(
        _SQL,
        params={
            "month_start": start.isoformat(),
            "month_end_excl": end_excl.isoformat(),
            "burn_end_excl": burn_end_excl.isoformat(),
        },
        pin_block=pin_block,
    )

    burns: list[dict] = []
    fees: dict[str, Decimal] = {}
    carve: dict[str, Decimal] = {}
    susds_gross = dsr = stusds = Decimal(0)
    warnings: list[str] = []

    for _, row in df.iterrows():
        stream = row["stream"]
        amount = Decimal(str(row["amount"]))
        if stream == "income:psm_jar":
            burns.append({"date": str(row["label"]), "amount": amount})
        elif stream == "income:stability_fee":
            fees[row["label"]] = amount
        elif stream == "expense:susds_drip":
            susds_gross = amount
        elif stream == "expense:susds_prime":
            carve[row["label"]] = amount
        elif stream == "expense:dsr_drip":
            dsr = amount
        elif stream == "expense:stusds_drip":
            stusds = amount
        else:
            raise ValueError(f"non_msc: unknown stream {stream!r} from query")

    # Attribution: "the first jar burn after a month ends is that month's
    # income" (methodology doc, literal). The first burn-DATE's burns count
    # (rows are date-granular; multiple burns in the same settlement day are
    # one payment); any LATER burn in the window is surfaced for
    # transparency but NOT attributed — it is loud because the rule leaves
    # that money unattributed (e.g. 2026-01-08, tracked with the
    # methodology author).
    burns.sort(key=lambda b: b["date"])
    first_date = burns[0]["date"] if burns else None
    attributed = [b for b in burns if b["date"] == first_date]
    excluded = [b for b in burns if b["date"] != first_date]

    if not burns:
        # Legitimate only while the burn window hasn't elapsed (report run
        # before the ~day-10 burn of month+1); loud either way.
        warnings.append(
            f"no jar burn found in ({end_excl - timedelta(days=1)}, "
            f"{burn_end_excl - timedelta(days=1)}] at pin {pin_block} — PSM "
            "income is $0 in this run; re-run after the monthly burn lands."
        )
    if excluded:
        skipped = ", ".join(f"{b['date']} (${b['amount']:,.2f})" for b in excluded)
        warnings.append(
            f"{len(excluded)} extra jar burn(s) in the attribution window "
            f"NOT attributed per the first-burn rule: {skipped} — confirm "
            "attribution with the methodology author."
        )
    for w in warnings:
        _log.warning("non_msc %s: %s", month, w)

    return NonMscMonthly(
        month=f"{month.year}-{month.month:02d}",
        pin_block=pin_block,
        psm_jar_income=sum((b["amount"] for b in attributed), Decimal(0)),
        jar_burns=attributed,
        stability_fee_income=sum(fees.values(), Decimal(0)),
        stability_fees_by_ilk=fees,
        susds_expense_gross=susds_gross,
        susds_prime_carveout=carve,
        dsr_expense=dsr,
        stusds_expense=stusds,
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


def render_summary(r: NonMscMonthly) -> str:
    L: list[str] = []
    L.append(f"# NON_MSC — {r.month}")
    L.append("")
    L.append("Sky protocol P&L outside the prime-agent (MSC) perimeter. "
             "Methodology: PSM income cash-recognized at the jar burn "
             "following month-end; stability fees at `vat.fold` (Art × Δrate); "
             "savings interest at `drip`, sUSDS net of the prime-held "
             "carve-out (MSC-accounted).")
    L.append("")
    L.append("## Income")
    L.append("")
    L.append("| Stream | USDS |")
    L.append("|---|---:|")
    for b in r.jar_burns:
        L.append(f"| PSM/Coinbase jar burn ({b['date']}) | {_usds(b['amount'])} |")
    if not r.jar_burns:
        L.append("| PSM/Coinbase jar burn (none in window yet) | 0.00 |")
    for ilk, v in sorted(r.stability_fees_by_ilk.items(), key=lambda kv: -kv[1]):
        L.append(f"| stability fee {ilk} | {_usds(v)} |")
    L.append(f"| **total income** | **{_usds(r.total_income)}** |")
    L.append("")
    L.append("## Expense")
    L.append("")
    L.append("| Stream | USDS |")
    L.append("|---|---:|")
    L.append(f"| sUSDS SSR (gross, all holders) | {_usds(r.susds_expense_gross)} |")
    L.append(f"| — of which: non-prime users (informational) | {_usds(r.susds_expense_to_users)} |")
    for holder, v in sorted(r.susds_prime_carveout.items(), key=lambda kv: -kv[1]):
        if v.quantize(Decimal("0.01")) == 0:
            continue   # sub-cent dust holder
        L.append(f"| — of which: prime-held, {holder} (offset by BR in MSC) | {_usds(v)} |")
    L.append(f"| DSR (legacy pot) | {_usds(r.dsr_expense)} |")
    L.append(f"| stUSDS | {_usds(r.stusds_expense)} |")
    L.append(f"| **total expense** | **{_usds(r.total_expense)}** |")
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
