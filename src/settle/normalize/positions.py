"""Canonical position balance + position value primitives.

Position value = balance × unit_price for fungible holdings.

Uniswap V3 positions are non-fungible NFTs, so they bypass the balance × price
formulation: ``get_position_value`` for ``lp_kind=uniswap_v3`` enumerates
NFT positions, computes ``(amount0, amount1)`` per position via tick math, and
sums each amount × per-token par-stable price to a USD total.
"""

from __future__ import annotations

from decimal import Decimal

from ..domain.primes import Prime, Venue
from ..domain.sky_tokens import KNOWN_PAR_STABLES_ETHEREUM
from .prices import UnsupportedPricingError, get_unit_price
from .protocols import IConvertToAssetsSource, IPositionBalanceSource, IV3PositionSource
from .registry import get_position_balance_source
from .sources.uniswap_v3 import RPCUniswapV3PositionSource


def get_position_balance(
    prime: Prime,
    venue: Venue,
    block: int,
    *,
    source: IPositionBalanceSource | None = None,
) -> Decimal:
    """Token-units balance of `venue.token` held by `prime.alm[venue.chain]` at `block`.

    For rebasing tokens (Aave aTokens / SparkLend spTokens), the returned amount
    is the *rebased* balance — interest already accrued. For ERC-4626 vaults,
    this is share count; multiply by the unit price (which folds in `convertToAssets`)
    to get USD value.
    """
    if venue.chain not in prime.alm:
        raise ValueError(
            f"Prime {prime.id!r} has no ALM on {venue.chain.value} "
            f"(needed for venue {venue.id})"
        )
    # Uniswap V3 positions aren't fungible ERC-20 — there's no scalar "balance"
    # of the pool. Use ``get_position_value`` directly, which enumerates NFTs
    # and sums redeemable amounts.
    if venue.lp_kind == "uniswap_v3":
        raise UnsupportedPricingError(
            f"Venue {venue.id} (Uni V3): no scalar balance defined for non-fungible "
            "NFT positions. Call get_position_value(prime, venue, block) instead."
        )
    holder = prime.alm[venue.chain]
    src = source if source is not None else get_position_balance_source()
    raw = src.balance_at(
        chain=venue.chain.value,
        token=venue.token.address.value,
        holder=holder.value,
        block=block,
    )
    return Decimal(raw) / Decimal(10 ** venue.token.decimals)


def get_position_value(
    prime: Prime,
    venue: Venue,
    block: int,
    *,
    balance_source: IPositionBalanceSource | None = None,
    erc4626_source: IConvertToAssetsSource | None = None,
    v3_position_source: IV3PositionSource | None = None,
    curve_pool_source=None,
    block_resolver=None,
    nav_oracle_resolver=None,
) -> Decimal:
    """USD value of the position at ``block``.

    Standard path: ``balance × unit_price``. Uniswap V3 positions take a
    dedicated path because NFTs aren't fungible — see ``_uniswap_v3_value``.

    ``nav_oracle_resolver`` (optional) overrides the registry lookup for
    Cat E (RWA) venues. Used by acceptance scripts to inject historical-NAV
    overrides for blocks where the live oracle hadn't started writing yet.
    """
    if venue.lp_kind == "uniswap_v3":
        if v3_position_source is None:
            # Honor a non-canonical NFPM if the venue config declares one.
            overrides = (
                {venue.chain: venue.nft_position_manager}
                if venue.nft_position_manager is not None
                else None
            )
            v3_position_source = RPCUniswapV3PositionSource(nfpm_per_chain=overrides)
        return _uniswap_v3_value(prime, venue, block, source=v3_position_source)

    balance = get_position_balance(prime, venue, block, source=balance_source)
    price = get_unit_price(
        venue, block,
        erc4626_source=erc4626_source,
        curve_pool_source=curve_pool_source,
        block_resolver=block_resolver,
        nav_oracle_resolver=nav_oracle_resolver,
    )
    return balance * price


