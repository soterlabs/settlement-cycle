"""Canonical debt timeseries primitive."""

from __future__ import annotations

import pandas as pd

from ..domain.period import Period
from ..domain.primes import Chain, Prime
from ..validation.schemas import assert_columns
from .protocols import IDebtSource
from .registry import get_debt_source


def get_debt_timeseries(
    prime: Prime,
    period: Period,
    *,
    source: IDebtSource | None = None,
) -> pd.DataFrame:
    """Daily cumulative ilk debt for `prime`, from `prime.start_date` through
    ``period.pin_blocks[ethereum]``.

    Returns DataFrame[block_date, daily_dart, cum_debt]. Compute slices to
    period bounds (SoM / EoM).
    """
    if Chain.ETHEREUM not in period.pin_blocks:
        raise ValueError(
            "Period must have an ethereum pin_block; got "
            f"chains={sorted(period.pin_blocks)}"
        )
    src = source if source is not None else get_debt_source()
    df = src.debt_timeseries(
        ilk=prime.ilk_bytes32,
        start=prime.start_date,
        pin_block=period.pin_blocks[Chain.ETHEREUM],
    )
    assert_columns(df, ["block_date", "daily_dart", "cum_debt"])
    return df
