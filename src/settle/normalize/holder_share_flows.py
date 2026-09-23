"""Dated capital movements for non-rebasing, bridged Sky savings shares."""
from datetime import UTC, datetime, time
from decimal import Decimal, localcontext

import pandas as pd

from ..domain.primes import Chain, PricingCategory


def holder_share_inflows(prime, venue, period, *, balance_source, block_resolver,
                        price_at_block, opening_shares, closing_shares):
    """Price all holder-net Transfers, including ordinary PSM3 migrations.

    Spark S43's 2026-09-06 deposit transfers 80,894,745 sUSDS from the ALM
    into PSM3. Mint/burn-only accounting missed the entire capital outflow:
    https://arbiscan.io/tx/0x86956309b33b505cb5a3fcbec1960f98fd5112891dbda2cde82af1aa2e623b4d
    Do not synthesize an end-of-period balancing row: that would keep spread
    reimbursement accruing after the real exit. A source gap fails closed.
    This method is never applicable to rebasing aTokens/spTokens.
    """
    if (venue.pricing_category != PricingCategory.ERC4626_VAULT
            or not venue.sky_savings_token or venue.chain == Chain.ETHEREUM):
        raise ValueError("Holder share flows require non-rebasing L2 Sky savings tokens")
    holder = venue.holder_override or prime.alm[venue.chain]
    frame = balance_source.cumulative_balance_timeseries(
        chain=venue.chain.value, token=venue.token.address.value, holder=holder.value,
        start=period.start, pin_block=period.pin_blocks[venue.chain],
    )
    with localcontext() as ctx:
        ctx.prec = 78  # Compare shares before price conversion/context rounding.
        by_day = {}
        for row in frame.itertuples(index=False):
            if period.start <= row.block_date <= period.end:
                shares = Decimal(str(row.daily_net))
                if not shares.is_finite():
                    raise ValueError(f"Nonfinite share movement for {venue.id}")
                by_day[row.block_date] = by_day.get(row.block_date, Decimal(0)) + shares
        delta = closing_shares - opening_shares
        gap = delta - sum(by_day.values(), Decimal(0))
        if not gap.is_finite() or abs(gap) > Decimal(1).scaleb(-venue.token.decimals):
            raise ValueError(f"Holder share reconciliation failed for {venue.id}: gap={gap} shares")
    rows = []
    cumulative = Decimal(0)
    for day, shares in sorted(by_day.items()):
        if shares == 0:
            continue
        block = (period.pin_blocks[venue.chain] if day == period.end else
                 block_resolver.block_at_or_before(
                     venue.chain.value, datetime.combine(day, time.max, tzinfo=UTC)))
        price = price_at_block(block)
        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            raise ValueError(f"Invalid share price for {venue.id}")
        amount = shares * price
        cumulative += amount
        rows.append({'block_date': day, 'daily_inflow': amount, 'cum_inflow': cumulative})
    return pd.DataFrame(rows, columns=['block_date', 'daily_inflow', 'cum_inflow'])
