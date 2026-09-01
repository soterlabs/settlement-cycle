"""Subsidised borrowing rate — daily reference rates and ramp formula.

Per debt-rate-methodology Step 1 (subsidy):

    subsidised_apr_d = ref_rate_d + (base_apr_d − ref_rate_d) × T / 24

where:
    ref_rate_d  = the prime's reference rate on date d (carry-forward):
                  3M T-Bill through 2026-07-22, SOFR from 2026-07-23
                  (the same Stability Scope change that cut SSR to 3.52%
                  and the BR−SSR spread to 20bps switched the subsidy
                  reference series to SOFR)
    base_apr_d  = apy_to_apr(SSR_d, 12) + spread_d (the un-subsidised rate;
                  spread 30bps, 20bps from 2026-07-23)
    T           = months elapsed since the subsidy program start
                  (Sky governance: 2026-01-01)

The dated series switch is expressed in the prime YAML as a list-valued
``ref_rate_kind`` (see ``SubsidyConfig.from_dict``); a scalar string keeps
the legacy single-series behaviour.

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
_VALID_REF_RATE_KINDS = ("tbill_3m", "sofr")

# YAML column per series. The suffix encodes the rate's NATURE, not a naming
# convention: SOFR is published by the NY Fed as an annualised SIMPLE rate
# and the Atlas defines it as "expressed as an annual rate", so it is an APR
# used as published. The 3M T-Bill column keeps its ``_apy`` suffix — it
# stopped being the subsidy reference on 2026-07-23.
#
# NB the COLUMN NAME is what is unchanged, not the semantics: whatever value
# ``at()`` returns is now sliced ``rate/365`` by ``subsidised_apr`` instead of
# ``(1+rate)^(1/365)-1``, so re-running a Jan–Jul 2026 month with current code
# re-prices its T-Bill-referenced subsidy (~1.8% higher on the reference leg).
# Those months are frozen by NOT being re-run, not by this column being
# immune. Re-typing it is only worth doing if they are ever restated.
_REF_RATE_COLUMNS = {"tbill_3m": "tbill_3m_apy", "sofr": "sofr_apr"}

# Subsidy program kicked in 2026-01-01; T=0 in Jan, T=1 in Feb, ... T=24+ → no subsidy.
SUBSIDY_PROGRAM_START = date(2026, 1, 1)
SUBSIDY_RAMP_MONTHS = 24
DEFAULT_SUBSIDY_CAP_USD = Decimal("1000000000")


@dataclass(frozen=True, slots=True)
class SubsidyConfig:
    """Per-prime subsidy parameters loaded from YAML.

    ``ref_rate_kind`` selects which reference rate this prime uses in the
    subsidy formula. In YAML it is either a scalar kind (``tbill_3m`` /
    ``sofr``, single series for the whole program) or a list of dated
    entries for a mid-program series switch::

        ref_rate_kind:
          - { kind: tbill_3m, from: '2026-01-01' }
          - { kind: sofr,     from: '2026-07-23' }

    The list form populates ``ref_rate_schedule`` (sorted ``(from, kind)``
    pairs; each kind applies from its ``from`` date, inclusive, until the
    next entry) and sets ``ref_rate_kind`` to a human-readable label (e.g.
    ``tbill_3m→sofr@2026-07-23``) used in provenance / the xlsx panel.
    The series live as per-kind columns in
    ``config/subsidy_reference_rates.yaml``.
    """

    enabled: bool
    cap_usd: Decimal = DEFAULT_SUBSIDY_CAP_USD
    program_start: date = SUBSIDY_PROGRAM_START
    ramp_months: int = SUBSIDY_RAMP_MONTHS
    ref_rate_kind: str = "tbill_3m"   # scalar kind, or label when scheduled
    ref_rate_schedule: tuple[tuple[date, str], ...] = ()  # dated (from, kind)

    @classmethod
    def from_dict(cls, d: dict | None) -> "SubsidyConfig":
        if not d:
            return cls(enabled=False)
        raw_kind = d.get("ref_rate_kind", "tbill_3m")
        schedule: tuple[tuple[date, str], ...] = ()
        if isinstance(raw_kind, str):
            kind = raw_kind
            if kind not in _VALID_REF_RATE_KINDS:
                raise ValueError(
                    f"Invalid subsidy.ref_rate_kind {kind!r}; "
                    f"expected one of {_VALID_REF_RATE_KINDS}"
                )
        else:
            entries = []
            for e in raw_kind:
                k = e["kind"]
                if k not in _VALID_REF_RATE_KINDS:
                    raise ValueError(
                        f"Invalid subsidy.ref_rate_kind entry {k!r}; "
                        f"expected one of {_VALID_REF_RATE_KINDS}"
                    )
                entries.append((date.fromisoformat(str(e["from"])), k))
            if not entries:
                raise ValueError("subsidy.ref_rate_kind list is empty")
            if entries != sorted(entries, key=lambda x: x[0]):
                raise ValueError(
                    "subsidy.ref_rate_kind entries must be in ascending "
                    f"'from' order: {entries}"
                )
            schedule = tuple(entries)
            kind = entries[0][1] + "".join(
                f"→{k}@{frm.isoformat()}" for frm, k in entries[1:]
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
            ref_rate_schedule=schedule,
        )


@dataclass(frozen=True, slots=True)
class ReferenceRateHistory:
    """Daily reference-rate timeseries for the subsidy formula.

    ``rates`` is a DataFrame[effective_date, ref_rate_apr] sorted by date.

    NOMINAL (APR) since 2026-09-01: SOFR is published by the NY Fed as an
    annualised simple rate and the Atlas defines it as "expressed as an
    annual rate", so it is used as published with no APY conversion. Rows
    before 2026-07-23 carry 3M T-Bill values that were captured under the
    old APY reading — left as-is, since those months are settled and not
    restated (scope: going forward only).
    Lookups use carry-forward (most recent rate ≤ target date).
    """

    rates: pd.DataFrame
    kind: str  # 'tbill_3m'

    # Beyond this carry-forward span (calendar days) emit a loud warning —
    # rates moved often enough that quietly using a 3-week-old value is
    # almost always a forgotten config update, not an intentional choice.
    _STALE_CARRY_FORWARD_DAYS = 21
    # Beyond this span, fail loud rather than silently feed a stale rate
    # into the subsidy formula — a 30+-day-old reference rate is almost
    # certainly a forgotten YAML update and silently using it produces a
    # materially wrong sky_revenue for the entire month.
    _STALE_CARRY_FORWARD_FATAL_DAYS = 45

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
        if stale_days > self._STALE_CARRY_FORWARD_FATAL_DAYS:
            raise ValueError(
                f"Reference rate ({self.kind}) for {target} carried forward "
                f"from {latest} ({stale_days} days stale, > "
                f"{self._STALE_CARRY_FORWARD_FATAL_DAYS}d fatal threshold). "
                "Update config/subsidy_reference_rates.yaml with rates for "
                "the missing days — silently using a month-old rate would "
                "materially mis-price the subsidy."
            )
        if stale_days > self._STALE_CARRY_FORWARD_DAYS:
            _log.warning(
                "Reference rate (%s) for %s carried forward from %s "
                "(%d days stale). Update config/subsidy_reference_rates.yaml.",
                self.kind, target, latest, stale_days,
            )
        return Decimal(str(eligible.loc[idx, "ref_rate_apr"]))

    def warn_if_period_end_missing(self, period_end: date) -> None:
        """Warn when the LAST day of a settlement period has no own-date row.

        The staleness thresholds above are calendar-span based, so a
        business-day print that publishes a day or two late (2026-08-31
        publishes on 09-01) slips through silently: the final day of the
        period accrues at the previous print with nothing in the log. That
        day carries full weight in the month's charge, and with the subsidy
        headroom now under 2 bps a single print can decide whether the ramp
        clamps — so the period boundary gets its own check.
        """
        if self.rates.empty:
            return
        if not (self.rates["effective_date"] == period_end).any():
            eligible = self.rates[self.rates["effective_date"] <= period_end]
            latest = eligible["effective_date"].max() if not eligible.empty else None
            _log.warning(
                "Reference rate (%s): no own-date row for the period's LAST "
                "day %s — it carries forward from %s. If that print has since "
                "published, add it to config/subsidy_reference_rates.yaml "
                "before settling this period.",
                self.kind, period_end, latest,
            )


@dataclass(frozen=True, slots=True)
class ScheduledReferenceRateHistory:
    """Date-dispatched composite of per-kind ``ReferenceRateHistory``.

    ``schedule`` is sorted ``(from_date, kind)`` pairs — each kind applies
    from its ``from_date`` (inclusive) until the next entry. ``at(target)``
    dispatches to that kind's OWN full history, so carry-forward around a
    switch date never crosses series (a Saturday right after the switch
    reads the new series' latest print, not the old series').

    Duck-types ``ReferenceRateHistory`` for the compute layer: only
    ``at(date)`` and ``kind`` (a human label) are consumed there.
    """

    histories: dict[str, ReferenceRateHistory]
    schedule: tuple[tuple[date, str], ...]
    kind: str  # human label, e.g. 'tbill_3m→sofr@2026-07-23'

    def kind_at(self, target: date) -> str:
        active = self.schedule[0][1]
        for frm, k in self.schedule:
            if frm <= target:
                active = k
            else:
                break
        return active

    def at(self, target: date) -> Decimal:
        return self.histories[self.kind_at(target)].at(target)

    def warn_if_period_end_missing(self, period_end: date) -> None:
        """Delegate to whichever series is active on the period's last day."""
        self.histories[self.kind_at(period_end)].warn_if_period_end_missing(
            period_end,
        )


