"""Distribution Rewards (DR) per prime / ref code.

Sourced from the ``settle-dr-dune`` submodule's reconciliation workbook
(``dune-results/dr_comparison_latest.xlsx``, ``Summary`` tab). That tab is
grouped by prime agent; each row is a ``ref_code`` with one DR-USD column
per month plus a per-group ``Total`` row. For a requested (prime, month) we
read that month's column and return the prime's per-ref-code breakdown and
group total.

Reporting-time enrichment only: ``enrich_with_dr`` populates
``MonthlyPnL.distribution_rewards`` (+ keeps the ``monthly_pnl`` invariant)
and ``MonthlyPnL.dr_breakdown`` for the summary.md "DR per ref code" table.
The provenance-patch counterpart lives in ``writer.refresh_dr_only``.

Graceful by design — returns ``None`` (and leaves the pnl unchanged) when
the submodule/workbook is absent, the prime has no DR group, or the month
isn't in the sheet. So runs without the submodule initialised still work.
"""

from __future__ import annotations

import dataclasses
import functools
import logging
from decimal import Decimal
from pathlib import Path

log = logging.getLogger(__name__)

# prime_id (config) -> group label in the DR Summary tab. Only these primes
# earn tagged DR; obex and the untagged "Other" bucket are intentionally
# excluded (per the 2026-06 scope decision). Osero is listed for
# completeness but has no config/runner in this repo.
PRIME_TO_DR_GROUP: dict[str, str] = {
    "spark":   "Spark",
    "grove":   "Grove",
    "skybase": "Skybase",
    "keel":    "Keel",
    "osero":   "Osero",
}

_WORKBOOK_REL = "settle-dr-dune/dune-results/dr_comparison_latest.xlsx"
_SHEET = "Summary"


def _repo_root() -> Path:
    # src/settle/load/dr_rewards.py -> parents[3] is the repo root.
    return Path(__file__).resolve().parents[3]


def _dec(v) -> Decimal:
    if v is None or v == "":
        return Decimal("0")
    return Decimal(str(v))


@functools.lru_cache(maxsize=1)
def _summary_rows() -> tuple | None:
    """The DR ``Summary`` tab as a tuple of row-tuples (header first), or
    ``None`` when the workbook/sheet is unavailable. Cached: the workbook is
    read once per process (one ``--dr-only`` run reuses it across months)."""
    wb_path = _repo_root() / _WORKBOOK_REL
    if not wb_path.exists():
        log.warning(
            "DR workbook not found at %s — skipping distribution rewards "
            "(is the settle-dr-dune submodule initialised?)", wb_path,
        )
        return None

    import openpyxl  # lazy — only when DR is actually read

    wb = openpyxl.load_workbook(wb_path, data_only=True, read_only=True)
    try:
        if _SHEET not in wb.sheetnames:
            log.warning("DR workbook %s has no %r sheet — skipping", wb_path, _SHEET)
            return None
        rows = tuple(wb[_SHEET].iter_rows(values_only=True))
    finally:
        wb.close()
    return rows or None


def _col_for(header: tuple, label: str) -> int | None:
    """Column index whose header matches ``label`` (case-insensitive). Month
    headers may be stored as text (``'YYYY-MM'``) or as Excel date cells (which
    openpyxl returns as ``datetime`` under ``data_only``); both are normalised
    to ``'YYYY-MM'`` before matching, so a workbook regeneration that switches
    the month headers to dates doesn't silently drop all DR."""
    want = label.strip().lower()
    for i, h in enumerate(header):
        if h is None:
            continue
        if hasattr(h, "year") and hasattr(h, "month"):  # date / datetime cell
            cell = f"{h.year}-{h.month:02d}"
        else:
            cell = str(h).strip()
        if cell.lower() == want:
            return i
    return None


def load_dr(prime_id: str, month: str) -> dict | None:
    """DR for ``(prime_id, month='YYYY-MM')``.

    Returns ``{"total": Decimal, "rows": [{"ref_code": str, "amount":
    Decimal, "notes": str}], "month": month}`` or ``None`` when unavailable.
    """
    group = PRIME_TO_DR_GROUP.get(prime_id)
    if group is None:
        return None

    rows = _summary_rows()
    if not rows:
        return None

    header = rows[0]
    col = _col_for(header, month)
    if col is None:
        log.warning(
            "DR Summary has no column for month %s — skipping DR for %s",
            month, prime_id,
        )
        return None
    notes_col = _col_for(header, "notes")  # resolved by label, not a magic index

    cur = None
    out_rows: list[dict] = []
    total: Decimal | None = None
    for row in rows[1:]:
        if row[0]:
            cur = str(row[0]).strip()
        if cur != group:
            continue
        ref = row[1]
        if ref is None:
            continue
        if str(ref).strip() == "Total":
            total = _dec(row[col])
            break  # the group's Total row ends its block
        notes = row[notes_col] if notes_col is not None and len(row) > notes_col else None
        out_rows.append({
            "ref_code": str(ref).strip(),
            "amount": _dec(row[col]),
            "notes": str(notes).strip() if notes else "",
        })

    if not out_rows:
        return None
    if total is None:  # group had no explicit Total row — fall back to the sum
        total = sum((r["amount"] for r in out_rows), Decimal("0"))

    return {"total": total, "rows": out_rows, "month": month}


def enrich_with_dr(pnl):
    """Populate ``distribution_rewards`` + ``dr_breakdown`` on ``pnl`` from
    the DR workbook for ``pnl``'s prime + month.

    Idempotent: sets ``distribution_rewards`` to the group total and bumps
    ``monthly_pnl`` by the *delta* vs the current value, so re-enriching an
    already-enriched pnl is a no-op (and the ``monthly_pnl`` invariant —
    which includes ``distribution_rewards`` — stays intact). Returns ``pnl``
    unchanged when no DR data is available. ``prime_agent_total_revenue`` is a
    computed property, so it updates automatically.
    """
    month = f"{pnl.month.year}-{pnl.month.month:02d}"
    dr = load_dr(pnl.prime_id, month)
    if dr is None:
        return pnl
    total = dr["total"]
    delta = total - pnl.distribution_rewards
    return dataclasses.replace(
        pnl,
        distribution_rewards=total,
        monthly_pnl=pnl.monthly_pnl + delta,
        dr_breakdown=dr["rows"],
    )
