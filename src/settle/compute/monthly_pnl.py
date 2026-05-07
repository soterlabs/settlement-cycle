"""Top-level orchestrator: gather Normalize inputs, run Compute, return MonthlyPnL.

The only place where Normalize and Compute meet. Resolves SoM / EoM blocks via
RPC unless overridden, then walks every venue for value snapshots + inflow
timeseries before composing the three revenue components.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

import pandas as pd

from ..domain.monthly_pnl import MonthlyPnL
from ..domain.period import Month, Period
from ..domain.pricing import PricingCategory
from ..domain.primes import Chain, Prime
from ..domain.sde import load_sde_table
from ..domain.sky_tokens import USDS_ETHEREUM, sUSDS_ETHEREUM
from ..domain.subsidy import load_reference_rates
from ..normalize import (
    get_alm_balance_timeseries,
    get_debt_timeseries,
    get_position_value,
    get_ssr_history,
    get_subproxy_balance_timeseries,
    get_venue_inflow_timeseries,
)
from ..normalize.prices import _resolve_rwa_nav
from ..normalize.protocols import (
    IBalanceSource,
    IBlockResolver,
    IConvertToAssetsSource,
    ICurvePoolSource,
    IDebtSource,
    IPositionBalanceSource,
    IPsm3Source,
    ISSRSource,
    IV3PositionSource,
)
from ..normalize.registry import (
    get_balance_source,
    get_block_resolver,
    get_convert_to_assets_source,
)
from ._helpers import cum_at_or_before
from .agent_rate import compute_agent_rate
from .prime_agent_revenue import VenueRevenueInputs, compute_prime_agent_revenue
from .sky_revenue import compute_sky_revenue

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Sources:
    """Optional source overrides — pass for tests, leave None to use registry defaults."""

    debt: IDebtSource | None = None
    balance: IBalanceSource | None = None
    ssr: ISSRSource | None = None
    position_balance: IPositionBalanceSource | None = None
    convert_to_assets: IConvertToAssetsSource | None = None
    psm3: IPsm3Source | None = None
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


def _sde_asset_value_timeseries(
    prime: Prime,
    venue,
    period: Period,
    *,
    balance_source,
    block_resolver,
    nav_at_block,
    cap_usd: Decimal | None = None,
) -> pd.DataFrame:
    """Daily SDE asset value (USD) per venue. Returns a level series with
    columns ``[block_date, cum_value]`` (the "cum_" prefix is API parity
    with cum_balance/cum_inflow; this is a daily snapshot, not a sum).

    AV_d = balance_at_day(d) × NAV(EoD block d), capped at ``cap_usd`` for
    ``kind=capped`` SDE. Consumed by ``compute_sky_revenue`` for utilized
    exclusion.
    """
    holder = venue.holder_override or prime.alm[venue.chain]
    pin_block = period.pin_blocks[venue.chain]
    bal_df = balance_source.cumulative_balance_timeseries(
        chain=venue.chain.value,
        token=venue.token.address.value,
        holder=holder.value,
        start=prime.start_date,
        pin_block=pin_block,
        min_transfer_amount=venue.min_transfer_amount_usd or Decimal(0),
    )

    rows = []
    current = period.start
    while current <= period.end:
        bal = cum_at_or_before(bal_df, "cum_balance", current)
        if bal == 0:
            value = Decimal("0")
        else:
            eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
            block = block_resolver.block_at_or_before(venue.chain.value, eod)
            value = bal * nav_at_block(block)
            if cap_usd is not None and value > cap_usd:
                value = cap_usd
        rows.append({"block_date": current, "cum_value": value})
        current = current + timedelta(days=1)
    return pd.DataFrame(rows)


def _susds_shares_to_principal(
    sub_susds_shares,
    *,
    sources: "Sources",
    block_resolver: IBlockResolver,
    chain: Chain,
):
    """Convert a sUSDS-shares timeseries to USDS-denominated cost-basis
    principal (``Σ shares × entry_pps``).

    Each day's signed share-flow is priced at that day's EoD pps (one
    ``convertToAssets`` RPC per active day). This is the deposit-time value,
    NOT the current value (``shares × current_pps``, which double-counts SSR).
    """
    if sub_susds_shares is None or sub_susds_shares.empty:
        return sub_susds_shares
    if not (sub_susds_shares["daily_net"] != 0).any():
        return sub_susds_shares  # no activity → all-zero is the same in shares/USDS

    # The vault address is hardcoded to Ethereum's sUSDS. Multi-chain sUSDS
    # would need a per-chain vault map; fail loudly rather than silently
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

    # Build daily principal flow + cumulative in pure-Python Decimals —
    # pandas' cumsum on object-dtype Decimal silently falls back to Python
    # reduction; explicit running-sum keeps the dtype contract intact.
    out = sub_susds_shares.copy()
    daily_usds: list[Decimal] = []
    cum_usds: list[Decimal] = []
    running = Decimal("0")
    for _, row in out.iterrows():
        shares_flow = row["daily_net"]
        if shares_flow == 0:
            d_usds = Decimal("0")
        else:
            d_usds = _to_decimal(shares_flow) * _pps_for_day(row["block_date"])
        running += d_usds
        daily_usds.append(d_usds)
        cum_usds.append(running)
    out["daily_net"] = daily_usds
    out["cum_balance"] = cum_usds
    return out


def _empty_psm_df() -> pd.DataFrame:
    return pd.DataFrame({"block_date": [], "daily_net": [], "cum_balance": []})


def _to_decimal(v) -> Decimal:
    """Coerce a numpy/pandas scalar to ``Decimal`` without round-tripping
    Decimals through ``str``."""
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _df_from_daily_dict(daily_by_date: dict) -> pd.DataFrame:
    """``[block_date, daily_net, cum_balance]`` DataFrame from a ``{date: Decimal}``
    map. Returns the empty-shape frame if the map is empty."""
    if not daily_by_date:
        return _empty_psm_df()
    rows = sorted(daily_by_date.items(), key=lambda kv: kv[0])
    df = pd.DataFrame({
        "block_date": [r[0] for r in rows],
        "daily_net":  [r[1] for r in rows],
    })
    df["cum_balance"] = df["daily_net"].cumsum()
    return df


def _aggregate_psm_usds(
    prime: Prime,
    period: Period,
    *,
    balance_source,
    psm3_source=None,
    block_resolver=None,
):
    """Sum PSM USDS-equivalent timeseries across every chain in
    ``prime.psm``. Per-chain timeseries are produced by
    ``get_psm_usds_timeseries``; this aggregates them into a single daily
    series consumable by ``compute_sky_revenue``.

    Returns an empty DataFrame if the prime has no PSM configured anywhere.
    """
    if not prime.psm:
        return _empty_psm_df()

    daily_by_date: dict = {}
    for chain in prime.psm:
        if chain not in period.pin_blocks:
            continue
        per_chain = get_psm_usds_timeseries(
            prime, chain, period,
            balance_source=balance_source,
            psm3_source=psm3_source,
            block_resolver=block_resolver,
        )
        for _, row in per_chain.iterrows():
            d = row["block_date"]
            daily_by_date[d] = daily_by_date.get(d, Decimal(0)) + _to_decimal(row["daily_net"])

    return _df_from_daily_dict(daily_by_date)


def get_psm_usds_timeseries(
    prime: Prime, chain: Chain, period: Period,
    *,
    balance_source,
    psm3_source=None,
    block_resolver=None,
):
    """USDS-equivalent the prime has parked at the PSM on ``chain``, per day.

    Treated as idle USDS in ``compute_sky_revenue.utilized``: the prime is
    reimbursed BR on the parked balance, matching prime-settlement-methodology
    Step 2 (idle USDS in PSM / PSM3).

    Returns a DataFrame ``[block_date, daily_net, cum_balance]`` matching the
    other balance timeseries. Returns empty DataFrame if the prime has no PSM
    configured on this chain.

    Two PSM mechanics, dispatched on ``prime.psm[chain].kind``:

    * ``DIRECTED_FLOW`` (Sky LITE-PSM-USDC): track net USDS flow from
      ``(subproxy + ALM) → PSM`` minus ``PSM → (subproxy + ALM)``. USDS is
      par-stable so the raw flow IS the USDS-equivalent.
    * ``ERC4626_SHARES`` (Spark PSM3): the ALM holds PSM3 shares which are
      *internal accounting* (no ERC-20 Transfer events) and the rate uses a
      non-standard ``convertToAssetValue(uint256)``. We snapshot
      ``convertToAssetValue(shares(alm, b), b)`` at each day's EoD block,
      diff it across days to produce ``daily_net``, and surface
      ``cum_balance`` as the running USDS-equivalent.
    """
    import pandas as pd
    from ..domain.primes import PsmKind

    cfg = prime.psm.get(chain)
    if cfg is None or chain not in prime.alm:
        return _empty_psm_df()

    if cfg.kind == PsmKind.DIRECTED_FLOW:
        # Sky LITE-PSM pattern. Track ``token`` flow in/out of PSM from both
        # the subproxy and the ALM. ``pin_block`` is only resolved here
        # because the ERC4626_SHARES path resolves blocks per-day via the
        # block_resolver and doesn't need a period-level pin.
        if cfg.token is None:
            raise ValueError(
                f"PSM config for {chain} (kind=directed_flow) requires a token "
                "(e.g. USDS for the Sky LITE-PSM)"
            )
        pin_block = period.pin_blocks[chain]
        bsrc = balance_source if balance_source is not None else get_balance_source()
        holders = [prime.subproxy[chain]] if chain in prime.subproxy else []
        holders.append(prime.alm[chain])

        def _flow(from_addr, to_addr):
            return bsrc.directed_inflow_timeseries(
                chain=chain.value, token=cfg.token.value,
                from_addr=from_addr, to_addr=to_addr,
                start=prime.start_date, pin_block=pin_block,
            )

        daily_by_date: dict = {}
        for holder in holders:
            for _, r in _flow(holder.value, cfg.address.value).iterrows():
                d = r["block_date"]
                daily_by_date[d] = daily_by_date.get(d, Decimal(0)) + _to_decimal(r["daily_inflow"])
            for _, r in _flow(cfg.address.value, holder.value).iterrows():
                d = r["block_date"]
                daily_by_date[d] = daily_by_date.get(d, Decimal(0)) - _to_decimal(r["daily_inflow"])
        return _df_from_daily_dict(daily_by_date)

    if cfg.kind == PsmKind.ERC4626_SHARES:
        # Spark PSM3. Shares are internal accounting (no Transfer events), so
        # the only way to know the ALM's holding is to read ``shares(alm, b)``
        # at each block. We snapshot the USDS-equivalent value
        # ``convertToAssetValue(shares(alm, b), b)`` at each day's EoD across
        # the period, diff across days to get ``daily_net``, and surface the
        # snapshot itself as ``cum_balance``.
        from ..normalize.registry import get_psm3_source as _get_psm3
        if block_resolver is None:
            raise ValueError(
                "get_psm_usds_timeseries(kind=erc4626_shares) requires a "
                "block_resolver to read PSM3 shares at each day's EoD block"
            )
        psm3 = psm3_source if psm3_source is not None else _get_psm3()
        scale = Decimal(10**18)
        holder = prime.alm[chain].value

        # Per-day RPC failures (drpc upstream flake, contract-not-yet-deployed
        # at very early blocks) shouldn't kill the whole chain's PSM3
        # timeseries — that would silently inflate sky_revenue by the missing
        # PSM holdings. Treat a failed day as "carry forward yesterday's
        # value" (no movement) and log the gap so it's auditable.
        from ..extract.rpc import RPCError
        import requests as _requests
        import logging as _logging
        _log = _logging.getLogger(__name__)

        def _value_at(day, fallback: Decimal | None = None) -> Decimal:
            eod = datetime.combine(day, time.max, tzinfo=timezone.utc)
            try:
                block = block_resolver.block_at_or_before(chain.value, eod)
                shares = psm3.shares_of(
                    chain=chain.value, psm3=cfg.address.value,
                    holder=holder, block=block,
                )
                if shares <= 0:
                    return Decimal(0)
                raw = psm3.convert_to_asset_value(
                    chain=chain.value, psm3=cfg.address.value,
                    num_shares=shares, block=block,
                )
                return Decimal(raw) / scale
            except (RPCError, _requests.HTTPError, _requests.ConnectionError, _requests.Timeout) as e:
                if fallback is None:
                    raise
                _log.warning(
                    "PSM3 read failed on %s for %s @ %s; carrying forward "
                    "$%s (error: %s). PSM USDS-equiv may be slightly stale "
                    "for this day.",
                    chain.value, cfg.address.value.hex(), day,
                    f"{fallback:,.2f}", type(e).__name__,
                )
                return fallback

        # One snapshot per day across [period.start, period.end]. The init
        # read (period.start - 1) cannot fall back — a missing baseline
        # means we can't compute period flows correctly, so let it raise.
        days = [period.start + timedelta(days=i) for i in range((period.end - period.start).days + 1)]
        cur_value = _value_at(period.start - timedelta(days=1))
        block_dates: list = []
        daily_net: list[Decimal] = []
        cum_balance: list[Decimal] = []
        for day in days:
            value = _value_at(day, fallback=cur_value)
            block_dates.append(day)
            daily_net.append(value - cur_value)
            cum_balance.append(value)
            cur_value = value

        if all(v == 0 for v in cum_balance):
            return _empty_psm_df()
        return pd.DataFrame({
            "block_date": block_dates,
            "daily_net": daily_net,
            "cum_balance": cum_balance,
        })

    raise ValueError(f"Unknown PSM kind: {cfg.kind!r}")


def _aggregate_curve_idle_usds(
    prime: Prime,
    period: Period,
    *,
    curve_pool_source,
    block_resolver,
    convert_to_assets_source=None,
) -> tuple[pd.DataFrame, Decimal]:
    """Daily data for Curve pool coins configured via ``curve_idle_usds``.

    Returns a **tuple** ``(utilized_deduction_df, susds_spread)``:

    * ``utilized_deduction_df`` — ``[block_date, daily_net, cum_balance]`` frame
      of the daily USDS deduction from ``utilized`` (par-stable coin venues only).
    * ``susds_spread`` — total Decimal Prime Revenue from the 30 bps spread on
      sUSDS inside LP pools (``sky_savings_token=True`` venues only).

    **Par-stable coin venues** (``sky_savings_token=False``):
    Prime's proportional share of the coin reserve is deducted from ``utilized``::

        prime_usds_d = (alm_lp_d / pool_total_d) × coin_reserve_d

    **sUSDS / sky-savings-token venues** (``sky_savings_token=True``):
    No ``utilized`` deduction. Instead the prime earns the 30 bps spread::

        spread_d = (alm_lp_d / pool_total_d) × (sUSDS_reserve_d × pps_d) × 30bps_daily

    where ``pps_d = convertToAssets(1 share, block_d) / 10**underlying_decimals``.
    """
    from datetime import time
    from ..domain.sky_tokens import KNOWN_PAR_STABLES_ETHEREUM, KNOWN_YIELD_BEARING_ETHEREUM
    from ..extract.rpc import balance_of as _balance_of
    from ..normalize.sources.curve_pool import CurvePoolSource
    from .agent_rate import AGENT_RATE_OVER_SSR
    from ._helpers import daily_compounding_factor

    venues_with_config = [v for v in prime.venues if v.curve_idle_usds is not None]
    if not venues_with_config:
        return _empty_psm_df(), Decimal("0")

    pool_src = curve_pool_source if curve_pool_source is not None else CurvePoolSource()

    # c2a is only needed for sky_savings_token venues.
    has_sky_savings = any(v.curve_idle_usds.sky_savings_token for v in venues_with_config)
    c2a = None
    if has_sky_savings:
        from ..normalize.registry import get_convert_to_assets_source as _get_c2a
        c2a = convert_to_assets_source if convert_to_assets_source is not None else _get_c2a()

    spread_daily_factor = daily_compounding_factor(AGENT_RATE_OVER_SSR)

    import logging as _logging
    _log = _logging.getLogger(__name__)

    def _par_stable_usds(coin_addr: bytes, raw_balance: int) -> Decimal:
        par = KNOWN_PAR_STABLES_ETHEREUM.get(coin_addr)
        if par is None:
            raise ValueError(
                f"_aggregate_curve_idle_usds: coin {coin_addr.hex()} is not in "
                "KNOWN_PAR_STABLES_ETHEREUM. Use sky_savings_token=True for "
                "yield-bearing coins."
            )
        _sym, decimals = par
        return Decimal(raw_balance) / Decimal(10 ** decimals)

    # Separate accumulators for par-stable (utilized deduction) and
    # sky-savings-token (spread revenue).
    daily_util: dict = {}
    daily_spread: dict = {}

    for venue in venues_with_config:
        cfg = venue.curve_idle_usds
        target_coin = cfg.coin.value
        pool_addr = venue.token.address.value
        holder = (venue.holder_override or prime.alm[venue.chain]).value
        chain_str = venue.chain.value

        current = period.start
        while current <= period.end:
            eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
            prime_usds = Decimal(0)
            prime_spread = Decimal(0)
            try:
                block = block_resolver.block_at_or_before(chain_str, eod)
                pool_state = pool_src.read_pool(chain_str, pool_addr, block)
                total_supply = pool_state.total_supply
                if total_supply == 0:
                    current = current + timedelta(days=1)
                    continue

                coin_idx = next(
                    (i for i, c in enumerate(pool_state.coins) if c.value == target_coin),
                    None,
                )
                if coin_idx is None:
                    _log.warning(
                        "curve_idle_usds: venue %s pool %s does not contain coin %s "
                        "at block %d — skipping day %s.",
                        venue.id, pool_addr.hex(), target_coin.hex(), block, current,
                    )
                    current = current + timedelta(days=1)
                    continue

                raw_coin_balance = pool_state.balances[coin_idx]
                from ..domain.primes import Address as _Addr
                alm_lp_raw = _balance_of(
                    venue.chain, venue.token.address, _Addr(holder), block,
                )
                alm_lp = Decimal(alm_lp_raw) / Decimal(10 ** venue.token.decimals)
                pool_total = Decimal(total_supply) / Decimal(10 ** venue.token.decimals)

                if cfg.sky_savings_token:
                    # sUSDS in LP: earn 30bps spread on prime's USDS-equivalent share.
                    # No utilized deduction.
                    yb = KNOWN_YIELD_BEARING_ETHEREUM.get(target_coin)
                    if yb is None:
                        raise ValueError(
                            f"curve_idle_usds sky_savings_token=True for coin "
                            f"{target_coin.hex()} but it is not in "
                            "KNOWN_YIELD_BEARING_ETHEREUM — add it to sky_tokens.py."
                        )
                    _sym, share_decimals, _und, underlying_decimals = yb
                    pps_raw = c2a.convert_to_assets(
                        chain=chain_str, vault=target_coin,
                        shares=10 ** share_decimals, block=block,
                    )
                    pps = Decimal(pps_raw) / Decimal(10 ** underlying_decimals)
                    prime_susds_usds = (
                        (alm_lp / pool_total)
                        * (Decimal(raw_coin_balance) / Decimal(10 ** share_decimals))
                        * pps
                    ) if pool_total > 0 else Decimal(0)
                    prime_spread = prime_susds_usds * spread_daily_factor
                elif target_coin in KNOWN_YIELD_BEARING_ETHEREUM:
                    # Yield-bearing but not sky_savings_token: data fetched for
                    # future use; no utilized deduction and no spread revenue yet.
                    pass
                else:
                    coin_usds = _par_stable_usds(target_coin, raw_coin_balance)
                    prime_usds = (
                        (alm_lp / pool_total) * coin_usds if pool_total > 0 else Decimal(0)
                    )

            except Exception as exc:
                _log.warning(
                    "curve_idle_usds: RPC error for venue %s on %s; carrying forward "
                    "prior value (error: %s).",
                    venue.id, current, type(exc).__name__,
                )
                prev = current - timedelta(days=1)
                prime_usds = daily_util.get(prev, Decimal(0))
                prime_spread = daily_spread.get(prev, Decimal(0))

            daily_util[current] = daily_util.get(current, Decimal(0)) + prime_usds
            daily_spread[current] = daily_spread.get(current, Decimal(0)) + prime_spread
            current = current + timedelta(days=1)

    # Build utilized deduction DataFrame (par-stable coins).
    if daily_util:
        days_sorted = sorted(daily_util)
        cum_balance = [daily_util[d] for d in days_sorted]
        daily_net = [cum_balance[0]] + [
            cum_balance[i] - cum_balance[i - 1] for i in range(1, len(cum_balance))
        ]
        util_df = pd.DataFrame({
            "block_date": days_sorted,
            "daily_net": daily_net,
            "cum_balance": cum_balance,
        })
    else:
        util_df = _empty_psm_df()

    susds_spread = sum(daily_spread.values(), Decimal("0"))
    return util_df, susds_spread


def _aggregate_lending_idle_usds(
    prime: Prime,
    period: Period,
    *,
    block_resolver,
) -> pd.DataFrame:
    """Daily USDS-equivalent of unborrowed underlying inside lending pools,
    summed across all venues with ``lending_idle_usds=True``.

    For each such venue (Cat C/D — Aave aToken / SparkLend spToken) the
    prime's proportional share of the pool's idle underlying is::

        prime_idle_d = (balanceOf(alm, spToken_d) / totalSupply(spToken_d))
                     × balanceOf(spToken_contract, underlying_d)

    ``balanceOf(alm, spToken)`` is the rebased balance (includes accrued
    interest), and ``totalSupply(spToken)`` is the rebased total — so the
    ratio is equivalent to using the scaled (un-rebased) values, which avoids
    needing a separate ``scaledTotalSupply`` call.

    ``balanceOf(spToken_contract, underlying)`` is the balance of the raw
    underlying token (USDS, DAI) sitting idle in the spToken contract — i.e.
    the unborrowed portion of the pool. Deposited capital that has been
    borrowed out is NOT reflected here, so this is exactly the prime-
    settlement-methodology Step 2 "idle underlying in lending pool" deduction.

    The underlying must be a par-stable (USDS, DAI, USDC at $1 per unit).
    Deduction is in USDS-equivalent at face value (divided by underlying decimals).

    Returns a ``[block_date, daily_net, cum_balance]`` DataFrame where
    ``cum_balance`` is a daily snapshot matching the PSM3 convention.
    Returns an empty DataFrame if no venue has ``lending_idle_usds=True``.
    """
    from datetime import time
    from ..extract.rpc import balance_of as _balance_of, total_supply_of as _total_supply_of

    venues = [v for v in prime.venues if v.lending_idle_usds]
    if not venues:
        return _empty_psm_df()

    import logging as _logging
    _log = _logging.getLogger(__name__)

    daily_by_date: dict = {}

    for venue in venues:
        if venue.underlying is None:
            _log.warning(
                "lending_idle_usds: venue %s has no `underlying` configured — skipping.",
                venue.id,
            )
            continue

        alm_addr = venue.holder_override or prime.alm[venue.chain]
        sptoken_addr = venue.token.address
        underlying_addr = venue.underlying.address
        underlying_decimals = venue.underlying.decimals
        chain = venue.chain

        current = period.start
        while current <= period.end:
            eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
            try:
                block = block_resolver.block_at_or_before(chain.value, eod)

                alm_sptoken_raw = _balance_of(chain, sptoken_addr, alm_addr, block)
                total_supply_raw = _total_supply_of(chain, sptoken_addr, block)

                if total_supply_raw == 0:
                    prime_idle = Decimal(0)
                else:
                    # Pool's idle underlying = underlying balance in the spToken contract
                    pool_idle_raw = _balance_of(chain, underlying_addr, sptoken_addr, block)
                    alm_share = Decimal(alm_sptoken_raw) / Decimal(total_supply_raw)
                    pool_idle_usds = Decimal(pool_idle_raw) / Decimal(10 ** underlying_decimals)
                    prime_idle = alm_share * pool_idle_usds

            except Exception as exc:
                _log.warning(
                    "lending_idle_usds: RPC error for venue %s on %s; carrying forward "
                    "prior value (error: %s).",
                    venue.id, current, type(exc).__name__,
                )
                prime_idle = daily_by_date.get(current - timedelta(days=1), Decimal(0))

            daily_by_date[current] = daily_by_date.get(current, Decimal(0)) + prime_idle
            current = current + timedelta(days=1)

    if not daily_by_date:
        return _empty_psm_df()

    days_sorted = sorted(daily_by_date)
    cum_balance = [daily_by_date[d] for d in days_sorted]
    daily_net = [cum_balance[0]] + [
        cum_balance[i] - cum_balance[i - 1] for i in range(1, len(cum_balance))
    ]
    return pd.DataFrame({
        "block_date": days_sorted,
        "daily_net": daily_net,
        "cum_balance": cum_balance,
    })


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
    # SSR — using current value would double-count savings). Used by
    # agent_rate (earning base); NOT passed to sky_revenue (subproxy balances
    # are treasury/risk capital, not pure ilk-debt proceeds).
    sub_susds = _susds_shares_to_principal(
        sub_susds_shares,
        sources=sources,
        block_resolver=resolver,
        chain=Chain.ETHEREUM,
    )
    alm_usds = get_alm_balance_timeseries(
        prime, Chain.ETHEREUM, USDS_ETHEREUM, period, source=sources.balance,
    )
    # Sum PSM USDS-equivalent across ALL chains where the prime has a PSM
    # configured. The prime's debt (cum_debt) is Ethereum-only (Vat), but
    # USDS-equivalent capital parked at any PSM (Sky LITE-PSM on Eth, Spark
    # PSM3 on L2s) was funded from that debt and reduces utilized.
    psm_usds = _aggregate_psm_usds(
        prime, period,
        balance_source=sources.balance,
        psm3_source=sources.psm3,
        block_resolver=resolver,
    )
    # Prime's proportional USDS-equivalent in configured Curve pools — Step 2
    # idle AMM USDS. Computed daily via RPC (``read_pool`` + ``balanceOf`` +
    # ``convertToAssets`` for sUSDS legs). Returns empty frame if no venue has
    # ``curve_idle_usds`` set (e.g. Grove, OBEX — $0, no cost).
    # Returns (utilized_deduction_df, curve_susds_spread).
    # curve_susds_spread is the total 30bps Prime Revenue from sUSDS held inside
    # Curve LP pools (sky_savings_token=True venues); added to prime_rev below.
    curve_idle_usds, curve_susds_spread = _aggregate_curve_idle_usds(
        prime, period,
        curve_pool_source=sources.curve_pool,
        block_resolver=resolver,
        convert_to_assets_source=sources.convert_to_assets,
    )
    # Prime's share of unborrowed underlying in configured lending pools — Step 2
    # idle lending pool USDS. Computed daily via ``balanceOf`` + ``totalSupply``.
    # Returns empty frame if no venue has ``lending_idle_usds=True``.
    lending_idle_usds = _aggregate_lending_idle_usds(
        prime, period,
        block_resolver=resolver,
    )
    ssr = get_ssr_history(prime, period, source=sources.ssr)

    # SDE table — config-driven Sky Direct exposures (replaces the legacy
    # ``Venue.sky_direct: bool`` flag). Empty table = no venues are SDE.
    sde_table = load_sde_table()
    sde_asset_value_per_venue: list = []

    # 3. Per-venue: value at SoM + EoM, inflow timeseries.
    venue_inputs: list[VenueRevenueInputs] = []
    for venue in prime.venues:
        if venue.skip:
            # Excluded from MSC — typically venues whose NAV oracle is
            # untrusted or whose underlying is too volatile (e.g. Avalanche
            # cross-chain RWAs without a reliable feed). Logged once for
            # provenance.
            import logging as _logging
            _logging.getLogger(__name__).info(
                "Skipping venue %s (%s, %s) — venue.skip=True.",
                venue.id, venue.token.symbol, venue.chain.value,
            )
            continue
        if venue.pricing_category == PricingCategory.SPARK_SAVINGS_V2:
            # Spark Savings V2 vaults aren't held at the prime ALM — the
            # vault contract custodies underlying for retail depositors and
            # the prime earns the yield spread (vault_yield − share_rate).
            # Computing this requires a separate assets-vs-liabilities
            # accounting layer (vault underlying balance ↔ share supply ×
            # pps) that doesn't fit the standard Cat A/B/C/E/F flow.
            # Skip with a warning until that layer lands.
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "Skipping Spark Savings V2 venue %s (%s, %s) — compute path "
                "for spread accounting not yet implemented.",
                venue.id, venue.token.symbol, venue.chain.value,
            )
            continue
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
        #
        # susds_spread is set to a Decimal for Cat B yield-bearing venues (sUSDS)
        # and remains None for all other venues. It is threaded into
        # VenueRevenueInputs.actual_revenue_override below.
        susds_spread: Decimal | None = None
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
            # Cat B — two sub-cases:
            #
            # (a) Sky-yield-bearing 4626 at ALM (e.g. sUSDS POL, S32):
            #     The SSR appreciation flows back to Sky via the borrow-rate
            #     charge on utilized. Prime earns only the 30bps spread
            #     (BR − SSR) per day on the SoM USDS value. We override
            #     actual_revenue with this spread rather than using the MtM
            #     Δvalue (which would count SSR as Prime Revenue).
            #
            # (b) All other Cat B vaults (Morpho, syrupUSDC, …):
            #     Standard MtM approach — share mint/burn × convertToAssets.
            from decimal import Decimal as _Dec
            from ..normalize.positions import _shares_to_usd_inflow_timeseries
            from ..normalize.prices import par_stable_price
            from .agent_rate import AGENT_RATE_OVER_SSR
            from ._helpers import daily_compounding_factor

            if venue.sky_savings_token:
                # Sub-case (a): yield-bearing token (sUSDS) at ALM.
                # Prime Revenue = 30bps × value_som × n_days (spread only).
                # `value_som` is already computed above via convertToAssets at
                # SoM block — no new RPC calls needed.
                spread_daily = daily_compounding_factor(AGENT_RATE_OVER_SSR)
                n_days = _Dec(str((period.end - period.start).days + 1))
                susds_spread = value_som * spread_daily * n_days
                inflow_ts = pd.DataFrame({
                    "block_date": [], "daily_inflow": [], "cum_inflow": [],
                })
            else:
                # Sub-case (b): normal Cat B MtM.
                susds_spread = None
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
            balance_src = sources.balance if sources.balance is not None else get_balance_source()
            external = {
                addr.value
                for addr in prime.external_alm_sources.get(venue.chain, [])
            }
            # Map override list keyed by raw 20-byte address (matches the
            # ``_to_bytes`` normalisation inside the helper).
            overrides_for_chain = prime.principal_return_overrides.get(venue.chain, {})
            overrides_by_bytes = {
                addr.value: [(o.date, o.amount) for o in entries]
                for addr, entries in overrides_for_chain.items()
            }
            inflow_ts = _cat_a_capital_inflow_timeseries(
                prime, venue, period,
                balance_source=balance_src,
                external_sources=external,
                principal_return_overrides=overrides_by_bytes,
            )
        elif venue.pricing_category == PricingCategory.RWA_TRANCHE:
            # Cat E — RWA tranche net flow × NAV oracle at day-end block.
            # Tracks cumulative balance into the ALM (any sender) since
            # tranche tokens often arrive via issuer custodians, not from 0x0.
            from ..normalize.positions import _rwa_inflow_timeseries
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

        # SDE classification — if the venue has an active Sky Direct entry
        # overlapping the period, build its daily asset-value timeseries
        # (capped if kind=capped) for utilized exclusion in compute_sky_revenue.
        sde_entry = sde_table.overlaps_venue(
            prime.id, venue.id, period.start, period.end,
        )
        if sde_entry is not None:
            bsrc = sources.balance if sources.balance is not None else get_balance_source()

            def _sd_nav(block, _v=venue, _br=resolver, _nr=sources.nav_oracle_resolver):
                return _resolve_rwa_nav(_v, block, block_resolver=_br, resolver=_nr)

            sde_asset_value_per_venue.append(_sde_asset_value_timeseries(
                prime, venue, period,
                balance_source=bsrc,
                block_resolver=resolver,
                nav_at_block=_sd_nav,
                cap_usd=sde_entry.cap_usd,
            ))

        venue_inputs.append(VenueRevenueInputs(
            venue=venue, value_som=value_som, value_eom=value_eom,
            inflow_timeseries=inflow_ts,
            sde_entry=sde_entry,
            actual_revenue_override=susds_spread,
        ))

    # 4. Compute three revenue components.
    agent_rate = compute_agent_rate(period, sub_usds, sub_susds, ssr)
    prime_rev, breakdown = compute_prime_agent_revenue(period, venue_inputs)
    # Add 30bps spread on sUSDS held inside Curve LP pools. This is computed
    # separately from the venue loop because the data comes from the Curve
    # pool daily snapshots, not from the venue's SoM/EoM position values.
    prime_rev = prime_rev + curve_susds_spread
    # SDE revenue (Σ actual × sd_share across venues) flows directly to Sky.
    sde_revenue = sum((vr.sd_revenue for vr in breakdown), Decimal("0"))

    # Aggregate per-venue daily SDE asset-value into one frame so
    # compute_sky_revenue can subtract it from utilized (SDE positions
    # already pay Sky directly via sde_revenue; charging BR would double-bill).
    if sde_asset_value_per_venue:
        sde_av_total = (
            pd.concat(sde_asset_value_per_venue)
              .groupby("block_date", as_index=False)["cum_value"].sum()
              .sort_values("block_date").reset_index(drop=True)
        )
    else:
        sde_av_total = None

    # Subsidised borrowing rate (debt-rate-methodology Step 1.b). When
    # ``prime.subsidy.enabled`` is False this collapses to full-BR and
    # ``ref_rate_history`` is never read.
    ref_rate_history = (
        load_reference_rates(kind=prime.subsidy.ref_rate_kind)
        if prime.subsidy.enabled else None
    )
    sky_rev_br = compute_sky_revenue(
        period, debt, alm_usds, ssr, psm_usds=psm_usds,
        subsidy_config=prime.subsidy,
        ref_rate_history=ref_rate_history,
        sde_asset_value=sde_av_total,
        curve_idle_usds=curve_idle_usds,
        lending_idle_usds=lending_idle_usds,
    )
    # Sky's full claim: BR on (utilized − SDE) + actual SDE revenue.
    sky_rev = sky_rev_br + sde_revenue
    # Legacy field — always 0 under the SDE-split model (Sky takes actual
    # revenue, not floored).
    sky_direct_shortfall = Decimal("0")

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
        sde_revenue=sde_revenue,
    )