def load_reference_rates(
    kind: str = "tbill_3m",
    config_path: Path | None = None,
) -> ReferenceRateHistory:
    """Load `config/subsidy_reference_rates.yaml` for the given rate kind.

    The YAML carries one column per series. The suffix encodes the rate's
    NATURE, which differs by series: ``sofr_apr`` (NY Fed publishes SOFR as
    an annualised simple rate; the Atlas defines it as "expressed as an
    annual rate") and ``tbill_3m_apy`` (unchanged — the T-Bill stopped
    being the subsidy reference on 2026-07-23, and its months are settled
    and not restated). Rows that lack the requested column are skipped —
    the series don't have to cover the same date ranges.
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

    col = _REF_RATE_COLUMNS[kind]
    # A row missing the REQUESTED column is legitimate (the two series need
    # not cover the same dates), but a row carrying NO known rate column at
    # all is a typo (e.g. ``tbil_3m_apy``) — the old per-row ``r[col]`` read
    # failed loud on those; keep that property rather than silently dropping
    # the day into the carry-forward.
    known_cols = set(_REF_RATE_COLUMNS.values())
    for r in cfg["rates"]:
        if not (known_cols & r.keys()):
            raise ValueError(
                f"{config_path}: rates row for {r.get('effective_date')!r} has "
                f"none of the known rate columns {sorted(known_cols)} — "
                "probable column-name typo; the day would silently fall into "
                "carry-forward."
            )
    rows = [
        {
            "effective_date": date.fromisoformat(r["effective_date"]),
            "ref_rate_apr": Decimal(str(r[col])),
        }
        for r in cfg["rates"]
        if col in r
    ]
    if not rows:
        raise ValueError(
            f"No rows with column {col!r} in {config_path} — add the "
            f"{kind} series before enabling it in a prime's subsidy config."
        )
    df = pd.DataFrame(rows).sort_values("effective_date").reset_index(drop=True)
    return ReferenceRateHistory(rates=df, kind=kind)


def load_reference_rates_for(
    subsidy: SubsidyConfig,
    config_path: Path | None = None,
) -> ReferenceRateHistory | ScheduledReferenceRateHistory:
    """Reference-rate history matching ``subsidy.ref_rate_schedule``.

    Single-kind configs return the plain ``ReferenceRateHistory``; scheduled
    configs return the date-dispatching composite.
    """
    if not subsidy.ref_rate_schedule:
        return load_reference_rates(subsidy.ref_rate_kind, config_path)
    kinds = {k for _, k in subsidy.ref_rate_schedule}
    return ScheduledReferenceRateHistory(
        histories={k: load_reference_rates(k, config_path) for k in kinds},
        schedule=subsidy.ref_rate_schedule,
        kind=subsidy.ref_rate_kind,
    )


def months_elapsed_since(d: date, anchor: date = SUBSIDY_PROGRAM_START) -> int:
    """Whole-month index since ``anchor``. Jan 2026 → 0, Feb 2026 → 1, ...

    Uses calendar months (start-of-month boundary), not 30-day buckets, so
    Feb 1 → T=1, Feb 28 → T=1, Mar 1 → T=2.
    """
    if d < anchor:
        return 0
    return (d.year - anchor.year) * 12 + (d.month - anchor.month)


def subsidised_apr(
    base_apr: Decimal,
    ref_rate_apr: Decimal,
    months_elapsed: int,
    ramp_months: int = SUBSIDY_RAMP_MONTHS,
) -> Decimal:
    """``ref_rate + (base − ref_rate) × T / 24``, clamped at the base rate.

    Since 2026-09-01 every argument is NOMINAL (APR): ``base`` is
    ``apy_to_apr(SSR, 12) + spread`` and the reference rate is used as
    published. The interpolation is plain arithmetic on rate numbers, so it
    is unchanged by the units switch — only what the numbers mean changed.

    At T=0: subsidised_apr = ref_rate (full subsidy).
    At T=24: subsidised_apr = base_apr (no subsidy).

    Clamp guards the case where ``ref_rate ≥ base_apr`` (e.g. T-Bill ≥ BR for
    Spark in some periods) — without it the linear interpolation would give
    a result *above* base_apr, charging the prime more than the unsubsidised
    rate. The subsidy intent is one-sided: the prime never pays more than BR.
    """
    t = max(0, min(months_elapsed, ramp_months))
    spread = base_apr - ref_rate_apr
    raw = ref_rate_apr + spread * Decimal(t) / Decimal(ramp_months)
    return min(base_apr, raw)