def _uniswap_v3_value(
    prime: Prime,
    venue: Venue,
    block: int,
    *,
    source: IV3PositionSource,
) -> Decimal:
    """Sum redeemable USD value across all V3 NFT positions in the target pool.

    Each position contributes ``amount0 × p(token0) + amount1 × p(token1)``
    where amounts include both liquidity-implied principal and uncollected
    fees, and ``p(•)`` is the par-stable price ($1 for tokens in the registry).

    Phase 2.A.5 only handles par-stable underlyings on Ethereum. Pools with
    yield-bearing or non-par tokens raise ``UnsupportedPricingError``.
    """
    if venue.chain.value != "ethereum":
        raise UnsupportedPricingError(
            f"Venue {venue.id}: V3 pricing only registered for ethereum in Phase 2.A "
            f"(needed: par-stable registry for chain {venue.chain.value!r})"
        )
    holder = prime.alm[venue.chain]
    positions = source.positions_in_pool(
        chain=venue.chain.value,
        owner=holder.value,
        pool=venue.token.address.value,
        block=block,
    )
    if not positions:
        return Decimal("0")

    registry = KNOWN_PAR_STABLES_ETHEREUM
    total = Decimal("0")
    for p in positions:
        for token, amount_raw in ((p.token0, p.amount0), (p.token1, p.amount1)):
            if amount_raw == 0:
                continue
            info = registry.get(token.value)
            if info is None:
                raise UnsupportedPricingError(
                    f"V3 position {p.token_id}: token {token.hex} is not in the "
                    "par-stable registry — recursive pricing is Phase 2.B+."
                )
            _symbol, decimals = info
            total += Decimal(amount_raw) / Decimal(10**decimals)   # par-stable @ $1
    return total


def _uniswap_v3_inflow_timeseries(
    prime: Prime,
    venue: Venue,
    from_block: int,
    to_block: int,
    *,
    source: IV3PositionSource,
    block_to_date,
):
    """Per-day USD inflow into V3 positions in the target pool, derived from
    NFPM ``IncreaseLiquidity`` / ``DecreaseLiquidity`` events.

    Signed event amounts are converted to USD via the par-stable registry
    (Phase 2.A — same scope as ``_uniswap_v3_value``) and bucketed by
    event-block date. Returns a DataFrame with columns
    ``[block_date, daily_inflow, cum_inflow]`` matching the Dune-backed
    ``directed_inflow_timeseries`` shape so Compute can treat all venues
    uniformly.

    ``block_to_date`` is a callable ``(block_number) -> date`` injected by
    the caller (typically wraps RPC ``block_timestamp``) so this layer doesn't
    import from extract directly.
    """
    import pandas as pd

    if venue.chain.value != "ethereum":
        raise UnsupportedPricingError(
            f"Venue {venue.id}: V3 inflow only registered for ethereum in Phase 2.A"
        )
    holder = prime.alm[venue.chain]
    events = source.liquidity_events_in_pool(
        chain=venue.chain.value,
        owner=holder.value,
        pool=venue.token.address.value,
        from_block=from_block,
        to_block=to_block,
    )
    empty = pd.DataFrame({
        "block_date": [], "daily_inflow": [], "cum_inflow": [],
    })
    if not events:
        return empty

    # Token0/token1 addresses live on the pool (and on every position struct).
    # Try to_block first; if the holder fully exited mid-period, fall back to
    # from_block (where positions had to exist for the events to fire). The
    # math is well-defined either way — events carry signed amounts; we only
    # need a position snapshot to look up token0/token1 decimals.
    snapshot = source.positions_in_pool(
        chain=venue.chain.value,
        owner=holder.value,
        pool=venue.token.address.value,
        block=to_block,
    )
    if not snapshot:
        snapshot = source.positions_in_pool(
            chain=venue.chain.value,
            owner=holder.value,
            pool=venue.token.address.value,
            block=from_block,
        )
    if not snapshot:
        raise UnsupportedPricingError(
            f"Venue {venue.id}: V3 inflow events present but no positions "
            "found at from_block or to_block — pool-token lookup unsupported."
        )
    ref = snapshot[0]
    info0 = KNOWN_PAR_STABLES_ETHEREUM.get(ref.token0.value)
    info1 = KNOWN_PAR_STABLES_ETHEREUM.get(ref.token1.value)
    if info0 is None or info1 is None:
        raise UnsupportedPricingError(
            f"Venue {venue.id}: V3 pool tokens not in par-stable registry — "
            "recursive pricing is Phase 2.B+."
        )
    _, dec0 = info0
    _, dec1 = info1

    rows = [
        {
            "block_date": block_to_date(ev.block_number),
            "daily_inflow": (
                Decimal(ev.amount0) / Decimal(10**dec0)
                + Decimal(ev.amount1) / Decimal(10**dec1)
            ),
        }
        for ev in events
    ]
    daily = (
        pd.DataFrame(rows)
        .groupby("block_date", as_index=False)["daily_inflow"]
        .sum()
        .sort_values("block_date")
        .reset_index(drop=True)
    )
    daily["cum_inflow"] = daily["daily_inflow"].cumsum()
    return daily


