"""Subsidised borrowing rate — daily reference rates and ramp formula.

Per debt-rate-methodology Step 1 (subsidy):

    subsidised_apy_d = ref_rate_d + (base_apy_d − ref_rate_d) × T / 24

where:
    ref_rate_d  = NY Fed EFFR or 3M T-Bill on date d (carry-forward)
    base_apy_d  = SSR_d + 30bps (the un-subsidised borrow rate)
    T           = months elapsed since the subsidy program start
                  (Sky governance: 2026-01-01)

The subsidy is capped: only the first ``subsidy_cap_usd`` of utilized USDS
is charged at the subsidised rate; any utilized excess is charged at the
full base rate. Default cap = $1,000,000,000.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import yaml

_log = logging.getLogger(__name__)
_VALID_REF_RATE_KINDS = ("tbill_3m", "effr")

# Subsidy program kicked in 2026-01-01; T=0 in Jan, T=1 in Feb, ... T=24+ → no subsidy.
SUBSIDY_PROGRAM_START = date(2026, 1, 1)
SUBSIDY_RAMP_MONTHS = 24
DEFAULT_SUBSIDY_CAP_USD = Decimal("1000000000")


@dataclass(frozen=True, slots=True)
class SubsidyConfig:
    """Per-prime subsidy parameters loaded from YAML.

    ``ref_rate_kind`` selects which reference rate this prime uses in the
    subsidy formula. Sky governance (2026-05-02): Grove uses 3-month T-Bill,
    Spark uses EFFR. Both columns live in
    ``config/subsidy_reference_rates.yaml``.
    """

    enabled: bool
    cap_usd: Decimal = DEFAULT_SUBSIDY_CAP_USD
    program_start: date = SUBSIDY_PROGRAM_START
    ramp_months: int = SUBSIDY_RAMP_MONTHS
    ref_rate_kind: str = "tbill_3m"   # 'tbill_3m' | 'effr'

    @classmethod
    def from_dict(cls, d: dict | None) -> "SubsidyConfig":
        if not d:
            return cls(enabled=False)
        kind = d.get("ref_rate_kind", "tbill_3m")
        if kind not in _VALID_REF_RATE_KINDS:
            raise ValueError(
                f"Invalid subsidy.ref_rate_kind {kind!r}; expected one of {_VALID_REF_RATE_KINDS}"
            )
        program_start = (
            date.fromisoformat(d["program_start"])
            if "program_start" in d else SUBSIDY_PROGRAM_START
        )
        return cls(
            enabled=bool(d.get("enabled", True)),
            cap_usd=Decimal(str(d.get("cap_usd", DEFAULT_SUBSIDY_CAP_USD))),
            program_start=program_start,
            ramp_months=int(d.get("ramp_months", SUBSIDY_RAMP_MONTHS)),
            ref_rate_kind=kind,
        )


@dataclass(frozen=True, slots=True)
class ReferenceRateHistory:
    """Daily reference-rate timeseries for the subsidy formula.

    ``rates`` is a DataFrame[effective_date, ref_rate_apy] sorted by date.
    Lookups use carry-forward (most recent rate ≤ target date).
    """

    rates: pd.DataFrame
    kind: str  # 'effr' | 'tbill_3m'

    # Beyond this carry-forward span (calendar days) emit a loud warning —
    # rates moved often enough that quietly using a 3-week-old value is
    # almost always a forgotten config update, not an intentional choice.
    _STALE_CARRY_FORWARD_DAYS = 21

    def at(self, target: date) -> Decimal:
        eligible = self.rates[self.rates["effective_date"] <= target]
        if eligible.empty:
            raise ValueError(
                f"No reference rate found ≤ {target}. "
                f"Earliest entry: {self.rates['effective_date'].min()}."
            )
        idx = eligible["effective_date"].idxmax()
        latest = eligible.loc[idx, "effective_date"]
        stale_days = (target - latest).days
        if stale_days > self._STALE_CARRY_FORWARD_DAYS:
            _log.warning(
                "Reference rate (%s) for %s carried forward from %s "
                "(%d days stale). Update config/subsidy_reference_rates.yaml.",
                self.kind, target, latest, stale_days,
            )
        return Decimal(str(eligible.loc[idx, "ref_rate_apy"]))


def load_reference_rates(
    kind: str = "tbill_3m",
    config_path: Path | None = None,
) -> ReferenceRateHistory:
    """Load `config/subsidy_reference_rates.yaml` for the given rate kind.

    The YAML file carries both ``tbill_3m_apy`` and ``effr_apy`` columns;
    callers pick which series to read via ``kind``. Defaults to T-Bill 3M.
    """
    if kind not in _VALID_REF_RATE_KINDS:
        raise ValueError(f"Unknown ref_rate kind {kind!r} ({'|'.join(_VALID_REF_RATE_KINDS)})")
    if config_path is None:
        config_path = (
            Path(__file__).resolve().parents[3]
            / "config" / "subsidy_reference_rates.yaml"
        )
    with config_path.open() as f:
        cfg = yaml.safe_load(f)

    col = "effr_apy" if kind == "effr" else "tbill_3m_apy"
    rows = [
        {
            "effective_date": date.fromisoformat(r["effective_date"]),
            "ref_rate_apy": Decimal(str(r[col])),
        }
        for r in cfg["rates"]
    ]
    df = pd.DataFrame(rows).sort_values("effective_date").reset_index(drop=True)
    return ReferenceRateHistory(rates=df, kind=kind)


def months_elapsed_since(d: date, anchor: date = SUBSIDY_PROGRAM_START) -> int:
    """Whole-month index since ``anchor``. Jan 2026 → 0, Feb 2026 → 1, ...

    Uses calendar months (start-of-month boundary), not 30-day buckets, so
    Feb 1 → T=1, Feb 28 → T=1, Mar 1 → T=2.
    """
    if d < anchor:
        return 0
    return (d.year - anchor.year) * 12 + (d.month - anchor.month)


def subsidised_apy(
    base_apy: Decimal,
    ref_rate_apy: Decimal,
    months_elapsed: int,
    ramp_months: int = SUBSIDY_RAMP_MONTHS,
) -> Decimal:
    """``ref_rate + (base − ref_rate) × T / 24``, clamped at base_apy.

    At T=0: subsidised_apy = ref_rate (full subsidy).
    At T=24: subsidised_apy = base_apy (no subsidy).

    Clamp guards the case where ``ref_rate ≥ base_apy`` (e.g. EFFR ≥ BR for
    Spark in some periods) — without it the linear interpolation would give
    a result *above* base_apy, charging the prime more than the unsubsidised
    rate. The subsidy intent is one-sided: the prime never pays more than BR.
    """
    t = max(0, min(months_elapsed, ramp_months))
    spread = base_apy - ref_rate_apy
    raw = ref_rate_apy + spread * Decimal(t) / Decimal(ramp_months)
    return min(base_apy, raw)
