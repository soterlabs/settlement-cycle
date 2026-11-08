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
    columns = ["block_date", "cum_balance", "ilk_debt", "idle_by_holder", "other_owners_by_holder"]
    cfg = prime.basin_idle_usds
    if cfg is None or period.end < cfg.effective_from:
        return pd.DataFrame(columns=columns)
    rows = []
    day = period.start
    while day <= period.end:
        idle = debt = Decimal(0)
        weights: dict[str, Decimal] = {}
        other_owners: dict[str, Decimal] = {}
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
                    for holder, amount in snapshot["balances"].items():
                        weights[holder] = (Decimal(amount) / Decimal(10**18)
                                           * Decimal(snapshot["shares"])
                                           / Decimal(snapshot["total_shares"]))
                for holder, amount in snapshot["balances"].items():
                    other_owners[holder] = Decimal(amount) / Decimal(10**18) - weights.get(holder, Decimal(0))
            idle = min(idle, debt)
        # Attribute the SAME capped exemption to the cash holders for report
        # allocation. Keep the aggregate calculation above unchanged. A final
        # residual avoids accumulating Decimal division noise across holders.
        attributed: dict[str, Decimal] = {}
        positive = sorted((holder, value) for holder, value in weights.items() if value > 0)
        total = sum((value for _, value in positive), Decimal(0))
        remaining = idle
        for index, (holder, value) in enumerate(positive):
            portion = remaining if index == len(positive) - 1 else idle * value / total
            attributed[holder] = portion
            remaining -= portion
        rows.append({"block_date": day, "cum_balance": idle, "ilk_debt": debt,
                     "idle_by_holder": attributed, "other_owners_by_holder": other_owners})
        day += timedelta(days=1)
    return pd.DataFrame(rows, columns=columns)


def basin_cash_by_venue(prime: Prime, period: Period, daily: pd.DataFrame) -> dict[str, dict[str, Decimal]]:
    """Average exempt cash and other owners' cash at each raw-USDS venue.

    Match actual cash holder addresses, not Basin invested assets or E41 by
    name. Unrepresented pockets remain unattributed, never double-counted.
    """
    if prime.basin_idle_usds is None or period.end < prime.basin_idle_usds.effective_from:
        return {}
    expected = {period.start + timedelta(days=i) for i in range(period.n_days)}
    if len(daily) != period.n_days or set(daily['block_date']) != expected:
        raise ValueError('Incomplete daily Basin attribution')
    holders = {}
    for venue in prime.venues:
        if (venue.chain != Chain.ETHEREUM or venue.token.address != rpc.USDS
                or venue.skip or not venue.settlement_enabled or venue.display_only):
            continue
        holder = (venue.holder_override or prime.alm[venue.chain]).hex
        if holder in holders:
            raise ValueError('Duplicate raw-USDS venue holder for Basin attribution')
        holders[holder] = venue.id
    totals: dict[str, dict[str, Decimal]] = {}
    for _, row in daily.iterrows():
        balances = row['idle_by_holder']
        if any(not value.is_finite() or value < 0 for value in balances.values()):
            raise ValueError('Invalid Basin holder deduction')
        if abs(sum(balances.values(), Decimal(0)) - row['cum_balance']) > Decimal('1e-18'):
            raise ValueError('Basin holder deductions do not match aggregate exemption')
        for key, column in [('idle_exempt', 'idle_by_holder'), ('other_owners', 'other_owners_by_holder')]:
            for holder, value in row[column].items():
                if not value.is_finite() or value < 0:
                    raise ValueError('Invalid Basin cash ownership')
                if holder in holders:
                    vid = holders[holder]
                    amounts = totals.setdefault(vid, {'idle_exempt': Decimal(0), 'other_owners': Decimal(0)})
                    amounts[key] += value
    for amounts in totals.values():
        for key in amounts:
            amounts[key] /= Decimal(period.n_days)
    return totals