def _atoken_index_weighted_inflow(
    prime: Prime,
    venue: Venue,
    som_block: int,
    eom_block: int,
    *,
    period_end_date,
    scaled_balance_at,
    balance_at,
):
    """Closed-form rebasing-token inflow for Cat C/D (Aave aToken, SparkLend
    spToken).

    Aave V3 aTokens and SparkLend spTokens both maintain a ``scaledBalanceOf``
    (un-rebased principal) and rebase the public ``balanceOf`` via a global
    liquidity index. The relationship at any block is::

        balanceOf(b)        = scaledBalanceOf(b) × liquidityIndex(b) / RAY

    The economically correct period rebase yield is::

        yield = scaledBalanceOf(SoM) × (index_eom − index_som) / RAY
              = balanceOf(EoM) × scaledBalanceOf(SoM) / scaledBalanceOf(EoM) − balanceOf(SoM)

    so we don't need to read the index directly — just the scaled balances at
    the two boundary blocks.

    period_inflow = Δvalue − yield, returned as a single-row DataFrame at the
    period end so the compute layer's ``cum_at_or_before`` machinery works
    uniformly.
    """
    import pandas as pd

    holder = prime.alm[venue.chain]
    chain_value = venue.chain.value
    token_addr = venue.token.address.value

    bal_som = balance_at(chain_value, token_addr, holder.value, som_block)
    bal_eom = balance_at(chain_value, token_addr, holder.value, eom_block)
    scaled_som = scaled_balance_at(chain_value, token_addr, holder.value, som_block)
    scaled_eom = scaled_balance_at(chain_value, token_addr, holder.value, eom_block)

    # Derive yield in raw token units (later divided by token.decimals).
    # If scaled_eom == 0 (position fully exited), there's no held-balance
    # yield in the period — all of the EoM value is "external" inflow.
    if scaled_eom == 0:
        yield_raw = 0
    else:
        # Round-half-even on the Decimal remainder. ``int()`` truncates toward
        # zero, biasing a slightly-negative result up by one raw unit (e.g.
        # -0.7 → 0 instead of -1) under partial-withdrawal precision noise.
        from decimal import Decimal as _D
        yield_raw = int(
            (_D(bal_eom) * _D(scaled_som) / _D(scaled_eom) - _D(bal_som))
            .to_integral_value(rounding="ROUND_HALF_EVEN")
        )

    delta_raw = bal_eom - bal_som
    period_inflow_raw = delta_raw - yield_raw

    # Convert to USD. For Cat C/D the token rebases to the underlying par
    # stable, so 1 raw unit = 1 underlying unit; multiply by $1.
    scale = Decimal(10 ** venue.token.decimals)
    period_inflow_usd = Decimal(period_inflow_raw) / scale

    return pd.DataFrame([{
        "block_date": period_end_date,
        "daily_inflow": period_inflow_usd,
        "cum_inflow": period_inflow_usd,
    }])


