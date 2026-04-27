"""Canonical SSR-history primitive."""

from __future__ import annotations

import pandas as pd

from ..domain.period import Period
from ..domain.primes import Chain, Prime
from ..domain.sky_tokens import SSR_HISTORY_ANCHOR
from ..validation.schemas import assert_columns
from .protocols import ISSRSource
from .registry import get_ssr_source


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
    return df
