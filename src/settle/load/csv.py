"""Render `MonthlyPnL` to CSV (machine-readable summary).

Two files:
- ``pnl.csv``    — one headline row
- ``venues.csv`` — one row per venue (only if non-empty)
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..domain.monthly_pnl import MonthlyPnL


def write_csv(pnl: MonthlyPnL, dest: Path) -> Path:
    """Headline CSV: one row, columns suitable for downstream rollups."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "prime_id", "month", "period_start", "period_end", "n_days",
        "sky_revenue", "agent_rate", "prime_agent_revenue", "monthly_pnl",
    ]
    row = [
        pnl.prime_id, str(pnl.month),
        pnl.period.start.isoformat(), pnl.period.end.isoformat(), pnl.period.n_days,
        f"{pnl.sky_revenue}", f"{pnl.agent_rate}",
        f"{pnl.prime_agent_revenue}", f"{pnl.monthly_pnl}",
    ]
    with dest.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        w.writerow(row)
    return dest


def write_venues_csv(pnl: MonthlyPnL, dest: Path) -> Path | None:
    """Per-venue breakdown CSV. Returns None if there are no venues."""
    if not pnl.venue_breakdown:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = ["venue_id", "label", "value_som", "value_eom", "period_inflow", "revenue"]
    with dest.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for v in pnl.venue_breakdown:
            w.writerow([
                v.venue_id, v.label,
                f"{v.value_som}", f"{v.value_eom}",
                f"{v.period_inflow}", f"{v.revenue}",
            ])
    return dest