def _cat_a_capital_inflow_timeseries(
    prime: Prime,
    venue: Venue,
    period,
    *,
    balance_source,
    external_sources: set,
):
    """Cat A par-stable capital-flow accounting with external-source allowlist.

    Each transfer to/from the ALM is classified by counterparty:
    - **external** (in ``external_sources``) → off-chain custodian sending
      realized yield directly to the ALM (Anchorage-style); flows through
      to revenue, NOT included here
    - everything else → value-preserving capital movement (PSM swap, venue
      contract allocation/withdrawal, mint/burn, allocator buffer); netted
      out of revenue, included here

    Returns DataFrame ``[block_date, daily_inflow, cum_inflow]`` of the
    capital portion. The compute layer subtracts this from Δvalue, leaving
    ``revenue = Δvalue − capital_net = external_net``. With an empty
    ``external_sources`` set the entire Δvalue is netted, so revenue = 0
    — the correct default for par-stables with no off-chain yield source.
    """
    import pandas as pd

    holder = prime.alm[venue.chain]
    pin_block = period.pin_blocks[venue.chain]

    detail = balance_source.inflow_by_counterparty(
        chain=venue.chain.value,
        token=venue.token.address.value,
        holder=holder.value,
        start=prime.start_date,
        pin_block=pin_block,
    )
    empty = pd.DataFrame({
        "block_date": [], "daily_inflow": [], "cum_inflow": [],
    })
    if detail.empty:
        return empty

    # Counterparties may arrive as bytes / bytearray / memoryview (Dune
    # varbinary, possibly with leading zeros stripped) or as a "0x"-prefixed
    # hex string. Normalize to a fixed 20-byte value so membership against
    # ``Address.value`` (always 20 bytes) is reliable.
    def _to_bytes(v):
        if isinstance(v, str):
            b = bytes.fromhex(v.removeprefix("0x"))
        elif isinstance(v, memoryview):
            b = bytes(v)
        elif isinstance(v, (bytes, bytearray)):
            b = bytes(v)
        else:
            # Symmetric with ``_to_addr_bytes`` in dune_balances.py — pass-
            # through would silently classify an unknown counterparty as
            # internal/capital, zeroing real revenue.
            raise TypeError(f"unexpected counterparty type: {type(v).__name__}")
        if len(b) > 20:
            # Oversized bytes can never match the 20-byte ``Address.value``
            # entries in ``external_sources``; passing through would silently
            # misclassify the row. Fail loudly.
            raise ValueError(f"counterparty longer than 20 bytes: {b.hex()}")
        return b.rjust(20, b"\x00")

    # Cannot use ``Series.isin(external_sources)`` here: pandas' isin has a
    # known quirk where bytes values containing leading null bytes (notably
    # the zero address ``b"\x00" * 20``) compare incorrectly. Use ``apply``
    # with Python ``in`` for correct bytes equality.
    norm = detail["counterparty"].map(_to_bytes)
    capital = detail[~norm.apply(lambda b: b in external_sources)]
    if capital.empty:
        return empty

    daily = (
        capital.groupby("block_date", as_index=False)["signed_amount"]
        .sum()
        .rename(columns={"signed_amount": "daily_inflow"})
        .sort_values("block_date")
        .reset_index(drop=True)
    )
    # Par-stable: each unit is $1, so signed_amount is already USD-equivalent.
    daily["cum_inflow"] = daily["daily_inflow"].cumsum()
    return daily


