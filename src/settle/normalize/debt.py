"""Canonical debt timeseries primitive."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pandas as pd

from ..domain.period import Period
from ..domain.primes import Address, Chain, Prime
from ..validation.schemas import assert_columns
from .protocols import IBlockResolver, IDebtSource
from .registry import get_debt_source

_log = logging.getLogger(__name__)

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

    **Fallback (no resolver):** returns the raw sparse Dune frame unchanged.
    ``cum_debt`` then carries normalised Art (wad units), NOT USDS — a ~4.5%
    under-statement for ALLOCATOR-SPARK-A. This path is intended only for
    tests / one-off queries where rate precision doesn't matter; a
    ``logging.warning`` is emitted so production callers that forget to pass
    a resolver notice. For ALLOCATOR-BLOOM-A ``rate = 1.0`` always, so the
    distinction is moot.
    """
    if Chain.ETHEREUM not in period.pin_blocks:
        raise ValueError(
            "Period must have an ethereum pin_block; got "
            f"chains={sorted(period.pin_blocks)}"
        )
    if prime.ilk_bytes32 is None:
        # Agent-rate-only prime (Keel, Skybase): no allocator ilk → no debt.
        # Return an all-zero single-row series instead of querying Dune so
        # downstream consumers (``compute_sky_revenue``'s
        # ``require_non_empty`` guard, the BR base) see a legitimate
        # zero-debt prime rather than a misconfigured source.
        logging.getLogger(__name__).info(
            "get_debt_timeseries: prime %s has no ilk_bytes32 — "
            "agent-rate-only prime, returning zero-debt series.", prime.id,
        )
        return pd.DataFrame([{
            "block_date": period.start,
            "daily_dart": Decimal("0"),
            "cum_debt": Decimal("0"),
        }])
    src = source if source is not None else get_debt_source()
    # Sparse Dune series: one row per day with frob/grab activity.
    # cum_debt here = Art (normalised), NOT actual USDS yet.
    #
    # Multi-ilk primes (``prime.extra_ilks`` — Grove's Diamond PAU
    # compartment ALLOCATOR-GROVE-A alongside legacy ALLOCATOR-BLOOM-A):
    # each ilk gets its own sparse series and its OWN Vat rate in the daily
    # expansion; the rate-scaled cum_debt values are summed per day.
    ilks: list[bytes] = [prime.ilk_bytes32, *prime.extra_ilks]
    sparse_by_ilk = {}
    for _ilk in ilks:
        _sparse = src.debt_timeseries(
            ilk=_ilk,
            start=prime.start_date,
            pin_block=period.pin_blocks[Chain.ETHEREUM],
        )
        assert_columns(_sparse, ["block_date", "daily_dart", "cum_debt"])
        sparse_by_ilk[_ilk] = _sparse
    sparse = sparse_by_ilk[prime.ilk_bytes32]

    from ..extract.rpc import ilk_rate as _ilk_rate

    if block_resolver is not None:
        # Daily expansion: one row per calendar day, rate read at EoD block.
        rows = []
        prev_cum: Decimal = Decimal("0")
        current = period.start
        while current <= period.end:
            eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
            block_d = block_resolver.block_at_or_before(Chain.ETHEREUM.value, eod)
            cum_d = Decimal("0")
            for _ilk in ilks:
                art_d = _art_at_or_before(sparse_by_ilk[_ilk], current)
                if art_d == 0:
                    continue  # skip the rate read for a zero-Art ilk/day
                rate_raw = _ilk_rate(Chain.ETHEREUM, _VAT, _ilk, block_d)
                cum_d += art_d * Decimal(rate_raw) / _RAY
            rows.append({
                "block_date": current,
                "daily_dart": cum_d - prev_cum,
                "cum_debt":   cum_d,
            })
            prev_cum = cum_d
            current += timedelta(days=1)
        return pd.DataFrame(rows)

    # No block_resolver: return the raw normalised Art series without rate
    # scaling. Callers that need accurate USDS values (e.g. compute_monthly_pnl)
    # always supply a resolver; this path is used only in tests or one-off
    # queries where rate precision is not required. Warn loudly so any
    # production caller that forgets to pass a resolver sees the footgun.
    _log.warning(
        "get_debt_timeseries called without block_resolver for ilk=%s "
        "prime=%s — returning raw normalised Art (wad), NOT rate-scaled "
        "USDS. cum_debt will under-state actual debt by the accumulated "
        "Vat ilk rate (~4.5%% for ALLOCATOR-SPARK-A by early 2026).",
        prime.ilk_bytes32.hex(), prime.id,
    )
    if len(ilks) == 1:
        return sparse
    # Multi-ilk: merge the sparse Art frames so extra_ilks debt isn't
    # silently dropped on this path either. Per-date cum = Σ over ilks of
    # each ilk's carried-forward Art (unscaled — same wad convention as the
    # single-ilk return above); daily_dart = first difference.
    all_dates = sorted({
        d for f in sparse_by_ilk.values() for d in f["block_date"]
    })
    rows = []
    prev_cum = Decimal("0")
    for d in all_dates:
        cum_d = sum(
            (_art_at_or_before(f, d) for f in sparse_by_ilk.values()),
            Decimal("0"),
        )
        rows.append({
            "block_date": d,
            "daily_dart": cum_d - prev_cum,
            "cum_debt":   cum_d,
        })
        prev_cum = cum_d
    return pd.DataFrame(rows)
