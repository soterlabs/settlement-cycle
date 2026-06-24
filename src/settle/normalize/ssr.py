"""Canonical SSR-history primitive."""

from __future__ import annotations

import logging

import pandas as pd

from ..domain.period import Period
from ..domain.primes import Chain, Prime
from ..domain.sky_tokens import SSR_HISTORY_ANCHOR
from ..validation.schemas import assert_columns
from .protocols import ISSRSource
from .registry import get_ssr_source

_log = logging.getLogger(__name__)

# Beyond this carry-forward span (calendar days from the last SSR change to
# period end) emit a loud warning. SSR genuinely can sit unchanged for long
# stretches, so this is NOT fatal — but a months-old "latest" rate on a
# current settlement is the signature of a stale SSR snapshot being reused
# (this is exactly what mis-priced the May 2026 Spark run: a Q1 fixture
# whose last SSR change was 2026-03-09 / 3.75%, silently carried into May
# when the on-chain rate had already stepped to 3.65% then 3.60%).
_SSR_STALE_WARN_DAYS = 45


def get_ssr_history(
    prime: Prime,
    period: Period,
    *,
    source: ISSRSource | None = None,
) -> pd.DataFrame:
    """SSR rate boundaries from SP-BEAM `file()` calls between
    :data:`SSR_HISTORY_ANCHOR` (Sky-protocol invariant) and
    ``period.pin_blocks[ethereum]``.

    `prime.start_date` is intentionally **not** used as the lower bound —
    SSR is global to Sky, and a prime's first month would otherwise return an
    empty DataFrame if no rate changed during that month. Raises if the prime
    pre-dates the anchor (caller must move the anchor back).
    """
    if Chain.ETHEREUM not in period.pin_blocks:
        raise ValueError("Period must have an ethereum pin_block")
    if prime.start_date < SSR_HISTORY_ANCHOR:
        raise ValueError(
            f"Prime {prime.id!r} starts {prime.start_date}, before "
            f"SSR_HISTORY_ANCHOR={SSR_HISTORY_ANCHOR}. Move the anchor in "
            "domain/sky_tokens.py back to cover this prime's launch date."
        )
    src = source if source is not None else get_ssr_source()
    df = src.ssr_history(
        start=SSR_HISTORY_ANCHOR,
        pin_block=period.pin_blocks[Chain.ETHEREUM],
    )
    assert_columns(df, ["effective_date", "ssr_apy"])

    # Staleness guard: the most recent SSR change should be reasonably close
    # to the settlement period. A latest-change date far before period end is
    # usually a reused/stale SSR snapshot rather than a genuinely flat rate —
    # verify against the on-chain rate at the pin block before trusting it.
    if not df.empty:
        eligible = df[df["effective_date"] <= period.end]
        if not eligible.empty:
            latest = eligible["effective_date"].max()
            stale_days = (period.end - latest).days
            if stale_days > _SSR_STALE_WARN_DAYS:
                latest_apy = eligible.loc[
                    eligible["effective_date"].idxmax(), "ssr_apy"
                ]
                _log.warning(
                    "SSR for period ending %s uses %.4f%% effective %s "
                    "(%d days stale). If SSR genuinely hasn't changed this is "
                    "fine — otherwise the SSR source is a stale snapshot (the "
                    "May 2026 Spark mis-pricing root cause). Verify against the "
                    "on-chain rate at the EoM pin block.",
                    period.end, float(latest_apy) * 100, latest, stale_days,
                )
    return df