def _rwa_inflow_timeseries(
    prime: Prime,
    venue: Venue,
    period,
    *,
    balance_source,
    block_resolver,
    nav_at_block,
):
    """Cat E (RWA tranche) inflow tracking via cumulative-balance changes.

    RWA tranche tokens (Centrifuge JAAA/JTRSY, Securitize STAC) don't follow
    the ``mint = from(0x0)`` convention — flows often originate from issuer
    custodians, vault contracts, or LiquidityPool addresses. We instead track
    *all* token movements in/out of the ALM (``cumulative_balance_timeseries``)
    and convert each day's net signed token flow to USD via NAV-at-day-end.

    ⚠ For yield-distribution-as-mint tokens (BUIDL): this over-counts as
    inflow because the issuer's daily yield mints look indistinguishable
    from capital deposits. Apparent revenue collapses near zero.
    Distinguishing distributor vs depositor needs an issuer-address registry
    — deferred until BUIDL distributor is identified.

    Returns DataFrame ``[block_date, daily_inflow, cum_inflow]`` matching the
    Dune-backed shape so downstream Compute treats all venues uniformly.
    """
    import pandas as pd
    from datetime import datetime, time, timezone

    holder = prime.alm[venue.chain]
    pin_block = period.pin_blocks[venue.chain]

    # Optional per-venue filter: drop sub-threshold transfers (e.g. BUIDL-I
    # daily yield-distribution mints below $1M).
    min_transfer = venue.min_transfer_amount_usd or Decimal(0)

    bal_df = balance_source.cumulative_balance_timeseries(
        chain=venue.chain.value,
        token=venue.token.address.value,
        holder=holder.value,
        start=prime.start_date,
        pin_block=pin_block,
        min_transfer_amount=min_transfer,
    )
    if bal_df.empty:
        return pd.DataFrame({
            "block_date": [], "daily_inflow": [], "cum_inflow": [],
        })

    # Skip the (expensive) NAV oracle read for rows outside the settlement
    # period. The downstream consumer (``compute_venue_revenue``) uses only
    # ``period_inflow = cum_at_or_before(period.end) - cum_at_or_before(period.start - 1)``
    # — any constant placeholder NAV applied to pre-period rows cancels in
    # that diff. Saves ~9000 RPC calls (≈365 days × ~25 binary-search hops)
    # per Cat E venue on a cold cache for a year-old prime.
    _PRE_PERIOD_NAV = Decimal("1")
    rows = []
    for _, row in bal_df.iterrows():
        d = row["block_date"]
        net_tokens = row["daily_net"]
        net_d = net_tokens if isinstance(net_tokens, Decimal) else Decimal(str(net_tokens))
        if period.start <= d <= period.end:
            eod = datetime.combine(d, time.max, tzinfo=timezone.utc)
            block = block_resolver.block_at_or_before(venue.chain.value, eod)
            nav = nav_at_block(block)
        else:
            nav = _PRE_PERIOD_NAV
        rows.append({"block_date": d, "daily_inflow": net_d * nav})

    out = pd.DataFrame(rows).sort_values("block_date").reset_index(drop=True)
    out["cum_inflow"] = out["daily_inflow"].cumsum()
    return out


def _shares_to_usd_inflow_timeseries(
    prime: Prime,
    venue: Venue,
    period,
    *,
    balance_source,
    block_resolver,
    price_at_block,
):
    """Generic Cat B / Cat E inflow tracking.

    Receipt-token mint/burn from Dune ``tokens.transfers`` is already-decimal-
    adjusted (e.g. ``daily_inflow = 100`` means 100 shares, not 100 × 10^dec).
    For each day with activity we resolve the day-end block and call
    ``price_at_block(block)`` to get USD per 1.0 share, then multiply.

    Why per-day, not per-event: for monthly settlement on slow-moving NAV /
    pps, intra-day variance is bps and aggregating a day's net flow to a
    single price is plenty accurate. Per-event would require a new SQL
    primitive returning row-level data; deferred until Phase 2.B+.

    ``price_at_block`` is an injected callable so this helper stays clean of
    Cat-specific pricing logic — the caller wires Cat B → ``convertToAssets``
    × par-stable, Cat E → ``NavOracle.read``.

    Returns DataFrame ``[block_date, daily_inflow, cum_inflow]``.
    """
    import pandas as pd
    from datetime import datetime, time, timezone

    holder = prime.alm[venue.chain]
    zero_addr = b"\x00" * 20
    pin_block = period.pin_blocks[venue.chain]

    mint_df = balance_source.directed_inflow_timeseries(
        chain=venue.chain.value, token=venue.token.address.value,
        from_addr=zero_addr, to_addr=holder.value,
        start=prime.start_date, pin_block=pin_block,
    )
    burn_df = balance_source.directed_inflow_timeseries(
        chain=venue.chain.value, token=venue.token.address.value,
        from_addr=holder.value, to_addr=zero_addr,
        start=prime.start_date, pin_block=pin_block,
    )

    # Per-day signed share net = mints − burns. Coerce both sides to Decimal
    # so the running cumsum stays on the Decimal contract.
    by_date: dict = {}
    for df, sign in ((mint_df, 1), (burn_df, -1)):
        if df.empty:
            continue
        for _, row in df.iterrows():
            d = row["block_date"]
            shares = row["daily_inflow"]
            shares_d = shares if isinstance(shares, Decimal) else Decimal(str(shares))
            by_date[d] = by_date.get(d, Decimal("0")) + sign * shares_d

    if not by_date:
        return pd.DataFrame({
            "block_date": [], "daily_inflow": [], "cum_inflow": [],
        })

    rows = []
    for d in sorted(by_date):
        eod = datetime.combine(d, time.max, tzinfo=timezone.utc)
        block = block_resolver.block_at_or_before(venue.chain.value, eod)
        usd_per_share = price_at_block(block)
        rows.append({
            "block_date": d,
            "daily_inflow": by_date[d] * usd_per_share,
        })
    out = pd.DataFrame(rows)
    out["cum_inflow"] = out["daily_inflow"].cumsum()
    return out


