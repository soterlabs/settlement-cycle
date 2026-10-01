"""Daily ownership-weighted Basin cash, limited to its allocator compartment."""
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pandas as pd

from ..domain.period import Period
from ..domain.primes import Chain, Prime
from ..extract import basin as rpc
from .protocols import IBlockResolver


def get_basin_idle_usds(
    prime: Prime, period: Period, *, block_resolver: IBlockResolver,
) -> pd.DataFrame:
    """Exclude actual idle USDS only, beginning at the configured effective date.

    Do not exempt invested collateral, totalAssets(), USDC, or direct ALM cash.
    All reads use the same EOD block. No RPC-error carry-forward: a stale cash
    balance could over-exempt debt after investment or redemption.

    Grove's September 1, 2026 policy uses ALLOCATOR-GROVE-A, not BLOOM-A.
    The cap is applied once across both Basins before the aggregate borrowing
    calculation, which retains the prime's single subsidy tranche.
    """
    columns = ["block_date", "cum_balance", "ilk_debt"]
    cfg = prime.basin_idle_usds
    if cfg is None or period.end < cfg.effective_from:
        return pd.DataFrame(columns=columns)
    rows = []
    day = period.start
    while day <= period.end:
        idle = debt = Decimal(0)
        if day >= cfg.effective_from:
            block = block_resolver.block_at_or_before(
                Chain.ETHEREUM.value, datetime.combine(day, time.max, UTC),
            )
            debt = rpc.ilk_debt(cfg.ilk, block)
            seen: set[str] = set()
            for basin in cfg.basins:
                snapshot = rpc.idle_usds(basin, cfg.holder, block)
                holders = set(snapshot["balances"])
                # A shared pocket would require explicit attribution between
                # Basins. Reject it rather than silently counting cash twice.
                if seen & holders:
                    raise ValueError("Basin idle holders overlap across configured Basins")
                seen.update(holders)
                if snapshot["total_shares"]:
                    cash = Decimal(sum(snapshot["balances"].values())) / Decimal(10**18)
                    idle += cash * Decimal(snapshot["shares"]) / Decimal(snapshot["total_shares"])
            idle = min(idle, debt)
        rows.append({"block_date": day, "cum_balance": idle, "ilk_debt": debt})
        day += timedelta(days=1)
    return pd.DataFrame(rows, columns=columns)
