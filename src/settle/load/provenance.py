"""Provenance JSON — the audit trail for a settlement run.

Records: pin blocks, source identifiers, generation timestamp, pipeline version.
Does NOT record raw query results — those live in the Extract cache.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import __version__
from ..domain.monthly_pnl import MonthlyPnL


def render_provenance(
    pnl: MonthlyPnL,
    *,
    sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the provenance dict. Pure — easy to test/snapshot."""
    return {
        "settle_version": __version__,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prime_id": pnl.prime_id,
        "month": str(pnl.month),
        "period": {
            "start": pnl.period.start.isoformat(),
            "end": pnl.period.end.isoformat(),
            "n_days": pnl.period.n_days,
        },
        "pin_blocks_eom": {c.value: blk for c, blk in pnl.period.pin_blocks.items()},
        "pin_blocks_som": {c.value: blk for c, blk in pnl.pin_blocks_som.items()},
        "results": {
            "sky_revenue": str(pnl.sky_revenue),
            "agent_rate": str(pnl.agent_rate),
            "prime_agent_revenue": str(pnl.prime_agent_revenue),
            "monthly_pnl": str(pnl.monthly_pnl),
        },
        "venue_breakdown": [
            {
                "venue_id": v.venue_id,
                "label": v.label,
                "value_som": str(v.value_som),
                "value_eom": str(v.value_eom),
                "period_inflow": str(v.period_inflow),
                "revenue": str(v.revenue),
            }
            for v in pnl.venue_breakdown
        ],
        "sources": sources or {},
    }


def write_provenance(
    pnl: MonthlyPnL,
    dest: Path,
    *,
    sources: dict[str, str] | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = render_provenance(pnl, sources=sources)
    dest.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return dest