def _curve_lp_index_weighted_inflow(
    prime: Prime,
    venue: Venue,
    som_block: int,
    eom_block: int,
    *,
    period_end_date,
    pool_source,
    lp_balance_at,
):
    """Closed-form Curve LP inflow.

    For stableswap pools (par-stable underlyings) the LP token behaves like a
    rebasing receipt: scaled balance is the LP-token balance (constant unless
    add/remove), and the "index" is the per-LP USD value
    ``unit_price = Σ(reserve_i × price_i) / total_supply``. Same identity as
    Aave aTokens::

        yield         = balance_som × (unit_price_eom − unit_price_som)
        period_inflow = Δvalue − yield = (balance_eom − balance_som) × unit_price_eom

    This is exact when there are no add/remove events during the period (the
    common case for stable LP positions sized for the long term). When events
    do occur, the approximation treats all balance-change as if at EoM
    unit_price; intra-period unit_price drift is bps for stableswap pools so
    the error is negligible for monthly settlement.

    Avoids needing to decode Curve event logs entirely — works for any pool
    template (NextGen 2-coin, Plain 3pool, Vyper variants).
    """
    import pandas as pd
    from ..domain.sky_tokens import KNOWN_PAR_STABLES_ETHEREUM
    from .prices import _curve_lp_unit_price

    if venue.chain.value != "ethereum":
        raise UnsupportedPricingError(
            f"Venue {venue.id}: Curve inflow only registered for ethereum in Phase 2.A"
        )

    holder = prime.alm[venue.chain]
    chain_value = venue.chain.value
    pool_addr = venue.token.address.value

    # Verify all coins are par-stable (same gate as the value path).
    state_eom = pool_source.read_pool(chain_value, pool_addr, eom_block)
    # 2-coin gate (mirroring `_curve_lp_inflow_timeseries`). Phase 2.A is
    # 2-pool only; the closed-form ``balance × virtual_price`` math assumes
    # all coins in the pool are par-stable, but the topic-hash registry in
    # extract/curve.py was only verified against 2-pool variants. A 3+ coin
    # pool slipped in here would silently price (it doesn't crash) but the
    # registered topics could miss some events.
    if len(state_eom.coins) != 2:
        raise UnsupportedPricingError(
            f"Venue {venue.id}: Curve inflow only registered for 2-coin "
            f"stableswap pools (got {len(state_eom.coins)})"
        )
    for coin in state_eom.coins:
        if coin.value not in KNOWN_PAR_STABLES_ETHEREUM:
            raise UnsupportedPricingError(
                f"Venue {venue.id}: Curve coin {coin.hex} not in par-stable "
                "registry — recursive pricing is Phase 2.B+."
            )

    unit_price_som = _curve_lp_unit_price(venue, som_block, pool_source=pool_source)
    unit_price_eom = _curve_lp_unit_price(venue, eom_block, pool_source=pool_source)

    bal_som_raw = lp_balance_at(chain_value, pool_addr, holder.value, som_block)
    bal_eom_raw = lp_balance_at(chain_value, pool_addr, holder.value, eom_block)
    scale = Decimal(10 ** venue.token.decimals)
    bal_som = Decimal(bal_som_raw) / scale
    bal_eom = Decimal(bal_eom_raw) / scale

    # period_inflow = (Δbalance) × unit_price_eom
    period_inflow_usd = (bal_eom - bal_som) * unit_price_eom

    return pd.DataFrame([{
        "block_date": period_end_date,
        "daily_inflow": period_inflow_usd,
        "cum_inflow": period_inflow_usd,
    }])


