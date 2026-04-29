"""Top-level orchestrator: gather Normalize inputs, run Compute, return MonthlyPnL.

The only place where Normalize and Compute meet. Resolves SoM / EoM blocks via
RPC unless overridden, then walks every venue for value snapshots + inflow
timeseries before composing the three revenue components.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from ..domain.monthly_pnl import MonthlyPnL
from ..domain.period import Month, Period
from ..domain.pricing import PricingCategory
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
    ICurvePoolSource,
    IDebtSource,
    IPositionBalanceSource,
    ISSRSource,
    IV3PositionSource,
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
    v3_position: IV3PositionSource | None = None
    curve_pool: ICurvePoolSource | None = None
    # Optional NAV-oracle resolver: ``Callable[[str], INavOracleSource]`` that
    # overrides the registry lookup. Used by acceptance scripts to inject
    # historical-NAV overrides without monkey-patching ``_NAV_ORACLE_SOURCES``.
    nav_oracle_resolver: object = None


def _previous_day_eod_utc(d) -> datetime:
    return datetime.combine(d - timedelta(days=1), time.max, tzinfo=timezone.utc)


def _resolve_pin_blocks(
    anchor_utc: datetime,
    chains: set[Chain],
    resolver: IBlockResolver,
) -> dict[Chain, int]:
    """Resolve ``last block_number with timestamp ≤ anchor_utc`` per chain via Protocol."""
    return {chain: resolver.block_at_or_before(chain.value, anchor_utc) for chain in chains}


def _sky_direct_br_charge(
    prime: Prime,
    venue,
    period: Period,
    *,
    balance_source,
    block_resolver,
    nav_at_block,
    ssr_history,
) -> Decimal:
    """Daily-precise BR_charge for a Sky Direct venue over ``period``.

    BR is conceptually a per-second APR (matching SSR's on-chain accrual).
    Integrating ``AV(t) × apr_per_sec(t)`` across the period at daily
    resolution: ``Σ_d AV_d × ((1+SSR_d+30bps)^(1/365) - 1)``.

    AV_d = ``balance_at_day(d) × NAV(eod_block_d)`` — daily share count from
    the cumulative-balance fixture, NAV from the venue's oracle at the day's
    EoD block. For par-stable Sky Direct (BUIDL), pass ``nav_at_block`` that
    returns ``Decimal("1")``.
    """
    from ._helpers import cum_at_or_before, daily_compounding_factor, ssr_at_or_before
    from datetime import date as _date, timedelta as _td

    holder = prime.alm[venue.chain]
    pin_block = period.pin_blocks[venue.chain]
    # Mirror the inflow side's filter so BR_charge AV matches inflow-tracked
    # capital. Without this, BUIDL-I's BR_charge would include sub-$1M daily
    # yield-distribution mints that the Cat-E inflow path filters out.
    min_transfer = venue.min_transfer_amount_usd or Decimal(0)
    bal_df = balance_source.cumulative_balance_timeseries(
        chain=venue.chain.value,
        token=venue.token.address.value,
        holder=holder.value,
        start=prime.start_date,
        pin_block=pin_block,
        min_transfer_amount=min_transfer,
    )

    total = Decimal("0")
    current = period.start
    while current <= period.end:
        bal = cum_at_or_before(bal_df, "cum_balance", current)
        if bal != 0:
            eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
            block = block_resolver.block_at_or_before(venue.chain.value, eod)
            nav = nav_at_block(block)
            ssr = ssr_at_or_before(ssr_history, current)
            base_apy = ssr + Decimal("0.003")
            total += bal * nav * daily_compounding_factor(base_apy)
        current = current + timedelta(days=1)
    return total


def _susds_shares_to_principal(
    sub_susds_shares,
    *,
    sources: "Sources",
    block_resolver: IBlockResolver,
    chain: Chain,
):
    """Convert a sUSDS-shares timeseries into a USDS-denominated cost-basis
    principal timeseries.

    Per debt-rate-methodology: ``principal = shares × entry_pps``. The cost
    basis at any time t is ``Σ_{deposits ≤ t} shares × pps_at_deposit
    − Σ_{withdrawals ≤ t} shares × pps_at_withdrawal`` — the cumulative USDS
    cost of building up the share balance, NOT the current value
    (``shares × current_pps``, which double-counts SSR).

    Approximation: each day's signed share-flow is priced at that day's
    end-of-day pps (one ``convertToAssets`` RPC per active day). This is a
    daily-resolution proxy for entry pps, accurate to the daily SSR drift.
    """
    import pandas as pd
    from datetime import datetime, time, timezone
    from ..normalize.registry import get_convert_to_assets_source

    if sub_susds_shares is None or sub_susds_shares.empty:
        return sub_susds_shares
    if not (sub_susds_shares["daily_net"] != 0).any():
        return sub_susds_shares  # no activity → all-zero is the same in shares/USDS

    # The vault address is currently hardcoded to Ethereum's sUSDS. Multi-chain
    # sUSDS (Base, Arbitrum) would need a per-chain vault map; assert here so a
    # future caller passing a non-Ethereum chain fails loudly instead of silently
    # reading the wrong contract.
    if chain != Chain.ETHEREUM:
        raise NotImplementedError(
            f"sUSDS principal conversion only registered for Ethereum; got {chain}"
        )

    c2a = (
        sources.convert_to_assets
        if sources.convert_to_assets is not None
        else get_convert_to_assets_source()
    )
    # ``cached(...)`` on ``rpc.convert_to_assets`` already memoizes
    # (chain, vault, shares, block) so repeat calls on the same day across
    # multiple monthly runs share the disk cache; no per-call cache needed.

    def _pps_for_day(d) -> Decimal:
        eod = datetime.combine(d, time.max, tzinfo=timezone.utc)
        block = block_resolver.block_at_or_before(chain.value, eod)
        raw = c2a.convert_to_assets(
            chain=chain.value,
            vault=sUSDS_ETHEREUM.address.value,
            shares=10**18,
            block=block,
        )
        return Decimal(raw) / Decimal(10**18)

    out = sub_susds_shares.copy()
    # Build daily principal flow + cumulative in pure Python Decimals — pandas'
    # cumsum on object-dtype Decimal series silently falls back to Python-level
    # reduction, which works in current pandas but is fragile against version
    # changes. Explicit running-sum keeps the dtype contract intact.
    daily_usds: list[Decimal] = []
    cum_usds: list[Decimal] = []
    running = Decimal("0")
    for _, row in out.iterrows():
        shares_flow = row["daily_net"]
        if shares_flow == 0:
            d_usds = Decimal("0")
        else:
            shares_d = (
                shares_flow if isinstance(shares_flow, Decimal)
                else Decimal(str(shares_flow))
            )
            d_usds = shares_d * _pps_for_day(row["block_date"])
        running += d_usds
        daily_usds.append(d_usds)
        cum_usds.append(running)
    out["daily_net"] = daily_usds
    out["cum_balance"] = cum_usds
    return out


def get_psm_usds_timeseries(prime: Prime, chain: Chain, token, period: Period, *, source):
    """USDS the prime has parked at PSM (net of withdrawals), per day.

    Net flow = ``(subproxy + alm) → PSM`` minus ``PSM → (subproxy + alm)``.
    Treated as idle USDS in ``compute_sky_revenue.utilized``: the prime is
    reimbursed BR on the parked balance, matching prime-settlement-methodology
    Step 2 (idle USDS in PSM3).

    Returns a DataFrame ``[block_date, daily_net, cum_balance]`` matching the
    other balance timeseries. Returns empty DataFrame if no PSM is configured
    on the prime (= no reimbursement, $0 contribution to utilized).
    """
    import pandas as pd
    from ._psm import PSM_BY_CHAIN

    psm_addr = PSM_BY_CHAIN.get(chain)
    if psm_addr is None or chain not in prime.subproxy or chain not in prime.alm:
        return pd.DataFrame({"block_date": [], "daily_net": [], "cum_balance": []})

    pin_block = period.pin_blocks[chain]
    holders = [prime.subproxy[chain], prime.alm[chain]]

    # Sum directed flows: holder → PSM (deposit) and PSM → holder (withdrawal).
    # signed: deposit positive (USDS leaves holder, parked at PSM), withdrawal negative.
    daily_by_date: dict = {}
    for holder in holders:
        deposits = source.directed_inflow_timeseries(
            chain=chain.value,
            token=token.address.value,
            from_addr=holder.value,
            to_addr=psm_addr.value,
            start=prime.start_date,
            pin_block=pin_block,
        )
        withdrawals = source.directed_inflow_timeseries(
            chain=chain.value,
            token=token.address.value,
            from_addr=psm_addr.value,
            to_addr=holder.value,
            start=prime.start_date,
            pin_block=pin_block,
        )
        for _, r in deposits.iterrows():
            daily_by_date[r["block_date"]] = daily_by_date.get(r["block_date"], Decimal(0)) + Decimal(str(r["daily_inflow"]))
        for _, r in withdrawals.iterrows():
            daily_by_date[r["block_date"]] = daily_by_date.get(r["block_date"], Decimal(0)) - Decimal(str(r["daily_inflow"]))

    if not daily_by_date:
        return pd.DataFrame({"block_date": [], "daily_net": [], "cum_balance": []})

    rows = sorted(daily_by_date.items(), key=lambda kv: kv[0])
    df = pd.DataFrame({
        "block_date": [r[0] for r in rows],
        "daily_net":  [r[1] for r in rows],
    })
    df["cum_balance"] = df["daily_net"].cumsum()
    return df


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

    # 1. Resolve the block resolver up front. We need it for pin_blocks (if not
    #    supplied) AND for V3 inflow tracking (event block → date conversion).
    resolver = (
        sources.block_resolver
        if sources.block_resolver is not None
        else get_block_resolver()
    )
    period_unpinned = Period.from_month(month)
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
    sub_susds_shares = get_subproxy_balance_timeseries(
        prime, Chain.ETHEREUM, sUSDS_ETHEREUM, period, source=sources.balance,
    )
    # Convert sUSDS shares → USDS-denominated cost-basis principal:
    # ``principal = Σ daily_net_shares × pps_at_that_day's_eod_block``. This is
    # the deposit-time value, NOT the current value (which includes accrued
    # SSR — using current value would double-count savings). Used by both
    # sky_revenue (subtracted from utilized) and agent_rate (earning base).
    sub_susds = _susds_shares_to_principal(
        sub_susds_shares,
        sources=sources,
        block_resolver=resolver,
        chain=Chain.ETHEREUM,
    )
    alm_usds = get_alm_balance_timeseries(
        prime, Chain.ETHEREUM, USDS_ETHEREUM, period, source=sources.balance,
    )
    psm_usds = get_psm_usds_timeseries(
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
            v3_position_source=sources.v3_position,
            curve_pool_source=sources.curve_pool,
            block_resolver=resolver,
            nav_oracle_resolver=sources.nav_oracle_resolver,
        )
        value_eom = get_position_value(
            prime, venue, eom_block,
            balance_source=sources.position_balance,
            erc4626_source=sources.convert_to_assets,
            v3_position_source=sources.v3_position,
            curve_pool_source=sources.curve_pool,
            block_resolver=resolver,
            nav_oracle_resolver=sources.nav_oracle_resolver,
        )

        # Inflow timeseries — three branches:
        #
        # 1. Uniswap V3 — pool-emitted IncreaseLiquidity / DecreaseLiquidity
        #    events carry raw token amounts directly. Source is the same
        #    IV3PositionSource used for valuation.
        # 2. Cat E (RWA managers) and other Cat F (no single underlying) —
        #    deferred until per-protocol event sourcing lands. Empty inflow
        #    means period revenue collapses to MtM Δ.
        # 3. Default (Cat A/B/C/D with a known underlying) — Dune `tokens.transfers`
        #    directed flow from ALM to venue address.
        if venue.lp_kind == "uniswap_v3":
            from ..normalize.positions import _uniswap_v3_inflow_timeseries
            v3_src = sources.v3_position
            if v3_src is None:
                from ..normalize.sources.uniswap_v3 import RPCUniswapV3PositionSource
                overrides = (
                    {venue.chain: venue.nft_position_manager}
                    if venue.nft_position_manager is not None
                    else None
                )
                v3_src = RPCUniswapV3PositionSource(nfpm_per_chain=overrides)
            inflow_ts = _uniswap_v3_inflow_timeseries(
                prime, venue, som_block, eom_block,
                source=v3_src,
                block_to_date=lambda b, _c=venue.chain.value: resolver.block_to_date(_c, b),
            )
        elif venue.lp_kind == "curve_stableswap":
            # Closed-form Curve inflow analogous to Cat C aToken: LP balance
            # is the "scaled" amount, unit_price is the "index". Avoids the
            # need to decode Curve event logs (which differ across templates
            # — NextGen 2-pool uses dynamic-array signatures vs. Plain Pool).
            #
            # Known limitation: when add/remove events fire mid-period, the
            # closed-form prices every change at EoM unit_price, biasing
            # inflow by the cross-event unit_price drift. The exact event-
            # based path was deleted (was dead code; RPC eth_getLogs over a
            # multi-month range exceeds Alchemy's log cap on busy pools).
            # Phase 3+: capture Curve events via Dune and auto-route based on
            # whether the period had any events.
            from ..extract.rpc import balance_of
            from ..normalize.positions import _curve_lp_index_weighted_inflow
            from ..normalize.sources.curve_pool import CurvePoolSource
            from ..domain.primes import Address as _Addr, Chain as _Chain
            curve_src = sources.curve_pool if sources.curve_pool is not None else CurvePoolSource()
            inflow_ts = _curve_lp_index_weighted_inflow(
                prime, venue, som_block, eom_block,
                period_end_date=period.end,
                pool_source=curve_src,
                lp_balance_at=lambda c, t, h, b: balance_of(
                    _Chain(c), _Addr(t), _Addr(h), b,
                ),
            )
        elif venue.pricing_category in (PricingCategory.AAVE_ATOKEN, PricingCategory.SPARKLEND_SPTOKEN):
            # Cat C / D — Aave aToken / SparkLend spToken closed-form inflow
            # via scaledBalanceOf (un-rebased principal). This avoids the
            # face-value-Transfer model's loss of accuracy when burns happen
            # at progressively higher liquidity indices: the simple model
            # under-counts yield by Σ(burn × index_growth_remaining).
            from ..extract.rpc import balance_of, scaled_balance_of
            from ..normalize.positions import _atoken_index_weighted_inflow
            from ..domain.primes import Address as _Addr, Chain as _Chain
            inflow_ts = _atoken_index_weighted_inflow(
                prime, venue, som_block, eom_block,
                period_end_date=period.end,
                balance_at=lambda c, t, h, b: balance_of(
                    _Chain(c), _Addr(t), _Addr(h), b,
                ),
                scaled_balance_at=lambda c, t, h, b: scaled_balance_of(
                    _Chain(c), _Addr(t), _Addr(h), b,
                ),
            )
        elif venue.pricing_category == PricingCategory.ERC4626_VAULT:
            # Cat B — share mint/burn × convertToAssets at day-end block.
            from decimal import Decimal as _Dec
            from ..normalize.positions import _shares_to_usd_inflow_timeseries
            from ..normalize.prices import par_stable_price
            from ..normalize.registry import (
                get_balance_source,
                get_convert_to_assets_source,
            )
            balance_src = sources.balance if sources.balance is not None else get_balance_source()
            erc4626_src = (
                sources.convert_to_assets if sources.convert_to_assets is not None
                else get_convert_to_assets_source()
            )
            if venue.underlying is None:
                raise ValueError(f"Venue {venue.id} (Cat B) requires `underlying`")
            shares_unit = 10 ** venue.token.decimals
            underlying_scale = _Dec(10 ** venue.underlying.decimals)
            par_price = par_stable_price(venue.underlying)

            def _cat_b_price(block, _v=venue, _erc=erc4626_src,
                             _shares=shares_unit, _scale=underlying_scale,
                             _par=par_price):
                raw = _erc.convert_to_assets(
                    chain=_v.chain.value,
                    vault=_v.token.address.value,
                    shares=_shares, block=block,
                )
                return (_Dec(raw) / _scale) * _par

            inflow_ts = _shares_to_usd_inflow_timeseries(
                prime, venue, period,
                balance_source=balance_src,
                block_resolver=resolver,
                price_at_block=_cat_b_price,
            )
        elif venue.pricing_category == PricingCategory.PAR_STABLE:
            # Cat A — raw par-stable holdings on the ALM. Source-tagged
            # inflow netting with an EXTERNAL allowlist: counterparties in
            # `prime.external_alm_sources[chain]` are off-chain custodian
            # senders (e.g. Anchorage) and pass through to revenue. Every
            # other counterparty (PSM swap leg, venue contract allocation/
            # withdrawal, mint/burn, allocator buffer) is treated as
            # value-preserving capital and netted out. Default empty set →
            # revenue = 0, which is correct for par-stables with no
            # off-chain yield source.
            from ..normalize.positions import _cat_a_capital_inflow_timeseries
            from ..normalize.registry import get_balance_source
            balance_src = sources.balance if sources.balance is not None else get_balance_source()
            external = {
                addr.value
                for addr in prime.external_alm_sources.get(venue.chain, [])
            }
            inflow_ts = _cat_a_capital_inflow_timeseries(
                prime, venue, period,
                balance_source=balance_src,
                external_sources=external,
            )
        elif venue.pricing_category == PricingCategory.RWA_TRANCHE:
            # Cat E — RWA tranche net flow × NAV oracle at day-end block.
            # Tracks cumulative balance into the ALM (any sender) since
            # tranche tokens often arrive via issuer custodians, not from 0x0.
            from ..normalize.positions import _rwa_inflow_timeseries
            from ..normalize.prices import _resolve_rwa_nav
            from ..normalize.registry import get_balance_source
            balance_src = sources.balance if sources.balance is not None else get_balance_source()

            def _cat_e_nav(block, _v=venue, _br=resolver, _nr=sources.nav_oracle_resolver):
                return _resolve_rwa_nav(_v, block, block_resolver=_br, resolver=_nr)

            inflow_ts = _rwa_inflow_timeseries(
                prime, venue, period,
                balance_source=balance_src,
                block_resolver=resolver,
                nav_at_block=_cat_e_nav,
            )
        elif venue.underlying is None:
            import pandas as _pd
            inflow_ts = _pd.DataFrame({
                "block_date": [], "daily_inflow": [], "cum_inflow": [],
            })
        else:
            inflow_ts = get_venue_inflow_timeseries(
                prime, venue.chain, venue.underlying, venue.token.address, period,
                source=sources.balance,
            )

        # For Sky Direct venues, compute the per-period BR_charge with
        # daily-precise time-weighting and per-second APR compounding.
        br_charge = None
        if venue.sky_direct:
            from ..normalize.prices import _resolve_rwa_nav
            from ..normalize.registry import get_balance_source as _get_bal
            bsrc = sources.balance if sources.balance is not None else _get_bal()

            def _sd_nav(block, _v=venue, _br=resolver, _nr=sources.nav_oracle_resolver):
                return _resolve_rwa_nav(_v, block, block_resolver=_br, resolver=_nr)

            br_charge = _sky_direct_br_charge(
                prime, venue, period,
                balance_source=bsrc,
                block_resolver=resolver,
                nav_at_block=_sd_nav,
                ssr_history=ssr,
            )

        venue_inputs.append(VenueRevenueInputs(
            venue=venue, value_som=value_som, value_eom=value_eom,
            inflow_timeseries=inflow_ts,
            br_charge=br_charge,
        ))

    # 4. Compute three revenue components.
    agent_rate = compute_agent_rate(period, sub_usds, sub_susds, ssr)
    prime_rev, breakdown = compute_prime_agent_revenue(period, venue_inputs)
    # Total Sky Direct shortfall (Sky absorbs this — reduces Sky's net claim).
    sky_direct_shortfall = sum(
        (vr.sky_direct_shortfall for vr in breakdown), Decimal("0"),
    )
    sky_rev_gross = compute_sky_revenue(
        period, debt, sub_usds, sub_susds, alm_usds, ssr, psm_usds=psm_usds,
    )
    sky_rev = sky_rev_gross - sky_direct_shortfall

    # ``monthly_pnl`` is an audit-only invariant (kept for provenance round-trip,
    # not displayed in the markdown headline or pnl.csv). The ``__post_init__``
    # in ``MonthlyPnL`` checks the same expression — this is just the canonical
    # value to store. Sky Direct shortfall is already netted into ``sky_rev``
    # above, so it doesn't appear here separately.
    return MonthlyPnL(
        prime_id=prime.id,
        month=month,
        period=period,
        sky_revenue=sky_rev,
        agent_rate=agent_rate,
        prime_agent_revenue=prime_rev,
        monthly_pnl=prime_rev + agent_rate - sky_rev,
        venue_breakdown=breakdown,
        pin_blocks_som=pin_blocks_som,
        sky_direct_shortfall=sky_direct_shortfall,
    )
