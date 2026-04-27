"""Top-level orchestrator: gather Normalize inputs, run Compute, return MonthlyPnL.

The only place where Normalize and Compute meet. Resolves SoM / EoM blocks via
RPC unless overridden, then walks every venue for value snapshots + inflow
timeseries before composing the three revenue components.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from ..domain.monthly_pnl import MonthlyPnL
from ..domain.period import Month, Period
from ..domain.primes import Chain, Prime
from ..domain.sky_tokens import USDS_ETHEREUM, sUSDS_ETHEREUM
from ..normalize import (
    get_alm_balance_timeseries,
    get_debt_timeseries,
    get_position_value,
    get_ssr_history,
    get_subproxy_balance_timeseries,
    get_venue_inflow_timeseries,
)
from ..normalize.protocols import (
    IBalanceSource,
    IBlockResolver,
    IConvertToAssetsSource,
    IDebtSource,
    IPositionBalanceSource,
    ISSRSource,
)
from ..normalize.registry import get_block_resolver
from .agent_rate import compute_agent_rate
from .prime_agent_revenue import VenueRevenueInputs, compute_prime_agent_revenue
from .sky_revenue import compute_sky_revenue


@dataclass(frozen=True, slots=True)
class Sources:
    """Optional source overrides — pass for tests, leave None to use registry defaults."""

    debt: IDebtSource | None = None
    balance: IBalanceSource | None = None
    ssr: ISSRSource | None = None
    position_balance: IPositionBalanceSource | None = None
    convert_to_assets: IConvertToAssetsSource | None = None
    block_resolver: IBlockResolver | None = None


def _previous_day_eod_utc(d) -> datetime:
    return datetime.combine(d - timedelta(days=1), time.max, tzinfo=timezone.utc)


def _resolve_pin_blocks(
    anchor_utc: datetime,
    chains: set[Chain],
    resolver: IBlockResolver,
) -> dict[Chain, int]:
    """Resolve ``last block_number with timestamp ≤ anchor_utc`` per chain via Protocol."""
    return {chain: resolver.block_at_or_before(chain.value, anchor_utc) for chain in chains}


def compute_monthly_pnl(
    prime: Prime,
    month: Month,
    *,
    sources: Sources | None = None,
    pin_blocks_eom: dict[Chain, int] | None = None,
    pin_blocks_som: dict[Chain, int] | None = None,
) -> MonthlyPnL:
    """Compute the full monthly settlement for ``prime`` × ``month``.

    Block resolution: by default, EoM and SoM blocks are resolved live via RPC
    (one binary search per chain, ~25 RPC calls each). Tests can supply both
    dicts explicitly to skip RPC entirely.
    """
    sources = sources if sources is not None else Sources()

    # 1. Resolve pin blocks if not provided. Block resolution goes through the
    #    `IBlockResolver` Protocol so subgraph/indexer backends can plug in later.
    period_unpinned = Period.from_month(month)
    if pin_blocks_eom is None or pin_blocks_som is None:
        resolver = sources.block_resolver if sources.block_resolver is not None else get_block_resolver()
        if pin_blocks_eom is None:
            pin_blocks_eom = _resolve_pin_blocks(
                period_unpinned.end_eod_utc, prime.chains, resolver,
            )
        if pin_blocks_som is None:
            pin_blocks_som = _resolve_pin_blocks(
                _previous_day_eod_utc(period_unpinned.start), prime.chains, resolver,
            )

    period = Period(period_unpinned.start, period_unpinned.end, pin_blocks=pin_blocks_eom)

    # 2. Gather Normalize inputs for sky_revenue + agent_rate (Ethereum-only).
    debt = get_debt_timeseries(prime, period, source=sources.debt)
    sub_usds = get_subproxy_balance_timeseries(
        prime, Chain.ETHEREUM, USDS_ETHEREUM, period, source=sources.balance,
    )
    sub_susds = get_subproxy_balance_timeseries(
        prime, Chain.ETHEREUM, sUSDS_ETHEREUM, period, source=sources.balance,
    )
    alm_usds = get_alm_balance_timeseries(
        prime, Chain.ETHEREUM, USDS_ETHEREUM, period, source=sources.balance,
    )
    ssr = get_ssr_history(prime, period, source=sources.ssr)

    # 3. Per-venue: value at SoM + EoM, inflow timeseries.
    venue_inputs: list[VenueRevenueInputs] = []
    for venue in prime.venues:
        if venue.chain not in pin_blocks_som:
            raise ValueError(
                f"Missing SoM pin_block for chain {venue.chain.value} "
                f"(needed by venue {venue.id})"
            )
        som_block = pin_blocks_som[venue.chain]
        eom_block = pin_blocks_eom[venue.chain]

        value_som = get_position_value(
            prime, venue, som_block,
            balance_source=sources.position_balance,
            erc4626_source=sources.convert_to_assets,
        )
        value_eom = get_position_value(
            prime, venue, eom_block,
            balance_source=sources.position_balance,
            erc4626_source=sources.convert_to_assets,
        )

        # Inflow timeseries needs an underlying — Phase 1 categories all have one.
        if venue.underlying is None:
            raise ValueError(
                f"Venue {venue.id} (cat {venue.pricing_category.value}) "
                "requires `underlying` for cost-basis tracking"
            )
        inflow_ts = get_venue_inflow_timeseries(
            prime, venue.chain, venue.underlying, venue.token.address, period,
            source=sources.balance,
        )

        venue_inputs.append(VenueRevenueInputs(
            venue=venue, value_som=value_som, value_eom=value_eom,
            inflow_timeseries=inflow_ts,
        ))

    # 4. Compute three revenue components.
    sky_rev = compute_sky_revenue(period, debt, sub_usds, sub_susds, alm_usds, ssr)
    agent_rate = compute_agent_rate(period, sub_usds, sub_susds, ssr)
    prime_rev, breakdown = compute_prime_agent_revenue(period, venue_inputs)

    monthly_pnl_value = prime_rev + agent_rate - sky_rev

    return MonthlyPnL(
        prime_id=prime.id,
        month=month,
        period=period,
        sky_revenue=sky_rev,
        agent_rate=agent_rate,
        prime_agent_revenue=prime_rev,
        monthly_pnl=monthly_pnl_value,
        venue_breakdown=breakdown,
        pin_blocks_som=pin_blocks_som,
    )
