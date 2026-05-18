"""Unit tests for ``_shares_to_usd_inflow_timeseries`` bidirectional netting
of ``Venue.share_burn_destinations``.

The S26 fix nets share Transfers to/from queue contracts on both directions:

  * ALM → queue  (sign = -1): the redemption-request leg (Maple PoolV2 etc.)
  * queue → ALM  (sign = +1): the refund / cancellation / partial-fulfilment leg

Sign-flipping either direction silently moves S15 Spark Apr revenue by tens
of millions of dollars — this test locks both signs in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pandas as pd

from settle.domain.primes import (
    Address,
    Chain,
    CashDistributionSource,  # noqa: F401  (kept for import-cycle safety)
    PricingCategory,
    Prime,
    Token,
    Venue,
)
from settle.normalize.positions import _shares_to_usd_inflow_timeseries


_ALM     = Address.from_str("0x1601843c5e9bc251a3272907010afa41fa18347e")
_QUEUE   = Address.from_str("0x0cda32e08b48bfddbc7ee96b44b09cf286f9e21a")
_VAULT   = Address.from_str("0x356b8d89c1e1239cbbb9de4815c39a1474d5ba7d")
_USDT    = Address.from_str("0xdac17f958d2ee523a2206206994597c13d831ec7")


@dataclass
class _MockBalanceSource:
    """Deterministic ``IBalanceSource`` stub keyed by (from_addr, to_addr)."""

    flows: dict[tuple[bytes, bytes], pd.DataFrame]

    def directed_inflow_timeseries(
        self, *, chain, token, from_addr, to_addr, start, pin_block,
    ) -> pd.DataFrame:
        df = self.flows.get((from_addr, to_addr))
        if df is None:
            return pd.DataFrame(
                {"block_date": [], "daily_inflow": [], "cum_inflow": []},
            )
        return df


class _MockBlockResolver:
    def block_at_or_before(self, chain: str, anchor):
        # Use a deterministic block-per-date stub — the test's
        # ``_price_at_block`` ignores the block value.
        return 24000000

    def block_to_date(self, chain, block):
        return date(2026, 4, 1)


def _df(rows: list[tuple[date, int]]) -> pd.DataFrame:
    """Make a (block_date, daily_inflow, cum_inflow) frame from a list of
    (date, shares) tuples — shares are decimal-adjusted token units."""
    if not rows:
        return pd.DataFrame(
            {"block_date": [], "daily_inflow": [], "cum_inflow": []},
        )
    dates = [r[0] for r in rows]
    daily = [Decimal(r[1]) for r in rows]
    cum: list[Decimal] = []
    running = Decimal(0)
    for d in daily:
        running += d
        cum.append(running)
    return pd.DataFrame({
        "block_date":   dates,
        "daily_inflow": daily,
        "cum_inflow":   cum,
    })


def _venue_with_queue() -> Venue:
    return Venue(
        id="S15",
        chain=Chain.ETHEREUM,
        token=Token(chain=Chain.ETHEREUM, address=_VAULT, symbol="syrupUSDT", decimals=6),
        pricing_category=PricingCategory.ERC4626_VAULT,
        underlying=Token(chain=Chain.ETHEREUM, address=_USDT, symbol="USDT", decimals=6),
        share_burn_destinations=[_QUEUE],
    )


def _prime() -> Prime:
    return Prime(
        id="spark",
        ilk_bytes32=b"\x00" * 32,
        start_date=date(2025, 11, 1),
        alm={Chain.ETHEREUM: _ALM},
    )


class _FakePeriod:
    pin_blocks = {Chain.ETHEREUM: 24000000}


def _flat_price_at_block(_block) -> Decimal:
    """Stable pps = 1.0 so the test isolates the share-flow netting."""
    return Decimal("1.0")


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_burn_destination_netting_bidirectional():
    """Mints + ALM→queue burns + queue→ALM refunds → net inflow is correct."""
    venue = _venue_with_queue()
    prime = _prime()
    src = _MockBalanceSource({
        # 1654 mints adding 247.6M shares (simulated as a single day)
        (b"\x00" * 20, _ALM.value): _df([(date(2026, 4, 5), 247_600_000)]),
        # ALM → queue: 289.9M shares of redemption requests
        (_ALM.value, _QUEUE.value):  _df([(date(2026, 4, 10), 289_900_000)]),
        # queue → ALM: 21.5M shares refunded (cancellation / partial fulfill)
        (_QUEUE.value, _ALM.value):  _df([(date(2026, 4, 20), 21_500_000)]),
    })

    out = _shares_to_usd_inflow_timeseries(
        prime, venue, _FakePeriod(),
        balance_source=src,
        block_resolver=_MockBlockResolver(),
        price_at_block=_flat_price_at_block,
    )

    # Net inflow in shares (= USD here since pps=1.0):
    #   +247.6M (mints) − 289.9M (ALM→queue) + 21.5M (queue→ALM) = −20.8M
    # Row count: one row per active date — locks the structural shape so a
    # future refactor that collapses days into a single row gets caught.
    assert len(out) == 3
    assert out["cum_inflow"].iloc[-1] == Decimal("-20800000")
    # Per-day cumulatives lock in the sign of each leg, not just the total.
    cum_by_date = dict(zip(out["block_date"], out["cum_inflow"]))
    assert cum_by_date[date(2026, 4, 5)]  == Decimal("247600000")    # +mint
    assert cum_by_date[date(2026, 4, 10)] == Decimal("-42300000")    # +mint − burn
    assert cum_by_date[date(2026, 4, 20)] == Decimal("-20800000")    # +mint − burn + refund


def test_burn_destination_netting_no_refund_branch():
    """Only ALM→queue (no refund leg) → cum_inflow = mints − burns."""
    venue = _venue_with_queue()
    prime = _prime()
    src = _MockBalanceSource({
        (b"\x00" * 20, _ALM.value): _df([(date(2026, 4, 5), 100_000_000)]),
        (_ALM.value, _QUEUE.value):  _df([(date(2026, 4, 10), 30_000_000)]),
        # no queue→ALM flow
    })

    out = _shares_to_usd_inflow_timeseries(
        prime, venue, _FakePeriod(),
        balance_source=src,
        block_resolver=_MockBlockResolver(),
        price_at_block=_flat_price_at_block,
    )
    # +100M − 30M = +70M
    assert len(out) == 2  # one row per active date
    assert out["cum_inflow"].iloc[-1] == Decimal("70000000")


def test_no_burn_destinations_behaves_like_before():
    """Default empty ``share_burn_destinations`` → only mints/burns count."""
    venue = Venue(
        id="V_NO_QUEUE",
        chain=Chain.ETHEREUM,
        token=Token(chain=Chain.ETHEREUM, address=_VAULT, symbol="X", decimals=6),
        pricing_category=PricingCategory.ERC4626_VAULT,
        underlying=Token(chain=Chain.ETHEREUM, address=_USDT, symbol="USDT", decimals=6),
        # share_burn_destinations defaults to []
    )
    prime = _prime()
    src = _MockBalanceSource({
        (b"\x00" * 20, _ALM.value): _df([(date(2026, 4, 1), 50_000_000)]),
        (_ALM.value, b"\x00" * 20): _df([(date(2026, 4, 15), 10_000_000)]),
    })

    out = _shares_to_usd_inflow_timeseries(
        prime, venue, _FakePeriod(),
        balance_source=src,
        block_resolver=_MockBlockResolver(),
        price_at_block=_flat_price_at_block,
    )
    # +50M − 10M = +40M (no queue netting since destinations list is empty)
    assert len(out) == 2  # one row per active date
    assert out["cum_inflow"].iloc[-1] == Decimal("40000000")
