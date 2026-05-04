"""Sky Direct Exposures (SDE) — config-driven, time-bounded table.

Replaces the per-venue ``Venue.sky_direct: bool`` flag with a single source
of truth at ``config/sky_direct_exposures.yaml``. The compute pipeline
queries this table per (prime_id, venue_id, date) to determine whether SDE
rules apply on that day.

SDE semantics (per debt-rate-methodology Step 4 + Atlas A.2.2.9.1.1.*):
- ``kind=fixed``  — whole venue revenue goes to Sky; prime keeps 0.
- ``kind=capped`` — first ``cap_usd`` of position is Sky's slice; revenue
                    is split proportionally. Grove keeps the rest.
- ``kind=pattern``— applies to a class of holdings (e.g. PSM3 USDC non-Eth).
                    Reserved for future plumbing; not consumed by compute today.

Loaded once per run (cheap YAML parse). The ``SDETable`` is immutable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

_log = logging.getLogger(__name__)
_VENUE_KINDS = ("fixed", "capped")


@dataclass(frozen=True, slots=True)
class SDEEntry:
    prime_id: str
    venue_id: str | None       # None for kind=pattern
    chain: str | None
    kind: str                  # 'fixed' | 'capped' | 'pattern'
    cap_usd: Decimal | None    # only for kind=capped
    pattern: str | None        # only for kind=pattern
    start_date: date
    end_date: date | None      # None = open-ended (still active)
    label: str
    source: str

    def is_active_on(self, d: date) -> bool:
        if d < self.start_date:
            return False
        if self.end_date is not None and d > self.end_date:
            return False
        return True

    def overlaps(self, period_start: date, period_end: date) -> bool:
        """True if this entry has ANY active day in [period_start, period_end]."""
        if period_end < self.start_date:
            return False
        if self.end_date is not None and period_start > self.end_date:
            return False
        return True


@dataclass(frozen=True, slots=True)
class SDETable:
    entries: tuple[SDEEntry, ...]

    def overlaps_venue(
        self,
        prime_id: str,
        venue_id: str,
        period_start: date,
        period_end: date,
    ) -> SDEEntry | None:
        """Venue's SDE entry active at ``period_start``, or None; raises on
        ambiguous (multiple active) config.

        Per Grove team's PnL workbook convention ``sd_share`` is locked at
        SoM and held constant for the whole month, so only the SoM-active
        entry is needed. ``period_end`` is accepted for symmetry with the
        general "overlap" semantics but is not consulted today.
        """
        matches = [
            e for e in self.entries
            if e.prime_id == prime_id
            and e.venue_id == venue_id
            and e.kind in _VENUE_KINDS
            and e.is_active_on(period_start)
        ]
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(
                f"Ambiguous SDE config: {len(matches)} entries match "
                f"prime={prime_id}, venue={venue_id}, date={period_start}: "
                f"{[e.label for e in matches]}"
            )
        return matches[0]


def load_sde_table(config_path: Path | None = None) -> SDETable:
    if config_path is None:
        config_path = (
            Path(__file__).resolve().parents[3]
            / "config" / "sky_direct_exposures.yaml"
        )
    if not config_path.exists():
        # Allow runs without the config (tests, mocked primes). Loud warning
        # so a CI/deploy that lost the file doesn't silently inflate sky_revenue.
        _log.warning(
            "SDE config not found at %s — proceeding with empty SDE table. "
            "Production runs require this file; missing it inflates sky_revenue.",
            config_path,
        )
        return SDETable(entries=())
    with config_path.open() as f:
        cfg = yaml.safe_load(f)

    raw_entries = [
        r
        for section in ("active", "historical")
        for r in (cfg.get(section) or [])
    ]

    entries: list[SDEEntry] = []
    for r in raw_entries:
        end_raw = r.get("end_date")
        cap_raw = r.get("cap_usd")
        entries.append(SDEEntry(
            prime_id=r["prime"],
            venue_id=r.get("venue_id"),
            chain=r.get("chain"),
            kind=r["kind"],
            cap_usd=Decimal(str(cap_raw)) if cap_raw is not None else None,
            pattern=r.get("pattern"),
            start_date=date.fromisoformat(r["start_date"]),
            end_date=date.fromisoformat(end_raw) if end_raw else None,
            label=r.get("label", ""),
            source=r.get("source", ""),
        ))

    for e in entries:
        if e.kind == "capped" and e.cap_usd is None:
            raise ValueError(f"SDE entry {e.label!r} has kind=capped but no cap_usd")
        if e.kind == "pattern" and e.pattern is None:
            raise ValueError(f"SDE entry {e.label!r} has kind=pattern but no pattern")

    # Pattern entries are reserved (see module docstring) — warn so an active
    # misconfig doesn't go unnoticed.
    active_patterns = [e.label for e in entries if e.kind == "pattern" and e.end_date is None]
    if active_patterns:
        _log.warning(
            "SDE pattern entries are not yet consumed by compute: %s. "
            "Their economics (e.g. PSM3 USDC non-Eth) are NOT applied.",
            active_patterns,
        )

    return SDETable(entries=tuple(entries))
