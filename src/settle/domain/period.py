"""Period & Month — calendar windowing for a settlement run.

Convention from RULES.md and SETTLEMENT_ARCHITECTURE.md:
- Settlement is monthly.
- A `Period` pins a `block_number` per chain (the EOD block of `end`), used for
  *every* Dune query (`WHERE block_number <= :pin`) and *every* RPC call (`block=:pin`).
- Period bounds are `[start, end]` inclusive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Self

from .primes import Chain


@dataclass(frozen=True, slots=True)
class Month:
    """A calendar month (UTC). Construct from `Month.parse('2026-04')` or `Month(2026, 4)`."""

    year: int
    month: int

    @classmethod
    def parse(cls, s: str) -> Self:
        """Parse `YYYY-MM` or `YYYY-MM-DD` (the day is ignored)."""
        parts = s.split("-")
        if len(parts) < 2:
            raise ValueError(f"Month must be 'YYYY-MM' or 'YYYY-MM-DD'; got {s!r}")
        return cls(int(parts[0]), int(parts[1]))

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        if self.month == 12:
            return date(self.year + 1, 1, 1) - timedelta(days=1)
        return date(self.year, self.month + 1, 1) - timedelta(days=1)

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


@dataclass(frozen=True, slots=True)
class Period:
    """Inclusive `[start, end]` date window with chain-pinned end blocks.

    `pin_blocks` must be populated before passing to Normalize/Compute. Use the
    block resolver in `settle.extract.rpc` to find the last block ≤ EOD UTC of `end`.
    """

    start: date
    end: date
    pin_blocks: dict[Chain, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError(f"start {self.start} > end {self.end}")

    @property
    def n_days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def end_eod_utc(self) -> datetime:
        """`end` at 23:59:59 UTC — the wall-clock anchor for `pin_blocks`."""
        return datetime.combine(self.end, datetime.max.time(), tzinfo=timezone.utc)

    @classmethod
    def from_month(cls, month: Month, pin_blocks: dict[Chain, int] | None = None) -> Self:
        return cls(month.first_day, month.last_day, pin_blocks or {})
