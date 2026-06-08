"""Canonical debt timeseries primitive."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pandas as pd

from ..domain.period import Period
from ..domain.primes import Address, Chain, Prime
from ..validation.schemas import assert_columns
from .protocols import IBlockResolver, IDebtSource
from .registry import get_debt_source

# MakerDAO Vat — same constant as snapshot/compute.py; duplicated here to
# avoid a cross-layer import (normalize must not import from compute/snapshot).
_VAT = Address.from_str("0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B")
_RAY = Decimal(10 ** 27)


def _art_at_or_before(df: pd.DataFrame, d: date) -> Decimal:
    """Carry-forward of the raw normalised Art at or before ``d``."""
    mask = df["block_date"] <= d
    if not mask.any():
        return Decimal("0")
    return df.loc[mask, "cum_debt"].iloc[-1]


def get_debt_timeseries(
    prime: Prime,
    period: Period,
    *,
    source: IDebtSource | None = None,
    block_resolver: IBlockResolver | None = None,
) -> pd.DataFrame:
    """Daily cumulative ilk debt for ``prime`` over ``period``.

    Returns DataFrame[block_date, daily_dart, cum_debt] where ``cum_debt``
    is the actual outstanding USDS (``Vat.ilks(ilk).Art × rate``), not raw
    normalised Art.

    The Dune source returns ``Σ dart = Art`` (normalised, wad/1e18).  This
    function multiplies by the Vat rate index so the BR principal is correct.

    **Daily precision (preferred):** when ``block_resolver`` is supplied, the
    function expands the sparse Dune series into one row per calendar day in
    ``[period.start, period.end]``.  For each day it reads ``ilk.rate`` at
    that day's EoD block via RPC (cached after first run — ~28 calls/month).
    ``daily_dart`` is derived as ``cum_debt_d − cum_debt_{d-1}``, capturing
    both frob/grab activity and the daily rate accrual on existing principal.

    **EoM approximation (fallback):** when no resolver is provided, the EoM
    rate is applied uniformly to the sparse series.  Daily rate error ≤ 0.3%
    for a monthly period — negligible vs. the ~4.5% systematic correction for
    ALLOCATOR-SPARK-A, but imprecise. Prefer always passing ``block_resolver``.

    For ALLOCATOR-BLOOM-A ``rate = 1.0`` always; both paths are no-ops there.
    """
    if Chain.ETHEREUM not in period.pin_blocks:
        raise ValueError(
            "Period must have an ethereum pin_block; got "
            f"chains={sorted(period.pin_blocks)}"
        )
    src = source if source is not None else get_debt_source()
    # Sparse Dune series: one row per day with frob/grab activity.
    # cum_debt here = Art (normalised), NOT actual USDS yet.
    sparse = src.debt_timeseries(
        ilk=prime.ilk_bytes32,
        start=prime.start_date,
        pin_block=period.pin_blocks[Chain.ETHEREUM],
    )
    assert_columns(sparse, ["block_date", "daily_dart", "cum_debt"])

    from ..extract.rpc import ilk_rate as _ilk_rate

    if block_resolver is not None:
        # Daily expansion: one row per calendar day, rate read at EoD block.
        rows = []
        prev_cum: Decimal = Decimal("0")
        current = period.start
        while current <= period.end:
            art_d = _art_at_or_before(sparse, current)
            eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
            block_d = block_resolver.block_at_or_before(Chain.ETHEREUM.value, eod)
            rate_raw = _ilk_rate(Chain.ETHEREUM, _VAT, prime.ilk_bytes32, block_d)
            cum_d = art_d * Decimal(rate_raw) / _RAY
            rows.append({
                "block_date": current,
                "daily_dart": cum_d - prev_cum,
                "cum_debt":   cum_d,
            })
            prev_cum = cum_d
            current += timedelta(days=1)
        return pd.DataFrame(rows)

    # Fallback: EoM rate applied uniformly to all rows in the sparse series.
    rate_raw = _ilk_rate(
        Chain.ETHEREUM, _VAT, prime.ilk_bytes32,
        period.pin_blocks[Chain.ETHEREUM],
    )
    rate = Decimal(rate_raw) / _RAY
    if rate != Decimal("1"):
        sparse["cum_debt"]   = sparse["cum_debt"]   * rate
        sparse["daily_dart"] = sparse["daily_dart"] * rate
    return sparse
