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
from ..domain.primes import Chain, Prime, PsmKind
from ..domain.sde import load_sde_table
from ..domain.sky_tokens import USDS_ETHEREUM, sUSDS_ETHEREUM
from ..domain.subsidy import load_reference_rates
from ..normalize import (
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
from .sky_revenue import compute_sky_revenue_daily

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
    """Resolve ``last block_number with timestamp ≤ anchor_utc`` per chain.

    Chains are resolved in parallel (ThreadPoolExecutor) to amortise the
    ~25 binary-search RPC calls each one requires. For Spark (6 chains) this
    cuts wall-clock time from ~6× to ~1× the single-chain cost.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not chains:
        return {}

    def _one(chain: Chain) -> tuple[Chain, int]:
        block = resolver.block_at_or_before(chain.value, anchor_utc)
        _log.info("  pin block resolved: %s → %d", chain.value, block)
        return chain, block

    with ThreadPoolExecutor(max_workers=min(len(chains), 8)) as pool:
        futures = [pool.submit(_one, c) for c in chains]
        return dict(f.result() for f in as_completed(futures))


def _curve_sde_asset_value_timeseries(
    prime: Prime,
    venue,
    period: Period,
    *,
    sde_coin,
    curve_pool_source,
    block_resolver,
    cap_usd: Decimal | None = None,
) -> "pd.DataFrame":
    """Daily SDE asset value for a Curve LP pool venue (par-stable SDE coin).

    Computes prime's proportional share of the named par-stable coin's reserve:

        value_d = (alm_lp_d / pool_total_d) × coin_reserve_d   (at $1/unit)

    Used instead of the RWA NAV-oracle path for Cat F venues where the SDE
    exposure is a par-stable coin (e.g. USDT in the sUSDS/USDT pool, S24).
    ``sde_coin`` must be in ``KNOWN_PAR_STABLES_ETHEREUM``.
    """
    from datetime import time
    from ..extract.rpc import balance_of as _balance_of
    from ..domain.primes import Address as _Addr
    from ..domain.sky_tokens import KNOWN_PAR_STABLES_ETHEREUM

    sde_coin_bytes = sde_coin.value
    par = KNOWN_PAR_STABLES_ETHEREUM.get(sde_coin_bytes)
    if par is None:
        raise ValueError(
            f"_curve_sde_asset_value_timeseries: SDE coin {sde_coin.hex} for venue "
            f"{venue.id} is not in KNOWN_PAR_STABLES_ETHEREUM — add it to sky_tokens.py."
        )
    _sym, coin_decimals = par

    pool_addr = venue.token.address.value
    holder = (venue.holder_override or prime.alm[venue.chain]).value
    chain_str = venue.chain.value

    rows = []
    current = period.start
    while current <= period.end:
        eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
        value = Decimal("0")
        try:
            block = block_resolver.block_at_or_before(chain_str, eod)
            pool_state = curve_pool_source.read_pool(chain_str, pool_addr, block)
            total_supply = pool_state.total_supply
            if total_supply > 0:
                coin_idx = next(
                    (i for i, c in enumerate(pool_state.coins) if c.value == sde_coin_bytes),
                    None,
                )
                if coin_idx is not None:
                    raw_coin = pool_state.balances[coin_idx]
                    alm_lp_raw = _balance_of(
                        venue.chain, venue.token.address, _Addr(holder), block,
                    )
                    alm_lp = Decimal(alm_lp_raw) / Decimal(10 ** venue.token.decimals)
                    pool_total = Decimal(total_supply) / Decimal(10 ** venue.token.decimals)
                    coin_usds = Decimal(raw_coin) / Decimal(10 ** coin_decimals)
                    value = (alm_lp / pool_total) * coin_usds
                    if cap_usd is not None and value > cap_usd:
                        value = cap_usd
                else:
                    _log.warning(
                        "curve SDE: venue %s pool %s does not contain SDE coin %s "
                        "at block %d — $0 for day %s.",
                        venue.id, pool_addr.hex(), sde_coin.hex, block, current,
                    )
        except Exception as exc:
            _log.warning(
                "curve SDE: RPC error for venue %s on %s; using $0 (error: %s).",
                venue.id, current, type(exc).__name__,
            )
        rows.append({"block_date": current, "cum_value": value})
        current = current + timedelta(days=1)
    return pd.DataFrame(rows)


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
    """Empty 6-column PSM timeseries frame.

    ``cum_balance`` = USDS-equivalent total = ``cum_usdc + cum_usds_leg +
    cum_susds`` (kept on the frame for at-a-glance reads / any external
    consumer that doesn't need the per-leg breakdown).
    """
    return pd.DataFrame({
        "block_date": [], "daily_net": [], "cum_balance": [],
        "cum_usdc": [], "cum_usds_leg": [], "cum_susds": [],
    })


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


def _psm3_susds_spread(psm_usds: pd.DataFrame | None, period: Period) -> Decimal:
    """30 bps daily-compounded Prime Revenue credit on the sUSDS slice of
    PSM3 holdings.

    The sUSDS leg of PSM3 is yield-bearing — the prime captures SSR
    automatically via ``convertToAssetValue`` growth of its PSM3 share. To
    keep the prime economically neutral on idle sUSDS (it shouldn't earn
    money for just parking capital), ``compute_sky_revenue`` charges full
    BR on this slice (no ``utilized`` reduction for the sUSDS leg), and
    this function credits back the 30 bps spread so the
    SSR-+-BR-charge-+-30-bps-credit composite nets to zero. See PRD §17.11.

    Formula: ``Σ_d cum_susds_d × daily_compounding_factor(BASE_RATE_OVER_SSR)``
    where d ranges over days in ``[period.start, period.end]``.
    """
    if psm_usds is None or psm_usds.empty or "cum_susds" not in psm_usds.columns:
        return Decimal("0")
    # Lazy import to avoid module-cycle (sky_revenue imports from _helpers).
    from ._helpers import daily_compounding_factor
    from .sky_revenue import BASE_RATE_OVER_SSR
    spread_factor = daily_compounding_factor(BASE_RATE_OVER_SSR)
    total = Decimal("0")
    current = period.start
    while current <= period.end:
        cum_susds = cum_at_or_before(psm_usds, "cum_susds", current)
        if cum_susds > 0:
            total += cum_susds * spread_factor
        current = current + timedelta(days=1)
    return total


def _aggregate_alm_usds(
    prime: Prime,
    period: Period,
    *,
    balance_source=None,
) -> pd.DataFrame:
    """Sum idle USDS balances held directly at each chain's ALM proxy.

    Loops over every chain in ``prime.alm``, fetches the USDS ERC-20 balance
    timeseries at the ALM address, and aggregates the daily nets into a single
    cross-chain series (same ``[block_date, daily_net, cum_balance]`` shape as
    ``_aggregate_psm_usds``).

    Chains where USDS is not yet registered in ``USDS_BY_CHAIN`` are skipped
    with a warning — treat this as a signal to add the address to sky_tokens.py.
    """
    from ..domain.sky_tokens import USDS_BY_CHAIN
    from ..normalize.balances import get_alm_balance_timeseries

    daily_by_date: dict = {}
    for chain in prime.alm:
        if chain not in period.pin_blocks:
            continue
        usds_token = USDS_BY_CHAIN.get(chain)
        if usds_token is None:
            _log.warning(
                "_aggregate_alm_usds: no USDS address known for chain %s "
                "(add it to USDS_BY_CHAIN in sky_tokens.py); skipping.",
                chain.value,
            )
            continue
        per_chain = get_alm_balance_timeseries(
            prime, chain, usds_token, period, source=balance_source,
        )
        for _, row in per_chain.iterrows():
            d = row["block_date"]
            daily_by_date[d] = daily_by_date.get(d, Decimal(0)) + _to_decimal(row["daily_net"])

    return _df_from_daily_dict(daily_by_date)


def _aggregate_psm_usds(
    prime: Prime,
    period: Period,
    *,
    balance_source,
    psm3_source=None,
    block_resolver=None,
    position_balance_source=None,
    convert_to_assets_source=None,
):
    """Sum PSM USDS-equivalent timeseries across every chain in
    ``prime.psm``, per leg. Per-chain timeseries are produced by
    ``get_psm_usds_timeseries``; this aggregates them into a single 6-column
    daily series (``[block_date, daily_net, cum_balance, cum_usdc,
    cum_usds_leg, cum_susds]``) consumable by ``compute_sky_revenue``.

    Each per-leg cumulative is forward-filled per chain across the full date
    set, so a chain that has no row on a given date contributes its last
    known value — preserving the "value-as-of-date" reading semantics that
    ``cum_at_or_before`` relies on.

    Returns an empty DataFrame if the prime has no PSM configured anywhere.

    Aggregation uses ``cum_balance`` (absolute snapshot/position) per chain,
    not ``daily_net``. For ``erc4626_shares``, cum_balance is the actual
    USDS-equivalent at each day's EoD block; daily_net is just the in-period
    delta. Summing daily_net and rebuilding a cumsum would omit the pre-period
    baseline balance (which can be $100M+) and produce a near-zero result.
    """
    if not prime.psm:
        return _empty_psm_df()

    # Coverage assertion (PRD §17.10): if a PSM is configured for a chain that
    # the orchestrator didn't resolve a pin_block for, the prior behavior was
    # to silently `continue` — which would silently inflate ``utilized`` (the
    # PSM holdings on that chain would be ignored, so the prime would be
    # charged BR on capital that's actually parked). Fail fast instead — a
    # missing pin_block here means the orchestrator's chain set is out of sync
    # with the YAML, and the only correct action is to fix the config.
    missing = [c.value for c in prime.psm if c not in period.pin_blocks]
    if missing:
        raise ValueError(
            f"PSM configured for chain(s) {missing} but no pin_block resolved "
            f"for them in this Period. Add the chain to the orchestrator's "
            f"chain set or remove the `psm:` block from the prime YAML."
        )

    per_chain_frames = []
    for chain in prime.psm:
        per_chain = get_psm_usds_timeseries(
            prime, chain, period,
            balance_source=balance_source,
            psm3_source=psm3_source,
            block_resolver=block_resolver,
            position_balance_source=position_balance_source,
            convert_to_assets_source=convert_to_assets_source,
        )
        if not per_chain.empty:
            per_chain_frames.append(per_chain[
                ["block_date", "cum_usdc", "cum_usds_leg", "cum_susds"]
            ])

    if not per_chain_frames:
        return _empty_psm_df()

    # Build the union of dates, forward-fill each chain's per-leg cum_X across
    # the full set, then sum across chains.
    all_dates = sorted({d for f in per_chain_frames for d in f["block_date"]})
    summed: dict = {
        d: {"cum_usdc": Decimal(0), "cum_usds_leg": Decimal(0), "cum_susds": Decimal(0)}
        for d in all_dates
    }
    for chain_df in per_chain_frames:
        chain_rows = {
            row["block_date"]: {
                "cum_usdc":     _to_decimal(row["cum_usdc"]),
                "cum_usds_leg": _to_decimal(row["cum_usds_leg"]),
                "cum_susds":    _to_decimal(row["cum_susds"]),
            }
            for _, row in chain_df.iterrows()
        }
        last = {"cum_usdc": Decimal(0), "cum_usds_leg": Decimal(0), "cum_susds": Decimal(0)}
        for d in all_dates:
            if d in chain_rows:
                last = chain_rows[d]
            for k in last:
                summed[d][k] += last[k]

    rows = sorted(summed.items())
    df = pd.DataFrame({
        "block_date":   [d for d, _ in rows],
        "cum_usdc":     [v["cum_usdc"]     for _, v in rows],
        "cum_usds_leg": [v["cum_usds_leg"] for _, v in rows],
        "cum_susds":    [v["cum_susds"]    for _, v in rows],
    })
    df["cum_balance"] = df["cum_usdc"] + df["cum_usds_leg"] + df["cum_susds"]
    if len(df) > 0:
        df["daily_net"] = df["cum_balance"].diff().fillna(df["cum_balance"].iloc[0])
    else:
        df["daily_net"] = Decimal(0)
    return df[["block_date", "daily_net", "cum_balance", "cum_usdc", "cum_usds_leg", "cum_susds"]]


def get_psm_usds_timeseries(
    prime: Prime, chain: Chain, period: Period,
    *,
    balance_source,
    psm3_source=None,
    block_resolver=None,
    position_balance_source=None,
    convert_to_assets_source=None,
):
    """USDS-equivalent the prime has parked at the PSM on ``chain``, per day.

    Returns a 6-column DataFrame ``[block_date, daily_net, cum_balance,
    cum_usdc, cum_usds_leg, cum_susds]``. Returns empty DataFrame if the
    prime has no PSM configured on this chain.

    Single supported mechanic today (``PsmKind.ERC4626_SHARES``, Spark PSM3):
    the ALM holds PSM3 shares which are *internal accounting* (no ERC-20
    Transfer events) and the rate uses a non-standard
    ``convertToAssetValue(uint256)``. We snapshot
    ``convertToAssetValue(shares(alm, b), b)`` at each day's EoD block,
    decompose into per-leg values (USDC / USDS / sUSDS) by reading reserve
    balances at the PSM3 contract and applying mainnet sUSDS pps for the
    sUSDS leg, then route each leg per PRD §17.11.
    """

    cfg = prime.psm.get(chain)
    if cfg is None or chain not in prime.alm:
        return _empty_psm_df()

    if cfg.kind == PsmKind.ERC4626_SHARES:
        # Spark PSM3. The pool holds three reserves — USDC, USDS, sUSDS — that
        # are treated differently by the settlement methodology (PRD §17.11):
        #
        #   USDS leg  → BR-reimbursed (subtracted from utilized) — the prime
        #               borrowed USDS, parked it at PSM3 ⇒ this slice is idle
        #   USDC leg  → Sky Direct Exposure (Atlas §A.2.3.2.2.3) — Sky takes
        #               the actual yield (≈ $0 for passive USDC reserves);
        #               utilized NOT reduced for this slice
        #   sUSDS leg → utilized NOT reduced; prime earns 30 bps spread
        #               (= BR − SSR) on its USDS-equivalent value — the sUSDS
        #               share price already returns SSR to the prime, so
        #               crediting full BR-reimbursement on top would double-
        #               count (same rule as sUSDS in regular allocations,
        #               PRD §17.7 + RULES §5).
        #
        # To apply these rules we decompose Spark's claim per day into the 3
        # legs by reading ``balanceOf(token, psm3)`` for each leg and using
        # mainnet sUSDS pps (the L2 sUSDS is a 1:1 bridge — verified to 4
        # decimals against Ethereum sUSDS ``convertToAssets(1e18)``):
        #
        #   pool_total_usds_eq = USDC_face + USDS_face + sUSDS_face × sUSDS_pps
        #   spark_share        = convertToAssetValue(spark_shares) / pool_total
        #   spark_per_leg      = spark_share × leg_value
        from ..normalize.registry import (
            get_psm3_source as _get_psm3,
            get_position_balance_source as _get_pos_bal,
            get_convert_to_assets_source as _get_c2a,
        )
        from ..domain.sky_tokens import PSM3_LEG_TOKENS, sUSDS_ETHEREUM
        if block_resolver is None:
            raise ValueError(
                "get_psm_usds_timeseries(kind=erc4626_shares) requires a "
                "block_resolver to read PSM3 shares at each day's EoD block"
            )
        if chain not in PSM3_LEG_TOKENS:
            raise ValueError(
                f"PSM3 leg-token registry has no entry for {chain.value}. "
                f"Add USDC/USDS/sUSDS addresses for the chain to "
                f"settle.domain.sky_tokens.PSM3_LEG_TOKENS."
            )
        psm3 = psm3_source if psm3_source is not None else _get_psm3()
        pos_bal = position_balance_source if position_balance_source is not None else _get_pos_bal()
        c2a = convert_to_assets_source if convert_to_assets_source is not None else _get_c2a()
        scale = Decimal(10**18)
        usdc_scale = Decimal(10**6)
        holder = prime.alm[chain].value
        leg_tokens = PSM3_LEG_TOKENS[chain]
        usdc_addr = leg_tokens["USDC"].address.value
        usds_addr = leg_tokens["USDS"].address.value
        susds_addr = leg_tokens["sUSDS"].address.value
        psm3_addr = cfg.address.value

        # Per-day RPC failures (drpc upstream flake, contract-not-yet-deployed
        # at very early blocks) shouldn't kill the whole chain's PSM3
        # timeseries — that would silently inflate sky_revenue by the missing
        # PSM holdings. Treat a failed day as "carry forward yesterday's
        # value" (no movement) and log the gap so it's auditable.
        from ..extract.rpc import RPCError
        import requests as _requests

        _ZERO_LEGS = (Decimal(0), Decimal(0), Decimal(0))

        def _legs_at(
            day,
            fallback: tuple[Decimal, Decimal, Decimal] | None = None,
        ) -> tuple[Decimal, Decimal, Decimal]:
            """Spark's USDS-equivalent claim split into (usdc, usds, susds)
            at this day's EoD block."""
            eod = datetime.combine(day, time.max, tzinfo=timezone.utc)
            try:
                block = block_resolver.block_at_or_before(chain.value, eod)
                shares = psm3.shares_of(
                    chain=chain.value, psm3=psm3_addr,
                    holder=holder, block=block,
                )
                if shares <= 0:
                    return _ZERO_LEGS
                spark_claim_raw = psm3.convert_to_asset_value(
                    chain=chain.value, psm3=psm3_addr,
                    num_shares=shares, block=block,
                )
                spark_claim = Decimal(spark_claim_raw) / scale

                # PSM3 leg reserves at this block. USDC is 6-decimal; USDS,
                # sUSDS are 18-decimal.
                usdc_raw  = pos_bal.balance_at(chain.value, usdc_addr,  psm3_addr, block)
                usds_raw  = pos_bal.balance_at(chain.value, usds_addr,  psm3_addr, block)
                susds_raw = pos_bal.balance_at(chain.value, susds_addr, psm3_addr, block)
                usdc_val   = Decimal(usdc_raw)  / usdc_scale
                usds_val   = Decimal(usds_raw)  / scale
                susds_face = Decimal(susds_raw) / scale

                # sUSDS pps from Ethereum (L2 sUSDS is a 1:1 bridge of mainnet
                # sUSDS — only mainnet exposes ``convertToAssets``).
                eth_block = block_resolver.block_at_or_before(Chain.ETHEREUM.value, eod)
                susds_pps_raw = c2a.convert_to_assets(
                    chain=Chain.ETHEREUM.value, vault=sUSDS_ETHEREUM.address.value,
                    shares=10**18, block=eth_block,
                )
                susds_pps = Decimal(susds_pps_raw) / scale
                susds_val = susds_face * susds_pps

                pool_total = usdc_val + usds_val + susds_val
                if pool_total <= 0:
                    # Pool has zero reserves but Spark has a non-zero claim
                    # → impossible. Fall back to assigning the entire claim
                    # to the USDS leg (safest classification: pretend it's
                    # plain idle USDS).
                    _log.warning(
                        "PSM3 pool_total=0 but spark_claim=$%s on %s @ %s; "
                        "classifying full claim as USDS leg.",
                        f"{spark_claim:,.2f}", chain.value, day,
                    )
                    return (Decimal(0), spark_claim, Decimal(0))

                spark_share = spark_claim / pool_total
                return (
                    spark_share * usdc_val,
                    spark_share * usds_val,
                    spark_share * susds_val,
                )
            except (RPCError, _requests.HTTPError, _requests.ConnectionError, _requests.Timeout) as e:
                if fallback is None:
                    raise
                _log.warning(
                    "PSM3 read failed on %s for %s @ %s; carrying forward "
                    "legs=(usdc=$%s, usds=$%s, susds=$%s) (error: %s). PSM "
                    "USDS-equiv may be slightly stale for this day.",
                    chain.value, psm3_addr.hex(), day,
                    f"{fallback[0]:,.2f}", f"{fallback[1]:,.2f}", f"{fallback[2]:,.2f}",
                    type(e).__name__,
                )
                return fallback

        # One snapshot per day across [period.start, period.end]. The init
        # read (period.start - 1) cannot fall back — a missing baseline
        # means we can't compute period flows correctly, so let it raise.
        days = [period.start + timedelta(days=i) for i in range((period.end - period.start).days + 1)]
        cur_legs = _legs_at(period.start - timedelta(days=1))
        block_dates: list = []
        daily_net: list[Decimal] = []
        cum_usdc: list[Decimal] = []
        cum_usds_leg: list[Decimal] = []
        cum_susds: list[Decimal] = []
        for day in days:
            legs = _legs_at(day, fallback=cur_legs)
            block_dates.append(day)
            cum_balance_today = sum(legs)
            cum_balance_yday = sum(cur_legs)
            daily_net.append(cum_balance_today - cum_balance_yday)
            cum_usdc.append(legs[0])
            cum_usds_leg.append(legs[1])
            cum_susds.append(legs[2])
            cur_legs = legs

        if all(u == 0 and s == 0 and z == 0 for u, s, z in zip(cum_usdc, cum_usds_leg, cum_susds)):
            return _empty_psm_df()
        df = pd.DataFrame({
            "block_date":   block_dates,
            "daily_net":    daily_net,
            "cum_usdc":     cum_usdc,
            "cum_usds_leg": cum_usds_leg,
            "cum_susds":    cum_susds,
        })
        # ``cum_balance`` = total USDS-equivalent (sum of the 3 legs). Kept for
        # backward compatibility with any consumer that doesn't yet know about
        # the legs; new consumers should read the per-leg columns directly.
        df["cum_balance"] = df["cum_usdc"] + df["cum_usds_leg"] + df["cum_susds"]
        return df

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
    from .sky_revenue import BASE_RATE_OVER_SSR
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

    # Spread = BR − SSR = BASE_RATE_OVER_SSR (30bps).
    spread_daily_factor = daily_compounding_factor(BASE_RATE_OVER_SSR)

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


def _log_sky_revenue_debug(
    daily: pd.DataFrame,
    sde_per_venue: list[tuple[str, pd.DataFrame]],
    breakdown: list,
    sky_rev_br: Decimal,
    sde_revenue: Decimal,
) -> None:
    """Log a full daily breakdown of sky_revenue components at INFO level.

    Emits three sections:
      1. Day-by-day table: debt, each deduction, utilized, APY, daily BR revenue.
      2. Per-SDE-venue daily asset-value table (if any SDEs active).
      3. SDE period-total revenue by venue + grand totals.
    """
    # ── section 1: daily utilized decomposition ──────────────────────────────
    hdr = (
        f"  {'date':10s}  {'cum_debt':>10s}  {'alm_usds':>9s}  "
        f"{'psm_usds':>9s}  {'sde_av':>9s}  {'curve':>9s}  "
        f"{'lending':>9s}  {'utilized':>10s}  "
        f"{'ssr%':>6s}  {'br%':>6s}  {'daily_rev':>12s}"
    )
    lines = [
        "",
        "  ╔══ sky_revenue daily breakdown ══════════════════════════════════════════════════════════════╗",
        f"  {hdr}",
        "  " + "─" * len(hdr),
    ]
    for _, row in daily.iterrows():
        lines.append(
            f"  {str(row['date']):10s}  "
            f"{float(row['cum_debt'])/1e6:>9.2f}M  "
            f"{float(row['alm_usds'])/1e6:>8.2f}M  "
            f"{float(row['psm_usds'])/1e6:>8.2f}M  "
            f"{float(row['sde_av'])/1e6:>8.2f}M  "
            f"{float(row['curve_idle'])/1e6:>8.2f}M  "
            f"{float(row['lending_idle'])/1e6:>8.2f}M  "
            f"{float(row['utilized'])/1e6:>9.2f}M  "
            f"{row['ssr_apy']*100:>5.2f}%  "
            f"{row['base_apy']*100:>5.2f}%  "
            f"${float(row['daily_sky_rev']):>11,.2f}"
        )
    lines.append("  " + "─" * len(hdr))
    lines.append(
        f"  {'TOTAL BR':>74s}  ${float(sky_rev_br):>11,.2f}"
    )

    # ── section 2: per-SDE-venue daily asset value ────────────────────────────
    if sde_per_venue:
        sde_venue_ids = [vid for vid, _ in sde_per_venue]
        # Build a wide frame: date × venue_id → cum_value (as float for display)
        frames = {}
        for vid, df in sde_per_venue:
            col = df.set_index("block_date")["cum_value"].apply(float).rename(vid)
            frames[vid] = col
        wide = pd.concat(frames.values(), axis=1).fillna(0.0)
        wide["total"] = wide[sde_venue_ids].sum(axis=1)

        sde_hdr = (
            f"  {'date':10s}  "
            + "  ".join(f"{v:>9s}" for v in sde_venue_ids)
            + f"  {'total':>9s}"
        )
        lines += [
            "",
            f"  ── SDE asset value per venue (daily, $M) ──",
            sde_hdr,
            "  " + "─" * (len(sde_hdr) - 2),
        ]
        for dt, wrow in wide.iterrows():
            row_str = f"  {str(dt):10s}  "
            row_str += "  ".join(f"{wrow[v]/1e6:>8.2f}M" for v in sde_venue_ids)
            row_str += f"  {wrow['total']/1e6:>8.2f}M"
            lines.append(row_str)

    # ── section 3: SDE period-total revenue by venue ─────────────────────────
    sde_venues = [vr for vr in breakdown if vr.sd_revenue != Decimal("0")]
    if sde_venues:
        lines += [
            "",
            f"  ── SDE revenue by venue (period totals) ──",
            f"  {'venue':6s}  {'label':30s}  {'sd_share':>8s}  {'value_som':>14s}  {'sd_revenue':>14s}",
            "  " + "─" * 80,
        ]
        for vr in sde_venues:
            lines.append(
                f"  {vr.venue_id:6s}  {(vr.label or '')[:30]:30s}  "
                f"{float(vr.sd_share)*100:>7.1f}%  "
                f"${float(vr.value_som):>13,.2f}  "
                f"${float(vr.sd_revenue):>13,.2f}"
            )
        lines.append("  " + "─" * 80)
        lines.append(f"  {'SDE total':>52s}  ${float(sde_revenue):>13,.2f}")

    lines += [
        "",
        f"  sky_rev_br (BR on utilized−SDE):  ${float(sky_rev_br):>14,.2f}",
        f"  sde_revenue (Σ actual × sd_share): ${float(sde_revenue):>14,.2f}",
        f"  sky_revenue total:                 ${float(sky_rev_br + sde_revenue):>14,.2f}",
        "  ╚══════════════════════════════════════════════════════════════════════════════════════════════╝",
        "",
    ]
    _log.info("\n".join(lines))


def compute_monthly_pnl(
    prime: Prime,
    month: Month,
    *,
    sources: Sources | None = None,
    pin_blocks_eom: dict[Chain, int] | None = None,
    pin_blocks_som: dict[Chain, int] | None = None,
    sky_only: bool = False,
) -> MonthlyPnL:
    """Compute the full monthly settlement for ``prime`` × ``month``.

    Block resolution: by default, EoM and SoM blocks are resolved live via RPC
    (one binary search per chain, ~25 RPC calls each). Tests can supply both
    dicts explicitly to skip RPC entirely.

    When ``sky_only=True`` the per-venue pricing loop (step 3) is restricted to
    SDE venues only, and ``compute_agent_rate`` is skipped entirely. This is
    substantially faster because it avoids the hundreds of per-venue RPC calls
    needed for prime_agent_revenue. The returned ``MonthlyPnL`` has
    ``prime_agent_revenue=0`` and ``agent_rate=0``; ``sky_revenue`` is fully
    accurate (all utilized components + SDE revenue are still computed).
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

    # Resolve EoM and SoM pin blocks for all chains in parallel (two concurrent
    # ThreadPoolExecutors, one per anchor). Each pool already parallelises across
    # chains internally, so for Spark (6 chains × 2 anchors) this cuts ~300
    # sequential RPC calls down to the cost of ~25 (one chain, one anchor).
    if pin_blocks_eom is None or pin_blocks_som is None:
        from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _ac
        _log.info(
            "resolving pin blocks for %d chain(s) (EoM + SoM in parallel)...",
            len(prime.chains),
        )
        eom_anchor = period_unpinned.end_eod_utc
        som_anchor = _previous_day_eod_utc(period_unpinned.start)
        with _TPE(max_workers=2) as _outer:
            _fut_eom = (
                _outer.submit(_resolve_pin_blocks, eom_anchor, prime.chains, resolver)
                if pin_blocks_eom is None else None
            )
            _fut_som = (
                _outer.submit(_resolve_pin_blocks, som_anchor, prime.chains, resolver)
                if pin_blocks_som is None else None
            )
            if _fut_eom is not None:
                pin_blocks_eom = _fut_eom.result()
            if _fut_som is not None:
                pin_blocks_som = _fut_som.result()
        _log.info("pin blocks resolved: eom=%s  som=%s", pin_blocks_eom, pin_blocks_som)

    # 1b. Upgrade to DuneBlockResolver for every chain that has Dune coverage.
    # One Dune query per chain replaces every per-day RPC binary-search (~25
    # eth_getBlockByNumber calls per anchor). For Spark (6 chains × 28 days ×
    # multiple timeseries per chain), this eliminates thousands of RPC calls.
    # Chains without a Dune blocks table fall back silently to RPCBlockResolver.
    # Queries are issued in parallel via a ThreadPoolExecutor.
    #
    # Dune chain → blocks table coverage (as of 2026-05):
    _DUNE_BLOCK_CHAINS = frozenset({
        "ethereum", "base", "arbitrum", "optimism", "avalanche_c",
        # unichain, plume, monad: not yet in Dune spellbook — use RPC
    })
    if sources.block_resolver is None and pin_blocks_eom:
        import os as _os
        if _os.environ.get("DUNE_API_KEY"):
            try:
                from concurrent.futures import ThreadPoolExecutor as _TPE2, as_completed as _ac2
                from ..normalize.sources.dune_block_resolver import (
                    DuneBlockResolver as _DBR,
                    MultiChainBlockResolver as _MCR,
                )
                from ..normalize.sources.rpc_block_resolver import RPCBlockResolver as _RBR
                dune_chains = {c for c in prime.chains if c.value in _DUNE_BLOCK_CHAINS}
                rpc_chains  = prime.chains - dune_chains
                _log.info(
                    "upgrading block resolver: DuneBlockResolver for %s, "
                    "RPCBlockResolver for %s",
                    sorted(c.value for c in dune_chains),
                    sorted(c.value for c in rpc_chains),
                )
                def _make_dune_resolver(chain: "Chain") -> "tuple[Chain, _DBR]":
                    dbr = _DBR(
                        chain=chain.value,
                        start_date=prime.start_date,
                        end_date=period_unpinned.end,
                        pin_block=pin_blocks_eom[chain],
                    )
                    return chain, dbr
                with _TPE2(max_workers=len(dune_chains) or 1) as _pool2:
                    _futs = {_pool2.submit(_make_dune_resolver, c): c for c in dune_chains}
                    per_chain: dict[str, object] = {}
                    for _f in _ac2(_futs):
                        _chain, _dbr = _f.result()
                        per_chain[_chain.value] = _dbr
                        _log.info(
                            "  DuneBlockResolver(%s): %d dates, %s → %s",
                            _chain.value, len(_dbr._dates),
                            _dbr._dates[0], _dbr._dates[-1],
                        )
                _rpc_fallback = _RBR()
                for c in rpc_chains:
                    per_chain[c.value] = _rpc_fallback
                resolver = _MCR(per_chain)
            except Exception as _e:
                _log.warning(
                    "DuneBlockResolver init failed (%s) — falling back to RPC for all chains", _e
                )

    period = Period(period_unpinned.start, period_unpinned.end, pin_blocks=pin_blocks_eom)

    _log.info("step 2: gathering Dune/normalize inputs (debt, balances, SSR)...")
    # 2. Gather Normalize inputs for sky_revenue + agent_rate (Ethereum-only).
    _log.info("  2a: debt timeseries...")
    debt = get_debt_timeseries(prime, period, source=sources.debt)
    if not sky_only:
        _log.info("  2b: subproxy USDS balance...")
        sub_usds = get_subproxy_balance_timeseries(
            prime, Chain.ETHEREUM, USDS_ETHEREUM, period, source=sources.balance,
        )
        _log.info("  2c: subproxy sUSDS shares...")
        sub_susds_shares = get_subproxy_balance_timeseries(
            prime, Chain.ETHEREUM, sUSDS_ETHEREUM, period, source=sources.balance,
        )
        # Convert sUSDS shares → USDS-denominated cost-basis principal:
        # ``principal = Σ daily_net_shares × pps_at_that_day's_eod_block``. This is
        # the deposit-time value, NOT the current value (which includes accrued
        # SSR — using current value would double-count savings). Used by
        # agent_rate (earning base); NOT passed to sky_revenue (subproxy balances
        # are treasury/risk capital, not pure ilk-debt proceeds).
        _log.info("  2d: sUSDS → principal conversion (RPC per day)...")
        sub_susds = _susds_shares_to_principal(
            sub_susds_shares,
            sources=sources,
            block_resolver=resolver,
            chain=Chain.ETHEREUM,
        )
    else:
        _log.info("  2b-d: skipped (sky_only mode — subproxy balances not needed)")
        sub_usds = sub_susds = None
    _log.info("  2e: ALM USDS balance (all chains)...")
    alm_usds = _aggregate_alm_usds(
        prime, period, balance_source=sources.balance,
    )
    # Sum PSM USDS-equivalent across ALL chains where the prime has a PSM
    # configured. The prime's debt (cum_debt) is Ethereum-only (Vat), but
    # USDS-equivalent capital parked at L2 PSM3 (Spark on Base / Arbitrum /
    # Optimism / Unichain) was funded from that debt and reduces utilized.
    # Sky's mainnet PSM stack is non-custodial and is intentionally NOT
    # configured (see PRD §17.11) — primes never accumulate balances there.
    _log.info("  2f: PSM USDS aggregate...")
    psm_usds = _aggregate_psm_usds(
        prime, period,
        balance_source=sources.balance,
        psm3_source=sources.psm3,
        block_resolver=resolver,
        position_balance_source=sources.position_balance,
        convert_to_assets_source=sources.convert_to_assets,
    )
    # Neutralising 30 bps spread credit on the sUSDS slice of PSM3 holdings
    # (PRD §17.11). Sky charges full BR on this slice (utilized NOT reduced
    # for sUSDS in compute_sky_revenue) AND pays SSR to sUSDS holders
    # (= prime via PSM3 share appreciation). Crediting 30 bps back keeps the
    # composite (+SSR − BR + 30 bps = 0) economically neutral on idle sUSDS.
    psm3_susds_spread = _psm3_susds_spread(psm_usds, period)
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
    _log.info("  2g: SSR history...")
    ssr = get_ssr_history(prime, period, source=sources.ssr)
    _log.info("  step 2 complete.")

    # SDE table — config-driven Sky Direct exposures (replaces the legacy
    # ``Venue.sky_direct: bool`` flag). Empty table = no venues are SDE.
    sde_table = load_sde_table()
    # Each entry is (venue_id: str, df: pd.DataFrame) so the debug logger can
    # show a per-SDE-venue daily asset-value table.
    sde_asset_value_per_venue: list[tuple[str, pd.DataFrame]] = []

    import time as _time
    n_venues = sum(1 for v in prime.venues if not v.skip)
    _log.info("step 3: per-venue pricing for %d venue(s)...", n_venues)
    # 3. Per-venue: value at SoM + EoM, inflow timeseries.
    venue_inputs: list[VenueRevenueInputs] = []
    _venue_idx = 0
    for venue in prime.venues:
        if venue.skip:
            # Excluded from MSC — typically venues whose NAV oracle is
            # untrusted or whose underlying is too volatile (e.g. Avalanche
            # cross-chain RWAs without a reliable feed). Logged once for
            # provenance.
            _log.info(
                "  [skip] %s (%s, %s) — venue.skip=True.",
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
            _log.warning(
                "  [skip] %s (%s, %s) — Spark Savings V2 compute path not yet implemented.",
                venue.id, venue.token.symbol, venue.chain.value,
            )
            continue
        # In sky_only mode, check the SDE table up-front so we can skip all
        # non-SDE venues before doing any RPC / pricing work.
        _early_sde = sde_table.overlaps_venue(prime.id, venue.id, period.start, period.end)
        if sky_only and _early_sde is None:
            _log.info("  [sky_only] skipping %s (no active SDE entry)", venue.id)
            continue


        _venue_idx += 1
        _venue_t0 = _time.monotonic()
        _log.info(
            "  [%d/%d] %s  %s/%s  starting...",
            _venue_idx, n_venues, venue.id, venue.chain.value,
            venue.pricing_category.value,
        )
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
                # Prime Revenue = (BR − SSR) × value_som × n_days = 30bps spread.
                # `value_som` already computed above via convertToAssets at SoM block.
                from .sky_revenue import BASE_RATE_OVER_SSR
                spread_daily = daily_compounding_factor(BASE_RATE_OVER_SSR)
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

        # SDE classification — already resolved above as _early_sde (before the
        # sky_only early-exit). Reuse it here to avoid a second table lookup.
        sde_entry = _early_sde
        if sde_entry is not None:
            ciuc = venue.curve_idle_usds
            if ciuc is not None and ciuc.sde_coin is not None:
                # Curve LP pool SDE: the exposure is a par-stable coin in the pool
                # (e.g. USDT in sUSDS/USDT). Use pool-state coin balance rather than
                # an RWA NAV oracle. See CurveIdleUsdsConfig.sde_coin for details.
                from ..normalize.sources.curve_pool import CurvePoolSource as _CPS
                _curve_src = (
                    sources.curve_pool
                    if sources.curve_pool is not None
                    else _CPS()
                )
                sde_asset_value_per_venue.append((venue.id, _curve_sde_asset_value_timeseries(
                    prime, venue, period,
                    sde_coin=ciuc.sde_coin,
                    curve_pool_source=_curve_src,
                    block_resolver=resolver,
                    cap_usd=sde_entry.cap_usd,
                )))
            else:
                # Cat E (RWA tranche) path — requires nav_oracle on the venue.
                bsrc = sources.balance if sources.balance is not None else get_balance_source()

                def _sd_nav(block, _v=venue, _br=resolver, _nr=sources.nav_oracle_resolver):
                    return _resolve_rwa_nav(_v, block, block_resolver=_br, resolver=_nr)

                sde_asset_value_per_venue.append((venue.id, _sde_asset_value_timeseries(
                    prime, venue, period,
                    balance_source=bsrc,
                    block_resolver=resolver,
                    nav_at_block=_sd_nav,
                    cap_usd=sde_entry.cap_usd,
                )))

        _log.info(
            "  [%d/%d] %s  done in %.1fs  som=$%.0f  eom=$%.0f%s",
            _venue_idx, n_venues, venue.id,
            _time.monotonic() - _venue_t0,
            value_som, value_eom,
            "  [SDE]" if sde_entry is not None else "",
        )
        venue_inputs.append(VenueRevenueInputs(
            venue=venue, value_som=value_som, value_eom=value_eom,
            inflow_timeseries=inflow_ts,
            sde_entry=sde_entry,
            actual_revenue_override=susds_spread,
        ))

    _log.info("step 4: computing revenue components...")
    # 4. Compute revenue components.
    if sky_only:
        # subproxy balances were not fetched — agent_rate is meaningless.
        # venue_inputs contains only SDE venues (non-SDE skipped in step 3),
        # so prime-side revenue is small / usually zero and we set it to 0.
        _, breakdown = compute_prime_agent_revenue(period, venue_inputs)
        agent_rate = Decimal("0")
        prime_rev = Decimal("0")
    else:
        agent_rate = compute_agent_rate(period, sub_usds, sub_susds, ssr)
        prime_rev, breakdown = compute_prime_agent_revenue(period, venue_inputs)
        # Add 30bps spread on sUSDS held inside Curve LP pools. This is computed
        # separately from the venue loop because the data comes from the Curve
        # pool daily snapshots, not from the venue's SoM/EoM position values.
        # Same shape, different source: 30 bps spread on the sUSDS slice of PSM3
        # holdings (PRD §17.11) — neutralises the SSR + BR-charge composite on
        # the sUSDS leg so the prime nets to zero on idle sUSDS.
        prime_rev = prime_rev + curve_susds_spread + psm3_susds_spread
    # SDE revenue (Σ actual × sd_share across venues) flows directly to Sky.
    sde_revenue = sum((vr.sd_revenue for vr in breakdown), Decimal("0"))

    # Aggregate per-venue daily SDE asset-value into one frame so
    # compute_sky_revenue_daily can subtract it from utilized (SDE positions
    # already pay Sky directly via sde_revenue; charging BR would double-bill).
    if sde_asset_value_per_venue:
        sde_av_total = (
            pd.concat([df for _, df in sde_asset_value_per_venue])
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
    sky_rev_br, sky_rev_daily = compute_sky_revenue_daily(
        period, debt, alm_usds, ssr, psm_usds=psm_usds,
        subsidy_config=prime.subsidy,
        ref_rate_history=ref_rate_history,
        sde_asset_value=sde_av_total,
        curve_idle_usds=curve_idle_usds,
        lending_idle_usds=lending_idle_usds,
    )
    # Sky's full claim: BR on (utilized − SDE) + actual SDE revenue.
    sky_rev = sky_rev_br + sde_revenue

    _log_sky_revenue_debug(
        sky_rev_daily,
        sde_asset_value_per_venue,
        breakdown,
        sky_rev_br,
        sde_revenue,
    )
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
