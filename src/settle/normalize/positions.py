"""Canonical position balance + position value primitives.

Position value = balance × unit_price for fungible holdings.

Uniswap V3 positions are non-fungible NFTs, so they bypass the balance × price
formulation: ``get_position_value`` for ``lp_kind=uniswap_v3`` enumerates
NFT positions, computes ``(amount0, amount1)`` per position via tick math, and
sums each amount × per-token par-stable price to a USD total.
"""

from __future__ import annotations

from decimal import Decimal

from ..domain.pricing import PricingCategory
from ..domain.primes import Chain, Prime, Venue
from ..domain.sky_tokens import KNOWN_PAR_STABLES_ETHEREUM, PAR_STABLES_BY_CHAIN
from ..extract.dune import execute_query
from .prices import UnsupportedPricingError, get_unit_price
from .protocols import (
    IBalanceSource,
    IConvertToAssetsSource,
    IPositionBalanceSource,
    IV3PositionSource,
    IV4PositionSource,
)
from .registry import get_balance_source, get_position_balance_source
from .sources.uniswap_v3 import RPCUniswapV3PositionSource
from .sources.uniswap_v4 import default_v4_source


def get_position_balance(
    prime: Prime,
    venue: Venue,
    block: int,
    *,
    source: IPositionBalanceSource | None = None,
    flow_source: IBalanceSource | None = None,
) -> Decimal:
    """Token-units balance of `venue.token` held by `prime.alm[venue.chain]` at `block`.

    For rebasing tokens (Aave aTokens / SparkLend spTokens), the returned amount
    is the *rebased* balance — interest already accrued. For ERC-4626 vaults,
    this is share count; multiply by the unit price (which folds in `convertToAssets`)
    to get USD value.

    Category EOA branches off the standard on-chain ``balanceOf`` path entirely:
    the balance is reconstructed from token-transfer history via
    ``flow_source`` (defaults to the registry's IBalanceSource — same dune
    pull used elsewhere). See :func:`_eoa_balance`.
    """
    if venue.chain not in prime.alm:
        raise ValueError(
            f"Prime {prime.id!r} has no ALM on {venue.chain.value} "
            f"(needed for venue {venue.id})"
        )

    # Category EOA: balance comes from flow accounting, not on-chain balanceOf.
    if venue.pricing_category == PricingCategory.EOA:
        return _eoa_balance(prime, venue, block, source=flow_source)

    # Uniswap V3 positions aren't fungible ERC-20 — there's no scalar "balance"
    # of the pool. Use ``get_position_value`` directly, which enumerates NFTs
    # and sums redeemable amounts.
    if venue.lp_kind == "uniswap_v3":
        raise UnsupportedPricingError(
            f"Venue {venue.id} (Uni V3): no scalar balance defined for non-fungible "
            "NFT positions. Call get_position_value(prime, venue, block) instead."
        )
    if venue.lp_kind == "uniswap_v4":
        raise UnsupportedPricingError(
            f"Venue {venue.id} (Uni V4): no scalar balance defined for non-fungible "
            "NFT positions. Call get_position_value(prime, venue, block) instead."
        )
    holder = venue.holder_override or prime.alm[venue.chain]
    src = source if source is not None else get_position_balance_source()
    raw = src.balance_at(
        chain=venue.chain.value,
        token=venue.token.address.value,
        holder=holder.value,
        block=block,
    )
    return Decimal(raw) / Decimal(10 ** venue.token.decimals)


def _eoa_balance(
    prime: Prime,
    venue: Venue,
    block: int,
    *,
    source: IBalanceSource | None = None,
) -> Decimal:
    """Flow-accounted balance for a Cat EOA venue.

    The venue's holder is an off-protocol EOA that the ALM has sent principal
    to (e.g. an OOB pipeline relay address). The on-chain ``balanceOf`` at the
    holder is irrelevant — what we track is the **ALM's claim** against the
    address, reconstructed from token-transfer history::

        balance = Σ(ALM → holder outflows in venue.token)
                − Σ(paired_source → ALM inflows in anchor.token, at par)

    Both legs are at-par USDC (Cat EOA's token must be par-stable, enforced in
    ``prices.get_unit_price``; the anchor venue's token must also be par-stable
    or the conversion below would silently misprice). The result is in
    ``venue.token`` units (decimal-adjusted).

    Drains to zero (or slightly negative — that's the venue spread) once the
    full principal has been returned via the anchor venue.
    """
    # Schema validation — these MUST be set for Cat EOA.
    if venue.holder_override is None:
        raise ValueError(
            f"Venue {venue.id} (Cat EOA): holder_override is required (the EOA "
            "address receiving principal from the ALM)."
        )
    if venue.paired_with is None or venue.paired_source is None:
        raise ValueError(
            f"Venue {venue.id} (Cat EOA): paired_with and paired_source are "
            "required to compute the drain leg of the balance."
        )

    # Locate the anchor venue. The anchor's token is what the paired_source
    # delivers to the ALM (the return asset).
    anchor = next((v for v in prime.venues if v.id == venue.paired_with), None)
    if anchor is None:
        raise ValueError(
            f"Venue {venue.id} (Cat EOA): paired_with={venue.paired_with!r} "
            f"does not match any venue id in prime {prime.id!r}."
        )
    # The drain leg below queries token transfers in ``anchor.token``. That
    # only works if the anchor's token is itself the asset the paired_source
    # delivers — i.e. a Cat A par-stable raw venue. Pairing with a Cat B
    # share-token (e.g. bbqAUSD shares) would query the wrong token entirely
    # (shares, not underlying), and the drain would silently come back as
    # zero. Fail loudly so misconfiguration surfaces at first balance read.
    if anchor.pricing_category != PricingCategory.PAR_STABLE:
        raise ValueError(
            f"Venue {venue.id} (Cat EOA): anchor {anchor.id} has category "
            f"{anchor.pricing_category.value!r}, not PAR_STABLE. Cat EOA "
            "venues must pair with a Cat A par-stable raw venue so the drain "
            "leg queries the actual return asset (not vault shares)."
        )

    src = source if source is not None else get_balance_source()
    alm_addr = prime.alm[venue.chain]

    # Leg 1: principal sent from ALM to the EOA holder, in venue.token.
    out_df = src.directed_inflow_timeseries(
        chain=venue.chain.value,
        token=venue.token.address.value,
        from_addr=alm_addr.value,
        to_addr=venue.holder_override.value,
        start=prime.start_date,
        pin_block=block,
    )
    out_total = (
        Decimal(str(out_df["cum_inflow"].iloc[-1])) if not out_df.empty
        else Decimal("0")
    )

    # Leg 2: returns from paired_source delivered to the ALM, in anchor.token.
    # The anchor venue must live on the same chain — cross-chain drain isn't
    # supported (would need block-resolver and price translation).
    if anchor.chain != venue.chain:
        raise ValueError(
            f"Venue {venue.id} (Cat EOA): anchor {anchor.id} is on chain "
            f"{anchor.chain.value!r} but this venue is on {venue.chain.value!r}. "
            "Cross-chain drain pairing is not supported."
        )
    drain_df = src.directed_inflow_timeseries(
        chain=anchor.chain.value,
        token=anchor.token.address.value,
        from_addr=venue.paired_source.value,
        to_addr=alm_addr.value,
        start=prime.start_date,
        pin_block=block,
    )
    drain_total = (
        Decimal(str(drain_df["cum_inflow"].iloc[-1])) if not drain_df.empty
        else Decimal("0")
    )

    # Both at par — subtraction is well-defined regardless of which par-stable
    # each token is. The result may be slightly negative once the full
    # principal has been returned (= venue spread / yield).
    return out_total - drain_total


def get_position_value(
    prime: Prime,
    venue: Venue,
    block: int,
    *,
    balance_source: IPositionBalanceSource | None = None,
    flow_source: IBalanceSource | None = None,
    erc4626_source: IConvertToAssetsSource | None = None,
    v3_position_source: IV3PositionSource | None = None,
    v4_position_source: IV4PositionSource | None = None,
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

    if venue.lp_kind == "uniswap_v4":
        if v4_position_source is None:
            v4_position_source = default_v4_source(venue)
        return _uniswap_v4_value(prime, venue, block, source=v4_position_source)

    balance = get_position_balance(
        prime, venue, block,
        source=balance_source,
        flow_source=flow_source,
    )
    if balance == 0:
        # Short-circuit: zero balance × any unit price = $0. Skipping the
        # unit_price call avoids an unnecessary failure on venues with
        # exotic pricing paths (e.g. recursive 4626 underlyings) that
        # aren't fully wired up but happen to hold $0 in this period.
        return Decimal("0")
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
    registry = PAR_STABLES_BY_CHAIN.get(venue.chain)
    if registry is None:
        raise UnsupportedPricingError(
            f"Venue {venue.id}: no par-stable registry for chain {venue.chain.value!r} "
            "— add the chain's par-stable token addresses to PAR_STABLES_BY_CHAIN "
            "in sky_tokens.py."
        )
    holder = venue.holder_override or prime.alm[venue.chain]
    # Wrap the V3 pool read so a chain whose RPC blocks historical eth_call
    # (drpc free tier on Monad in particular) degrades to value=$0 with a
    # WARNING rather than aborting the run. Compute layer treats $0 the
    # same as "venue empty / not yet funded" — which is the correct
    # economic value for newly-added venues that aren't live yet.
    from ..extract.rpc import RPCError as _RPCError
    import requests as _requests
    try:
        positions = source.positions_in_pool(
            chain=venue.chain.value,
            owner=holder.value,
            pool=venue.token.address.value,
            block=block,
        )
    except (_RPCError, _requests.HTTPError, _requests.ConnectionError,
            _requests.Timeout) as _e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "_uniswap_v3_value: V3 pool read failed for %s on %s at block %d "
            "(%s) — degrading to value=$0. Inflow timeseries will also degrade.",
            venue.id, venue.chain.value, block, _e,
        )
        return Decimal("0")
    if not positions:
        return Decimal("0")
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


def _venue_v4_pool_key(venue: Venue):
    """Build an ``extract.uniswap_v4.V4PoolKey`` from the venue's domain pool key."""
    from ..extract.uniswap_v4 import V4PoolKey
    pk = venue.univ4_pool_key
    if pk is None:
        raise UnsupportedPricingError(
            f"Venue {venue.id} (Uni V4): univ4_pool_key is required."
        )
    return V4PoolKey(
        currency0=pk.currency0,
        currency1=pk.currency1,
        fee=pk.fee,
        tick_spacing=pk.tick_spacing,
        hooks=pk.hooks,
    )


def _uniswap_v4_value(
    prime: Prime,
    venue: Venue,
    block: int,
    *,
    source: IV4PositionSource,
) -> Decimal:
    """Sum redeemable USD value across the venue's V4 positions in the target pool.

    Each position contributes ``amount0 × p(token0) + amount1 × p(token1)`` at
    par ($1) for the par-stable underlyings in scope. Uncollected LP fees are
    not added (negligible for tight stable ranges — see the source docstring).

    The two failure modes are handled DIFFERENTLY on purpose:

    * **Not live yet** — the pool is uninitialized at ``block`` or the token
      ids aren't minted / owned → ``positions_in_pool`` returns an empty list
      → ``$0``. That is the correct economic value for a not-yet-funded venue.
    * **Read failure** — the pool read raises (after ``rpc.py``'s retry /
      backoff) → we RE-RAISE and block the run. Silently booking ``$0`` here
      would corrupt the SoM/EoM MtM ``(value_eom − value_som) − inflow`` for a
      possibly-funded position (e.g. make it look fully drained). Genuine
      uncertainty must fail loud, not degrade. (This is why it no longer
      mirrors ``_uniswap_v3_value``, which still degrades for the Monad
      free-tier display-only venue.)
    """
    registry = PAR_STABLES_BY_CHAIN.get(venue.chain)
    if registry is None:
        raise UnsupportedPricingError(
            f"Venue {venue.id}: no par-stable registry for chain {venue.chain.value!r} "
            "— add the chain's par-stable token addresses to PAR_STABLES_BY_CHAIN "
            "in sky_tokens.py."
        )
    holder = venue.holder_override or prime.alm[venue.chain]
    pool_key = _venue_v4_pool_key(venue)

    from ..extract.rpc import RPCError as _RPCError
    import requests as _requests
    try:
        positions = source.positions_in_pool(
            chain=venue.chain.value,
            owner=holder.value,
            token_ids=list(venue.univ4_token_ids),
            pool_key=pool_key,
            block=block,
        )
    except (_RPCError, _requests.HTTPError, _requests.ConnectionError,
            _requests.Timeout):
        import logging as _logging
        _logging.getLogger(__name__).error(
            "_uniswap_v4_value: V4 pool read for %s on %s at block %d failed "
            "after retries — refusing to book $0 for a possibly-funded "
            "position; failing the run. (An uninitialized pool / unminted "
            "token ids return an empty list and are booked as a legitimate "
            "$0 — those do NOT reach here.)",
            venue.id, venue.chain.value, block,
        )
        raise
    if not positions:
        return Decimal("0")
    total = Decimal("0")
    for p in positions:
        for token, amount_raw in ((p.currency0, p.amount0), (p.currency1, p.amount1)):
            if amount_raw == 0:
                continue
            info = registry.get(token.value)
            if info is None:
                raise UnsupportedPricingError(
                    f"V4 position {p.token_id}: token {token.hex} is not in the "
                    "par-stable registry — recursive pricing not supported."
                )
            _symbol, decimals = info
            total += Decimal(amount_raw) / Decimal(10**decimals)   # par-stable @ $1
    return total


def _uniswap_v4_inflow_timeseries(
    prime: Prime,
    venue: Venue,
    from_block: int,
    to_block: int,
    *,
    source: IV4PositionSource,
    block_to_date,
):
    """Per-day USD capital inflow into the venue's V4 positions, from
    ``ModifyLiquidity`` events (liquidityDelta priced at the event block).

    Returns a DataFrame ``[block_date, daily_inflow, cum_inflow]`` matching the
    Dune-backed shape so Compute treats all venues uniformly.

    A failed event read RE-RAISES (after ``rpc.py``'s retry/backoff) rather
    than degrading to an empty frame: the inflow feeds
    ``revenue = Δvalue − Σ inflow``, so a silently dropped mid-period mint
    would be booked as revenue — the same corruption ``_uniswap_v4_value``
    refuses for the value side. A range with genuinely no events returns the
    empty frame ($0 inflow), which is the correct economic answer. (The V3
    inflow path still degrades for the Monad free-tier display-only venue.)
    """
    import pandas as pd

    pool_key = _venue_v4_pool_key(venue)
    registry = PAR_STABLES_BY_CHAIN.get(venue.chain, KNOWN_PAR_STABLES_ETHEREUM)
    empty = pd.DataFrame({"block_date": [], "daily_inflow": [], "cum_inflow": []})

    from ..extract.rpc import RPCError as _RPCError
    import requests as _requests
    try:
        flows = source.liquidity_flows_in_pool(
            chain=venue.chain.value,
            token_ids=list(venue.univ4_token_ids),
            pool_key=pool_key,
            from_block=from_block,
            to_block=to_block,
        )
    except (_RPCError, _requests.HTTPError, _requests.ConnectionError,
            _requests.Timeout):
        import logging as _logging
        _logging.getLogger(__name__).error(
            "_uniswap_v4_inflow_timeseries: flows read for %s on %s "
            "[from %d to %d] failed after retries — refusing to book $0 "
            "inflow (a dropped mid-period mint would be counted as revenue); "
            "failing the run.",
            venue.id, venue.chain.value, from_block, to_block,
        )
        raise
    if not flows:
        return empty

    info0 = registry.get(pool_key.currency0.value)
    info1 = registry.get(pool_key.currency1.value)
    if info0 is None or info1 is None:
        raise UnsupportedPricingError(
            f"Venue {venue.id}: V4 pool tokens not in par-stable registry for "
            f"chain {venue.chain.value!r} — add them to PAR_STABLES_BY_CHAIN."
        )
    _, dec0 = info0
    _, dec1 = info1

    rows = [
        {
            "block_date": block_to_date(f.block_number),
            "daily_inflow": (
                Decimal(f.amount0) / Decimal(10**dec0)
                + Decimal(f.amount1) / Decimal(10**dec1)
            ),
        }
        for f in flows
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

    holder = venue.holder_override or prime.alm[venue.chain]
    empty = pd.DataFrame({
        "block_date": [], "daily_inflow": [], "cum_inflow": [],
    })
    # Same degrade-to-$0 contract as `_uniswap_v3_value`: if the chain's
    # RPC / Dune source can't serve historical V3 reads (Monad on drpc
    # free tier today), surface a WARN and return an empty inflow frame
    # so the downstream venue degrades to revenue=$0 cleanly.
    from ..extract.rpc import RPCError as _RPCError
    from ..extract.dune import DuneError as _DuneError
    import requests as _requests
    try:
        events = source.liquidity_events_in_pool(
            chain=venue.chain.value,
            owner=holder.value,
            pool=venue.token.address.value,
            from_block=from_block,
            to_block=to_block,
        )
    except (_RPCError, _DuneError, _requests.HTTPError,
            _requests.ConnectionError, _requests.Timeout) as _e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "_uniswap_v3_inflow_timeseries: liquidity-events read failed for "
            "%s on %s [from %d to %d] (%s) — degrading to empty inflow.",
            venue.id, venue.chain.value, from_block, to_block, _e,
        )
        return empty
    if not events:
        return empty

    # Token0/token1 addresses live on the pool (and on every position struct).
    # Try to_block first; if the holder fully exited mid-period, fall back to
    # from_block (where positions had to exist for the events to fire). The
    # math is well-defined either way — events carry signed amounts; we only
    # need a position snapshot to look up token0/token1 decimals.
    try:
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
    except (_RPCError, _requests.HTTPError, _requests.ConnectionError,
            _requests.Timeout) as _e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "_uniswap_v3_inflow_timeseries: pool-token snapshot read failed "
            "for %s on %s (%s) — degrading to empty inflow.",
            venue.id, venue.chain.value, _e,
        )
        return empty
    if not snapshot:
        raise UnsupportedPricingError(
            f"Venue {venue.id}: V3 inflow events present but no positions "
            "found at from_block or to_block — pool-token lookup unsupported."
        )
    ref = snapshot[0]
    _registry = PAR_STABLES_BY_CHAIN.get(venue.chain, KNOWN_PAR_STABLES_ETHEREUM)
    info0 = _registry.get(ref.token0.value)
    info1 = _registry.get(ref.token1.value)
    if info0 is None or info1 is None:
        raise UnsupportedPricingError(
            f"Venue {venue.id}: V3 pool tokens not in par-stable registry for "
            f"chain {venue.chain.value!r} — add them to PAR_STABLES_BY_CHAIN "
            "in sky_tokens.py."
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


# Merkl distributors per chain. These contracts emit a per-claim
# ``Claimed(address indexed user, address indexed token, uint256 amount)``
# event — we read those events directly (via ``merkl_claims_<chain>.sql``)
# instead of chasing the post-claim internal Transfer routing, which Merkl
# fans out across multiple intermediaries (Aave pool proxy, static-aToken
# wrapper, etc.) that can't safely be added to ``external_alm_sources``
# without false-positiving ordinary Grove-initiated Aave deposits.
#
# Add more chains here if Merkl is enabled on them for any prime. Senders
# that DON'T match an entry here fall through to the generic Transfer-event
# path (``_atoken_transfer_revenue_usd``) — suitable for direct-sweep
# channels like Anchorage interest or BUIDL yield mints, where the sender
# IS the ALM-ingress address.
_MERKL_DISTRIBUTORS: dict[Chain, set[bytes]] = {
    Chain.ETHEREUM: {
        bytes.fromhex("3ef3d8ba38ebe18db133cec108f4d14ce00dd9ae"),
    },
}


def _atoken_external_revenue_usd(prime: Prime, venue: Venue, period) -> Decimal:
    """Cat C / D external-rewards revenue, in USD.

    Sum of off-pool aToken rewards arriving at ``prime.alm[venue.chain]``
    for ``venue.token`` during ``period``. Used to credit yield
    distributions that the closed-form ``_atoken_index_weighted_inflow``
    formula buckets as principal injection rather than revenue.

    **Sender dispatch.** Each entry in ``prime.external_alm_sources[venue.chain]``
    is routed by data source:

      * **Merkl distributors** (per ``_MERKL_DISTRIBUTORS``) → JOIN the
        distributor's ``Claimed(user, token, amount)`` event with the
        aToken's ``Mint(caller, onBehalfOf, value, …)`` event in the same
        tx, where ``caller = Claimed.token`` (the staticAToken wrapper)
        and ``onBehalfOf = user = ALM``. The aToken venue is identified by
        ``Mint.contract_address``, so no Merkl-internal addresses ever
        touch the YAML — only ``venue.token.address``. Robust against
        Merkl's wrapper routing where ``Claimed.token`` is the staticAToken
        (not the aToken) and the aToken Transfer's ``from`` is the Aave
        pool proxy (used for every Aave operation, so unsafe to allowlist).
      * **Generic senders** (everything else) → sum aToken
        ``Transfer(from=sender, to=ALM)`` events. Suitable for direct
        sweeps (Anchorage interest, BUIDL yield mints) where the sender
        IS the ALM-ingress address.

    Both paths share the same value semantics:

    * The ``Claimed.amount`` / ``Transfer.amount`` field is the value in the
      underlying's units (Aave's aToken rebases on transfer/mint).
    * For **par-stable underlyings** (RLUSD, USDC, …), underlying-units ×
      $1 = USD, so the sum is the USD revenue directly.
    * Non-par-stable underlyings raise ``UnsupportedPricingError`` — no
      silent mispricing.

    Returns ``Decimal("0")`` when:
      * No ``external_alm_sources`` are configured for ``venue.chain``
        (default for primes with no off-pool yield channel).
      * ``DUNE_API_KEY`` is unset — logs once at WARNING and returns 0
        rather than crashing the run.

    Performance: one Dune query per ``(chain, token, sender)`` tuple per
    period. Grove's current config has 1 sender (Merkl) × 3 Cat C venues
    on Ethereum = 3 single-row queries per cell, all cached via
    ``@cached(source_id="dune.execute")``.
    """
    from decimal import Decimal as _Decimal
    import logging as _logging
    import os as _os

    # Category guard — the rebased-amount = USD shortcut only holds for
    # Aave aTokens / SparkLend spTokens (Cat C/D). A non-aToken venue
    # passing through here would silently misprice its inflows: bail loudly.
    from ..domain.primes import PricingCategory as _PC
    if venue.pricing_category not in (_PC.AAVE_ATOKEN, _PC.SPARKLEND_SPTOKEN):
        raise UnsupportedPricingError(
            f"_atoken_external_revenue_usd: venue {venue.id} category "
            f"{venue.pricing_category.value!r} is not Cat C/D — helper is "
            "specific to Aave aToken / SparkLend spToken transfer semantics. "
            "Route through the Cat A `_cat_a_capital_inflow_timeseries` path "
            "or extend this module for the new category."
        )
    senders = prime.external_alm_sources.get(venue.chain, [])
    if not senders:
        return _Decimal("0")
    if not _os.environ.get("DUNE_API_KEY"):
        _logging.getLogger(__name__).warning(
            "_atoken_external_revenue_usd: DUNE_API_KEY unset — skipping external "
            "rewards for venue %s (would have queried %d sender(s)).",
            venue.id, len(senders),
        )
        return _Decimal("0")
    # Par-stability check on the UNDERLYING — that's what determines whether
    # the rebased aToken amount equals USD. The aToken contract itself is
    # never a par-stable in our config.
    if venue.underlying is None:
        raise UnsupportedPricingError(
            f"_atoken_external_revenue_usd: venue {venue.id} has no underlying — "
            "can't classify rebased aToken transfer amount without knowing the "
            "underlying token. Set venue.underlying in the prime YAML."
        )
    from .prices import is_par_stable
    if not is_par_stable(venue.underlying):
        raise UnsupportedPricingError(
            f"_atoken_external_revenue_usd: venue {venue.id} underlying "
            f"{venue.underlying.symbol!r} is not par-stable — refusing to "
            "treat rebased aToken transfer amount as USD. Add the underlying "
            "to PAR_STABLE_SYMBOLS or extend this helper with a price oracle."
        )

    merkl_set = _MERKL_DISTRIBUTORS.get(venue.chain, set())
    total = _Decimal("0")
    for sender in senders:
        if sender.value in merkl_set:
            total += _merkl_claims_revenue_usd(prime, venue, period, sender)
        else:
            total += _atoken_transfer_revenue_usd(prime, venue, period, sender)
    return total


def _merkl_claims_revenue_usd(
    prime: Prime, venue: Venue, period, distributor,
) -> Decimal:
    """Read the Merkl distributor's ``Claimed(user, token, amount)`` events
    for ``user = ALM`` in the period, attributed to ``venue.token`` via a
    JOIN to the Aave V3 aToken ``Mint`` event in the same tx.

    Why the JOIN: Merkl's ``Claimed.token`` is the *Merkl reward token*
    (Aave's staticAToken / LM wrapper), NOT the underlying aToken the ALM
    actually receives. Filtering on the aToken address against Claimed
    returns zero rows. The aToken's own ``Mint(caller, onBehalfOf, …)``
    event fires in the same tx with ``caller = staticAToken = Claimed.token``
    and ``onBehalfOf = ALM``, so the JOIN
    ``(c.tx_hash, c.topic2) == (m.tx_hash, m.topic1) AND
    m.contract_address = venue.token`` deterministically routes each Claimed
    amount to its venue — even when a single tx claims rewards for multiple
    aTokens, each Claimed pairs with exactly one Mint. See
    ``queries/merkl_claims_ethereum.sql`` for the full SQL.

    Returns the sum × 10**-decimals in the underlying's units (= USD for
    par-stable underlyings, which the caller has already validated).
    Per-chain SQL because Dune doesn't allow chain interpolation in
    ``FROM``; see ``_MERKL_DISTRIBUTORS`` for the address list and
    ``queries/merkl_claims_<chain>.sql`` for the queries.
    """
    from pathlib import Path as _Path
    from decimal import Decimal as _Decimal

    _SQL_BY_CHAIN = {Chain.ETHEREUM: "merkl_claims_ethereum.sql"}
    sql_name = _SQL_BY_CHAIN.get(venue.chain)
    if sql_name is None:
        # Merkl deployed on a chain we don't have SQL for yet. Treat as 0
        # rather than crash — operator should add the per-chain SQL file
        # and update ``_MERKL_DISTRIBUTORS``.
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "_merkl_claims_revenue_usd: no Merkl SQL for chain %s — skipping. "
            "Add queries/merkl_claims_%s.sql to enable.",
            venue.chain.value, venue.chain.value,
        )
        return _Decimal("0")

    # ``user_padded_hex`` is a 32-byte left-padded address for indexed-topic
    # comparison (topic1 / topic2 in event logs). Pass as hex without the
    # ``0x`` prefix; the SQL applies ``from_hex(...)`` to land back at
    # varbinary. ``atoken`` is the raw venue.token address — the SQL JOINs
    # against ``ethereum.logs.contract_address`` on the Mint side to attribute
    # the Claimed amount to this venue without needing the (Merkl-internal)
    # staticAToken address in config.
    user_padded_hex = "00" * 12 + (venue.holder_override or prime.alm[venue.chain]).value.hex()

    queries_dir = _Path(__file__).resolve().parent.parent / "queries"
    # Wrap the Dune call so a 402 / network blip degrades the venue to $0
    # external revenue instead of crashing the whole cell — same pattern as
    # the DunePsm3Source preload guard. Under-counts (a real Merkl claim is
    # silently dropped) but the alternative is total failure of every
    # downstream venue. Logged loud so the operator notices.
    from ..extract.dune import DuneError as _DuneError
    import requests as _requests
    import logging as _logging
    try:
        df = execute_query(
            queries_dir / sql_name,
            params={
                "distributor":     distributor.value,
                "user_padded_hex": user_padded_hex,
                "atoken":          venue.token.address.value,
                "start_date":      period.start.isoformat(),
                "end_date":        period.end.isoformat(),
            },
            pin_block=period.pin_blocks[venue.chain],
        )
    except (_DuneError, _requests.HTTPError, _requests.ConnectionError,
            _requests.Timeout) as _e:
        _logging.getLogger(__name__).warning(
            "_merkl_claims_revenue_usd: Dune query failed for venue %s on %s "
            "(%s) — returning $0 external revenue. Common causes: Dune credits "
            "exhausted (402), throttling (429), or transient network / DNS.",
            venue.id, venue.chain.value, _e,
        )
        return _Decimal("0")
    if df is None or df.empty:
        return _Decimal("0")
    raw = df["total_amount_raw"].iloc[0]
    if isinstance(raw, _Decimal):
        raw_int = int(raw)
    elif isinstance(raw, (int, float)):
        raw_int = int(raw)
    else:
        raw_int = int(_Decimal(str(raw)))
    # Convert from token raw units to underlying-equivalent (= USD for
    # par-stable underlyings; caller has already validated this).
    return _Decimal(raw_int) / _Decimal(10 ** venue.token.decimals)


def _atoken_transfer_revenue_usd(
    prime: Prime, venue: Venue, period, sender,
) -> Decimal:
    """Generic per-sender Transfer-event sum (the old path).

    Suitable for **direct-sweep** off-pool yield channels: the sender's
    address itself transfers the aToken to the ALM, and we can attribute
    the full transfer amount as revenue. Examples: Anchorage interest
    sweeps, BUIDL yield mints (when paid as aTokens), any custodian
    that drops aToken receipts directly.

    NOT suitable for Merkl-style flows that route through Aave pool /
    static-wrapper intermediaries — use ``_merkl_claims_revenue_usd`` for
    those (dispatched on ``_MERKL_DISTRIBUTORS`` membership upstream).
    """
    from pathlib import Path as _Path
    from decimal import Decimal as _Decimal

    queries_dir = _Path(__file__).resolve().parent.parent / "queries"
    # Same Dune-degradation guard as ``_merkl_claims_revenue_usd``: a 402 or
    # transient transport error returns $0 instead of crashing the cell.
    from ..extract.dune import DuneError as _DuneError
    import requests as _requests
    import logging as _logging
    try:
        df = execute_query(
            queries_dir / "atoken_external_inflow.sql",
            params={
                "chain":      venue.chain.value,
                "token":      venue.token.address.value,
                "holder":     (venue.holder_override or prime.alm[venue.chain]).value,
                "sender":     sender.value,
                "start_date": period.start.isoformat(),
                "end_date":   period.end.isoformat(),
            },
            pin_block=period.pin_blocks[venue.chain],
        )
    except (_DuneError, _requests.HTTPError, _requests.ConnectionError,
            _requests.Timeout) as _e:
        _logging.getLogger(__name__).warning(
            "_atoken_transfer_revenue_usd: Dune query failed for venue %s on %s "
            "(sender=%s, %s) — returning $0 external revenue. Common causes: "
            "Dune credits exhausted (402), throttling (429), or transient "
            "network / DNS.",
            venue.id, venue.chain.value, sender.value.hex(), _e,
        )
        return _Decimal("0")
    if df is None or df.empty:
        return _Decimal("0")
    raw = df["total_amount"].iloc[0]
    if isinstance(raw, _Decimal):
        return raw
    if isinstance(raw, (int, float)):
        return _Decimal(raw)
    return _Decimal(str(raw))


def _atoken_per_segment_yield(
    chain_value: str,
    token_addr: bytes,
    holder_addr: bytes,
    som_block: int,
    eom_block: int,
    segment_blocks: list[int],
    *,
    balance_at,
    scaled_balance_at,
) -> int:
    """Per-segment closed-form yield for Aave aTokens.

    Aave aTokens rebase ``balanceOf`` via a global liquidity index while
    ``scaledBalanceOf`` (un-rebased principal) only changes on
    mint/burn/transfer events. The closed-form formula::

        yield = bal_end × scaled_start / scaled_end - bal_start

    is exact across a span where scaled is constant (no events) — it
    reduces to the pure rebase ``bal_end - bal_start``. When scaled
    changes mid-span (an event happened), the same formula attributes
    yield as if the scaled-start balance accrued for the whole span;
    the error is bounded by ``Δscaled × (index_end - index_event) / RAY``
    — at most ``V × intraday_index_growth`` per event.

    Applying the closed-form per-SEGMENT (one per event day) instead of
    per-PERIOD (one for the whole month) accumulates the per-event
    error rather than the per-period error. For E1 April 2026's
    $115M of late-month mints, the per-period closed-form lost
    ≈$170K of yield (Δscaled / (scaled + Δscaled) × pool_yield ≈ 30%);
    per-segment recovers all but a few dollars (events were at end-of-day
    blocks so intraday growth is near zero).

    ``segment_blocks`` is a sorted list of boundary blocks; the helper
    treats each consecutive pair as one segment, prepending ``som_block``
    and appending ``eom_block``. Boundaries outside [som_block, eom_block]
    are silently dropped. Duplicate consecutive blocks collapse to a
    zero-length segment that contributes 0 yield (harmless).

    Clean-exit handling: when scaled drops to 0 (dust) within a segment,
    the closed-form denominator explodes. The helper binary-searches the
    burn block within that segment and uses ``balanceOf(burn_block - 1)``
    as the segment-end balance — the same recovery as the pre-existing
    clean-exit code path, just applied per-segment.

    Returns the total yield in raw token units. Clamps each per-segment
    yield to ≥ 0 (rebase only ever increases balance at rest; a
    negative segment yield indicates an event slipped through our
    boundary set and is more honest reported as 0 than as a bogus loss).
    """
    from decimal import Decimal as _D

    # Build sorted unique block list spanning [som_block, eom_block].
    blocks = sorted({som_block, eom_block,
                     *[b for b in segment_blocks if som_block <= b <= eom_block]})
    if len(blocks) < 2:
        return 0

    bal_start = balance_at(chain_value, token_addr, holder_addr, blocks[0])
    scaled_start = scaled_balance_at(chain_value, token_addr, holder_addr, blocks[0])
    total_yield = 0

    for i in range(1, len(blocks)):
        end_block = blocks[i]
        if end_block == blocks[i - 1]:
            continue  # degenerate zero-length segment
        bal_end = balance_at(chain_value, token_addr, holder_addr, end_block)
        scaled_end = scaled_balance_at(chain_value, token_addr, holder_addr, end_block)

        if scaled_start == 0:
            # Pre-deployment / empty position at segment start. Anything
            # at end is principal injection, not yield.
            seg_yield = 0
        elif scaled_end == 0 or scaled_end * 1000 < scaled_start:
            # Clean exit within this segment — Aave leaves 1 wei dust on
            # full withdrawal so the literal ``scaled_end == 0`` check
            # misses it. Use the same relative-threshold definition as
            # the outer function's ``is_clean_exit``. Closed-form
            # denominator blows up here; instead binary-search for the
            # burn block and read balance just before.
            lo, hi = blocks[i - 1] + 1, end_block
            threshold = max(1, scaled_start // 10)
            while lo < hi:
                mid = (lo + hi) // 2
                sb = scaled_balance_at(chain_value, token_addr, holder_addr, mid)
                if sb <= threshold:
                    hi = mid
                else:
                    lo = mid + 1
            bal_pre_burn = balance_at(
                chain_value, token_addr, holder_addr, lo - 1,
            )
            seg_yield = bal_pre_burn - bal_start
        else:
            # Standard per-segment closed-form.
            seg_yield = int(
                _D(bal_end) * _D(scaled_start) / _D(scaled_end)
            ) - bal_start

        total_yield += max(0, seg_yield)
        bal_start = bal_end
        scaled_start = scaled_end

    return total_yield


# Backwards-compatibility alias so existing imports don't break. The
# per-event helper's old (pre_block, post_block) interface is gone — the
# per-segment helper subsumes it with a simpler single-block-per-boundary
# input. Tests using the old name are updated separately.
_atoken_per_event_yield = _atoken_per_segment_yield


def _atoken_index_weighted_inflow(
    prime: Prime,
    venue: Venue,
    som_block: int,
    eom_block: int,
    *,
    period_end_date,
    scaled_balance_at,
    balance_at,
    transfer_event_blocks=None,
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

    **Clean exit (mid-period full withdrawal) — partial-period yield via
    binary search on the withdrawal block, with multi-withdrawal fallback.**
    Aave V3 leaves 1 wei dust on full exit; the closed-form's
    ``bal_eom × scaled_som / scaled_eom`` term then collapses to ≈scaled_som
    (1 × scaled_som / 1) and the formula degenerates to ``scaled_som −
    bal_som``, which equals minus the *pre-period* yield embedded in
    ``bal_som``. For E2 aHorRwaUSDC Feb 2026 (bal_som $11.6M, index_som
    ≈1.020), that's a phantom −$232K "loss" with zero economic basis.

    When ``scaled_eom < 0.1% × scaled_som`` (clean exit), we attempt a
    binary-search recovery: find the block ``W`` where scaled_balance
    first drops to dust and read ``balance_at(W − 1)`` — the rebased
    pre-withdrawal balance with the correct ``index_W`` folded in by
    the on-chain rebase. Then ``yield_raw = bal_pre_W − bal_som``.

    Sanity check: this only works for a **single** withdrawal (clean drop
    from scaled_som to dust). If the position was drained in multiple
    partial withdrawals, the binary search converges to the last burn
    block, where ``bal_pre_W`` is just a residual — yielding a large
    *negative* phantom (E2 Feb 2026 saw two withdrawals: $11.37M →
    $6.48M → 0; binary search alone gives revenue = −$4.98M). We detect
    this by reading the midpoint of the search range BEFORE the loop:
    if scaled at the period midpoint is neither ≈ scaled_som nor ≈ dust,
    the position is being drained in stages and we fall back to
    ``yield = 0`` (the conservative dust-guard behaviour) rather than
    reporting a misleading partial result. Properly attributing yield
    across multiple withdrawal segments requires per-event index reads,
    which is deferred — the lost yield is bounded by ~$20K/mo for
    typical Horizon-sized positions.

    Aave's standard ``Transfer`` event emits the *scaled* amount (per its
    ``_burn``/``_mint`` override), so the events-sum path used elsewhere
    (Cat A) doesn't reconcile cleanly against the rebased boundary
    balances we read here. The RPC binary search avoids that mismatch
    entirely and costs ~20 ``eth_call`` reads per clean-exit cell — all
    cached via ``@cached(source_id="rpc.scaled_balance_of")``.

    **Approximation when external rewards arrive mid-period.** Even without
    clean exit, the closed-form is exact only when scaled balance is
    constant from SoM to EoM. A mid-period Aave mint of ``Δscaled`` (e.g.
    a Merkl claim redeeming through the staticAToken wrapper, captured
    separately via ``_atoken_external_revenue_usd``) makes the formula
    report yield on the *end-of-period* scaled basis rather than the
    entering principal, with relative error ``Δscaled / (scaled_som +
    Δscaled) × pool_yield``. For Grove Feb 2026 (≈$40M position, $2.96M
    Merkl drop, ≈$67K pool yield on aEthRLUSD), this is ≈5% of pool
    yield but only ~$3.4K absolute — negligible relative to the $2.96M
    external_revenue added on top. Promote to per-event index reads if
    this ever becomes material on a settlement.
    """
    import pandas as pd
    from decimal import Decimal as _D

    holder = venue.holder_override or prime.alm[venue.chain]
    chain_value = venue.chain.value
    token_addr = venue.token.address.value

    bal_som = balance_at(chain_value, token_addr, holder.value, som_block)
    bal_eom = balance_at(chain_value, token_addr, holder.value, eom_block)
    scaled_som = scaled_balance_at(chain_value, token_addr, holder.value, som_block)
    scaled_eom = scaled_balance_at(chain_value, token_addr, holder.value, eom_block)

    delta_raw = bal_eom - bal_som
    scale = Decimal(10 ** venue.token.decimals)

    is_clean_exit = (scaled_eom == 0 or
                     (scaled_som > 0 and scaled_eom * 1000 < scaled_som))

    # Per-segment yield path: when the caller provides a way to enumerate
    # event boundary blocks (typically end-of-event-day blocks derived
    # from the daily mint/burn fixtures), apply the closed-form formula
    # per-segment instead of per-period. This correctly attributes yield
    # to each segment's scaled-balance basis, eliminating the
    # ``Δscaled × (index_eom - index_at_event) / RAY`` under-count of the
    # whole-period closed-form. See ``_atoken_per_segment_yield`` for the
    # math; in short, it's the same formula but applied N times instead
    # of once, with per-segment error bounded by ``V × intraday_index_
    # growth`` per event (≈ pennies for end-of-day boundaries).
    if transfer_event_blocks is not None and som_block < eom_block:
        boundaries = list(transfer_event_blocks(
            chain_value, token_addr, holder.value, som_block, eom_block,
        ))
        # The callable returns (pre_block, post_block, date) triples.
        # Per-segment needs only the POST blocks for yield calculation; the
        # dates are used to stamp each inflow row on the correct calendar day
        # so that _time_weighted_avg_value sees the position change on the
        # actual event day instead of period_end_date.
        segment_blocks = sorted({post for pre, post, *_ in boundaries})
        yield_raw = _atoken_per_segment_yield(
            chain_value, token_addr, holder.value,
            som_block, eom_block, segment_blocks,
            balance_at=balance_at,
            scaled_balance_at=scaled_balance_at,
        )
        period_inflow_raw = delta_raw - yield_raw
        period_inflow_usd = Decimal(period_inflow_raw) / scale
        if boundaries:
            import logging as _logging
            _logging.getLogger(__name__).info(
                "_atoken_index_weighted_inflow: venue %s per-segment "
                "yield %s across %d boundary block(s).",
                venue.id, yield_raw, len(segment_blocks),
            )
            # Build a per-event inflow timeseries so that tw_avg_value
            # reflects position changes on the correct calendar days.
            # For each event boundary (pre, post, date) the inflow is:
            #   inflow_event = (bal_post - bal_pre) − yield_event
            # where yield_event uses the same closed-form as the overall
            # formula — exact when scaled is constant across [pre, post].
            # Non-event segments contribute only rebase yield (inflow=0),
            # so they need no rows. The sum of per-event inflows equals
            # period_inflow_usd computed above.
            # NOTE: rows are collapsed to one per calendar date below.
            # Multiple same-day events (e.g. E3 April 2026: Merkl claim
            # +$1.41M at 15:32, full burn −$1.41M at 16:13) would
            # otherwise produce duplicate ``block_date`` rows, and the
            # consumer's ``cum_at_or_before`` date-max lookup has no
            # defined row to pick among ties — the pre-fix ``idxmax``
            # took the FIRST tied row, silently dropping every later
            # same-day event from the cumulative (−$1.41M of phantom
            # principal loss on E3).
            # Collapsed one-row-per-date accumulation: boundaries are
            # processed in post-block order, so for each date the running
            # ``cum`` after its last event IS the end-of-day cumulative.
            # Collapsing in plain Python (dict keyed by date, overwritten
            # per event) avoids any reliance on pandas groupby intra-group
            # ordering and keeps the Decimal values exact.
            by_date: dict = {}
            cum = Decimal(0)
            for pre_blk, post_blk, event_date in sorted(boundaries, key=lambda t: t[1]):
                b_pre  = balance_at(chain_value, token_addr, holder.value, pre_blk)
                sb_pre = scaled_balance_at(chain_value, token_addr, holder.value, pre_blk)
                b_post = balance_at(chain_value, token_addr, holder.value, post_blk)
                sb_post = scaled_balance_at(chain_value, token_addr, holder.value, post_blk)
                delta_evt = b_post - b_pre
                if sb_post == 0:
                    # Clean exit within this window: yield = 0 (conservative;
                    # rare since _atoken_per_segment_yield handles it per-seg).
                    y_evt = 0
                else:
                    y_evt = max(0, int(
                        (_D(b_post) * _D(sb_pre) / _D(sb_post) - _D(b_pre))
                        .to_integral_value(rounding="ROUND_HALF_EVEN")
                    ))
                inflow_evt = Decimal(delta_evt - y_evt) / scale
                cum += inflow_evt
                if event_date not in by_date:
                    by_date[event_date] = {
                        "block_date": event_date,
                        "daily_inflow": Decimal(0),
                        "cum_inflow": Decimal(0),  # placeholder; set below
                    }
                day = by_date[event_date]
                day["daily_inflow"] += inflow_evt
                day["cum_inflow"] = cum  # always the running total after this event
            return (
                pd.DataFrame(sorted(by_date.values(), key=lambda r: r["block_date"]))
                .reset_index(drop=True)
            )
    elif is_clean_exit and som_block < eom_block:
        # Clean-exit fallback (no event-block lookup wired). Same
        # recovery logic as before this PR: binary-search for the
        # withdrawal block, or fall back to yield=0 for multi-segment
        # drains. Preserved for callers that don't pass
        # ``transfer_event_blocks`` (notably the existing test fixtures
        # that don't have mint/burn aggregates wired).
        if scaled_som == 0:
            yield_raw = 0
        else:
            mid_block = (som_block + eom_block) // 2
            sb_mid = scaled_balance_at(chain_value, token_addr, holder.value, mid_block)
            dust_threshold  = scaled_som // 1000
            stable_threshold = (scaled_som * 9) // 10
            multi_withdrawal = (dust_threshold < sb_mid < stable_threshold)
            if multi_withdrawal:
                yield_raw = 0
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "_atoken_index_weighted_inflow: venue %s drained in "
                    "stages (scaled at midpoint = %s, scaled_som = %s); "
                    "no transfer_event_blocks lookup wired — falling "
                    "back to yield=0.", venue.id, sb_mid, scaled_som,
                )
            else:
                lo, hi = som_block + 1, eom_block
                threshold = scaled_som // 10
                while lo < hi:
                    mid = (lo + hi) // 2
                    sb = scaled_balance_at(chain_value, token_addr, holder.value, mid)
                    if sb <= threshold:
                        hi = mid
                    else:
                        lo = mid + 1
                bal_pre_W = balance_at(chain_value, token_addr, holder.value, lo - 1)
                candidate_yield = bal_pre_W - bal_som
                if candidate_yield < 0:
                    yield_raw = 0
                    import logging as _logging
                    _logging.getLogger(__name__).warning(
                        "_atoken_index_weighted_inflow: venue %s binary "
                        "search produced negative yield (bal_pre=%s, "
                        "bal_som=%s); falling back to yield=0.",
                        venue.id, bal_pre_W, bal_som,
                    )
                else:
                    yield_raw = candidate_yield
        period_inflow_raw = delta_raw - yield_raw
        period_inflow_usd = Decimal(period_inflow_raw) / scale
    elif is_clean_exit:
        # Degenerate case (som_block == eom_block, single-block period —
        # only possible in tests). Yield = 0; everything is capital.
        period_inflow_usd = Decimal(delta_raw) / scale
    else:
        # Closed-form path (the common case). Round-half-even on the
        # Decimal remainder. ``int()`` truncates toward zero, biasing a
        # slightly-negative result up by one raw unit under partial-
        # withdrawal precision noise.
        #
        # Note: the closed-form under-counts when a large mid-period mint
        # arrives (Δscaled × (index_eom − index_at_mint) / RAY of yield
        # earned by newly-minted aTokens isn't attributed). E1 April 2026
        # hits this with $115M of mints late month, losing ~$170K of
        # yield vs Grove. We tried promoting to the per-event helper here
        # but day-resolution boundaries collapse the inter-event-day
        # yield on consecutive event days into the principal jump,
        # producing WORSE numbers than the closed-form. Fixing this
        # cleanly needs face-value mint amounts subtracted from the
        # segment yield, or sub-day event-block resolution — deferred.
        yield_raw = int(
            (_D(bal_eom) * _D(scaled_som) / _D(scaled_eom) - _D(bal_som))
            .to_integral_value(rounding="ROUND_HALF_EVEN")
        )
        period_inflow_raw = delta_raw - yield_raw
        period_inflow_usd = Decimal(period_inflow_raw) / scale

    return pd.DataFrame([{
        "block_date": period_end_date,
        "daily_inflow": period_inflow_usd,
        "cum_inflow": period_inflow_usd,
    }])


def _erc4626_shares_weighted_inflow(
    prime: Prime,
    venue: Venue,
    som_block: int,
    eom_block: int,
    *,
    period_end_date,
    balance_at,
    price_at_block,
):
    """Closed-form Cat B (ERC-4626) inflow for chains without event-scanning
    support.

    For chains in Dune's spellbook the standard
    ``_shares_to_usd_inflow_timeseries`` reads per-day mint/burn events
    and prices each at that day's ``convertToAssets``. On chains the
    public RPC won't scan (Monad's public endpoint limits
    ``eth_getLogs`` to a 100-block window, making a year-long scan
    infeasible), we fall back to this closed-form analog of
    ``_atoken_index_weighted_inflow``::

        yield         = shares_som × (pps_eom − pps_som)
        period_inflow = Δvalue − yield
                      = (shares_eom − shares_som) × pps_eom

    where ``pps_block = convertToAssets(1e^vault.decimals, block) ×
    par_underlying_price / 10^underlying.decimals``. Exact when no
    mid-period mint/burn happens; mid-period activity is priced at
    ``pps_eom`` (treats new principal as if added at EoM), same
    approximation as the Cat C closed-form. Returns a single-row
    DataFrame at ``period_end_date`` matching the per-day shape the
    compute layer's ``cum_at_or_before`` machinery expects.

    ``price_at_block(block) -> Decimal`` is injected by the caller and
    encapsulates ``convertToAssets`` × par-stable lookup — same
    callable shape used by ``_shares_to_usd_inflow_timeseries``.
    ``balance_at`` reads the on-chain share balance (rebased = scaled
    for ERC-4626) at the boundary blocks.

    Used for the Monad ``grove-bbqAUSD`` venue today; extends naturally
    to Unichain and Plume if Cat B venues appear there.
    """
    import pandas as pd
    from decimal import Decimal as _D

    holder = venue.holder_override or prime.alm[venue.chain]
    chain_value = venue.chain.value
    token_addr = venue.token.address.value

    shares_som = balance_at(chain_value, token_addr, holder.value, som_block)
    shares_eom = balance_at(chain_value, token_addr, holder.value, eom_block)
    pps_som = price_at_block(som_block)
    pps_eom = price_at_block(eom_block)

    scale = _D(10 ** venue.token.decimals)
    value_som = _D(shares_som) * pps_som / scale
    value_eom = _D(shares_eom) * pps_eom / scale
    yield_usd = _D(shares_som) * (pps_eom - pps_som) / scale
    period_inflow_usd = (value_eom - value_som) - yield_usd

    return pd.DataFrame([{
        "block_date":   period_end_date,
        "daily_inflow": period_inflow_usd,
        "cum_inflow":   period_inflow_usd,
    }])


# Materiality floor for the Cat A capture-gap guard: in-period balance
# movement below this (in USD-equivalent, par-stable) is treated as dust/noise
# and does not trigger the empty-counterparty-log RuntimeError. Set well above
# spam-wei dust and far below any real capture gap (the incidents were tens of
# thousands of dollars: Grove E13 ±$49,596, Spark S27 ~$194K).
_CAT_A_CAPTURE_GAP_FLOOR_USD = Decimal("1")


def _cat_a_capital_inflow_timeseries(
    prime: Prime,
    venue: Venue,
    period,
    *,
    balance_source,
    external_sources: set,
    principal_return_overrides: dict | None = None,
    yield_reversal_overrides: dict | None = None,
    paired_principal_caps: dict | None = None,
):
    """Cat A par-stable capital-flow accounting with external-source allowlist.

    Each transfer to/from the ALM is classified by counterparty:
    - **external** (in ``external_sources``) → off-chain custodian sending
      realized yield directly to the ALM (Anchorage-style); flows through
      to revenue, NOT included here
    - everything else → value-preserving capital movement (PSM swap, venue
      contract allocation/withdrawal, mint/burn, allocator buffer); netted
      out of revenue, included here

    ``principal_return_overrides``: optional ``{address_bytes:
    [(date, amount), …]}`` map. When set, an inflow whose ``(counterparty,
    block_date, signed_amount)`` matches an entry is reclassified as
    capital instead of yield — used for tri-party loan principal-correction
    or loan-termination events that arrive from an `external_alm_sources`
    address but represent a capital movement rather than yield.

    ``yield_reversal_overrides``: the mirror — optional ``{address_bytes:
    [(date, amount), …]}`` map for OUTFLOWS from the ALM TO an external
    source that return over-received yield (e.g. Spark reimbursing
    Anchorage $5M on 2026-05-19 after the over-sized May 14 payment). A
    matching outflow (date, |amount| within $1) is reclassified as
    NEGATIVE yield (excluded from the capital frame, so it nets against
    that source's inflows in revenue). Default direction stays capital —
    principal disbursements to escrows must never read as negative
    yield, so each reversal is an explicit, auditable entry.

    ``paired_principal_caps``: optional ``{paired_source_bytes:
    cum_principal_out_df}`` map for the "off-protocol round-trip" pattern
    (see ``Venue.display_only``). When provided, inflows from each
    ``paired_source`` are classified per-event with a running cap: the
    portion of the cumulative receipts that does NOT exceed the cumulative
    principal-out (ALM → display-only venue's holder, in that venue's
    token) is treated as capital (principal-return); the excess is yield
    (realized revenue at this anchor venue). Each frame must have columns
    ``[block_date, cum_inflow]`` (the cum-outflow series from
    ``directed_inflow_timeseries`` of the ALM→holder leg). Auto-populated
    by the orchestrator from display-only EOA venues whose ``paired_with``
    points to this anchor; do not set by hand.

    Returns DataFrame ``[block_date, daily_inflow, cum_inflow]`` of the
    capital portion. The compute layer subtracts this from Δvalue, leaving
    ``revenue = Δvalue − capital_net = external_net``. With an empty
    ``external_sources`` set the entire Δvalue is netted, so revenue = 0
    — the correct default for par-stables with no off-chain yield source.
    """
    import pandas as pd

    holder = venue.holder_override or prime.alm[venue.chain]
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
        if external_sources:
            # External yield source registered but no per-counterparty data.
            # Before assuming $0 flows, check whether the balance actually
            # moved in-period: balances only change via transfers, so
            # in-period movement + an EMPTY counterparty log is a guaranteed
            # capture gap, never a legitimate state. Proceeding would book
            # the whole balance delta as ±yield — the Grove E13 ±$49,596
            # May/June 2026 incident (E32 Mar/Apr was the same class).
            cum_df = balance_source.cumulative_balance_timeseries(
                chain=venue.chain.value,
                token=venue.token.address.value,
                holder=holder.value,
                start=prime.start_date,
                pin_block=pin_block,
            )
            # Materiality floor: ``daily_net`` is $-equivalent for par-stables
            # (the cumulative fallback below sums it directly as USD capital).
            # Sub-dollar dust (spam wei transfers) must NOT abort the run — the
            # summary layer already treats sub-cent as noise. Only MATERIAL
            # in-period movement with an empty log signals a real capture gap.
            moved = pd.DataFrame()
            if not cum_df.empty:
                bd = pd.to_datetime(cum_df["block_date"])
                net = pd.to_numeric(cum_df["daily_net"], errors="coerce").fillna(0)
                in_period = (
                    (bd >= pd.Timestamp(period.start))
                    & (bd <= pd.Timestamp(period.end))
                )
                moved = cum_df[in_period & (net.abs() >= _CAT_A_CAPTURE_GAP_FLOOR_USD)]
            if not moved.empty:
                import os as _os
                # Override accepts "1" (all venues) or a comma-separated venue-id
                # allowlist (e.g. "S26,E13") so bypassing one known-immaterial
                # gap does NOT globally downgrade every other venue's guard.
                _allow = _os.environ.get("SETTLE_ALLOW_UNCLASSIFIED_CAT_A", "")
                _allowed = _allow == "1" or venue.id in {
                    s.strip() for s in _allow.split(",") if s.strip()
                }
                msg = (
                    f"Cat A venue {venue.id} ({venue.token.symbol}, "
                    f"external_yield_source) has material in-period balance "
                    f"movement (>= ${_CAT_A_CAPTURE_GAP_FLOOR_USD}) but an EMPTY "
                    f"counterparty log — the inflow_by_counterparty capture for "
                    f"this venue is missing, and proceeding would misclassify "
                    f"the balance delta as ±yield. Capture the venue's transfer "
                    f"log (see the fixture capture script's INFLOW_BY_CP list) "
                    f"or set SETTLE_ALLOW_UNCLASSIFIED_CAT_A={venue.id} (or =1) "
                    f"to accept the misclassification for this run."
                )
                if not _allowed:
                    raise RuntimeError(msg)
                import logging as _logging
                _logging.getLogger(__name__).warning(msg)
            # No in-period movement (dormant venue) — $0 flows is the
            # correct answer; revenue stays Δvalue-driven (= 0).
            return empty
        # No registered external yield source AND no per-counterparty data.
        # Methodology: par-stables don't generate yield by themselves; any
        # balance change at the ALM must be value-preserving capital movement
        # (PSM swap, allocator buffer, etc.) → capital_net = Δvalue → revenue
        # = 0. Fall back to the cumulative-balance timeseries: every balance
        # change becomes capital. For par-stables, 1 token unit = $1 so
        # daily_net is already $-equivalent.
        # Guard: this fallback equates token units with USD which is only
        # valid for par-stable tokens. Refuse non-par tokens loudly rather
        # than silently mispricing balance changes (config bug surface).
        from .prices import is_par_stable
        if not is_par_stable(venue.token):
            raise ValueError(
                f"Cat A fallback to cumulative_balance reached for non-par-stable "
                f"token {venue.token.symbol!r} (venue {venue.id}). The fallback "
                "treats balance as $1/unit which is only valid for par-stables; "
                "either add the token to PAR_STABLE_SYMBOLS or provide "
                "inflow_by_counterparty data."
            )
        cum_df = balance_source.cumulative_balance_timeseries(
            chain=venue.chain.value,
            token=venue.token.address.value,
            holder=holder.value,
            start=prime.start_date,
            pin_block=pin_block,
        )
        if cum_df.empty:
            return empty
        out = pd.DataFrame({
            "block_date": cum_df["block_date"],
            "daily_inflow": cum_df["daily_net"],
        })
        out["cum_inflow"] = out["daily_inflow"].cumsum()
        return out

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
    is_external_cp = norm.apply(lambda b: b in external_sources)

    # External-counterparty classification is DIRECTIONAL: only positive
    # signed_amount (inflows from the custodian to the ALM) are recognised
    # as external yield. Negative signed_amount (outflows from the ALM TO
    # the custodian — e.g. loan principal disbursements to Anchorage tri-
    # party escrow) are CAPITAL movements BY DEFAULT, not negative yield.
    # Without this directional check, a $99M ALM→Anchorage loan
    # disbursement would surface as −$99M phantom yield (Spark May 2026
    # S26 USDC). The one exception is an explicit ``yield_reversal_overrides``
    # entry (applied further below): a registered (date, |amount|) outflow
    # is a confirmed return of over-received yield and nets against the
    # source's inflows.
    from decimal import Decimal as _Decimal
    detail = detail.copy()
    detail["_cp_bytes"] = norm
    is_external = is_external_cp & (detail["signed_amount"].apply(
        lambda x: _Decimal(str(x)) > 0
    ))

    # Apply principal-return overrides: an inflow that's nominally from an
    # external source but matches a registered (date, amount) override is
    # reclassified as capital (e.g., a tri-party loan principal correction
    # or loan-termination return). Match tolerance: ±$1.

    if principal_return_overrides:
        def _is_override(row):
            cp = row["_cp_bytes"]
            if cp not in external_sources:
                return False
            entries = principal_return_overrides.get(cp, [])
            sa = abs(_Decimal(str(row["signed_amount"])))
            bd = row["block_date"]
            for entry_date, entry_amount in entries:
                if bd == entry_date and abs(sa - entry_amount) <= 1:
                    return True
            return False

        is_principal_return = detail.apply(_is_override, axis=1)
        # Capital = (not external INFLOW) OR (external AND override-matched)
        capital_mask = ~is_external | is_principal_return
    else:
        capital_mask = ~is_external

    # Apply yield-reversal overrides (the mirror of principal-return):
    # an OUTFLOW to an external source matching a registered (date,
    # |amount|) is a return of over-received yield — exclude it from the
    # capital frame so it nets against the source's inflows in revenue
    # (``revenue = Δvalue − capital_net``: removing a negative flow from
    # capital_net lowers revenue by the same amount). Match tolerance ±$1,
    # same as principal-return.
    if yield_reversal_overrides:
        def _is_yield_reversal(row):
            cp = row["_cp_bytes"]
            if cp not in external_sources:
                return False
            sa = _Decimal(str(row["signed_amount"]))
            if sa >= 0:
                return False    # reversals are outflows only
            entries = yield_reversal_overrides.get(cp, [])
            bd = row["block_date"]
            for entry_date, entry_amount in entries:
                if bd == entry_date and abs(-sa - entry_amount) <= 1:
                    return True
            return False

        is_yield_reversal = detail.apply(_is_yield_reversal, axis=1)
        capital_mask = capital_mask & ~is_yield_reversal

    # Per-row capital amount: signed_amount if classified as capital, else 0.
    # Subsequent paired-cap logic may further reduce this for paired_source
    # inflows that exceed the cumulative principal-out.
    detail["_capital_amount"] = [
        _Decimal(str(detail["signed_amount"].iloc[i])) if capital_mask.iloc[i]
        else _Decimal("0")
        for i in range(len(detail))
    ]

    # Paired-principal-cap override: inflows from each ``paired_source`` are
    # classified per-event using a running cap against the corresponding
    # cum-principal-out series. The portion within the cap is capital
    # (principal-return); the excess is yield (revenue at the anchor).
    if paired_principal_caps:
        from ..compute._helpers import cum_at_or_before as _cum_at_or_before
        # Sort by date so we process inflows chronologically per source.
        # Stable sort keeps multiple same-day events in their original order.
        detail = detail.sort_values("block_date", kind="stable").reset_index(drop=True)
        consumed: dict[bytes, _Decimal] = {src: _Decimal("0") for src in paired_principal_caps}
        for idx in range(len(detail)):
            cp = detail["_cp_bytes"].iloc[idx]
            if cp not in paired_principal_caps:
                continue
            sa = _Decimal(str(detail["signed_amount"].iloc[idx]))
            if sa <= 0:
                # Outflow back to the paired_source — leave as default capital
                # classification (no cap consumed; cap only applies to inflows).
                continue
            cap_df = paired_principal_caps[cp]
            cap_at = _cum_at_or_before(cap_df, "cum_inflow", detail["block_date"].iloc[idx])
            room = cap_at - consumed[cp]
            if room >= sa:
                capital_portion = sa
            elif room > 0:
                capital_portion = room
            else:
                capital_portion = _Decimal("0")
            consumed[cp] += capital_portion
            detail.at[idx, "_capital_amount"] = capital_portion

    nonzero = detail[detail["_capital_amount"] != _Decimal("0")]
    if nonzero.empty:
        return empty

    daily = (
        nonzero.groupby("block_date", as_index=False)["_capital_amount"]
        .sum()
        .rename(columns={"_capital_amount": "daily_inflow"})
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

    holder = venue.holder_override or prime.alm[venue.chain]
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
    som_block: int | None = None,
    balance_at=None,
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

    **EoM reconciliation against on-chain `balanceOf`.** When ``som_block``
    and ``balance_at`` are supplied, after collecting the event-tracked share
    deltas the helper reads the on-chain share balance at SoM and EoM and
    compares the actual Δshares against the events-tracked Δshares. Any
    discrepancy (typically caused by a Dune ``tokens.transfers`` indexing gap
    at the exact pin-block boundary — observed for Grove E23 on 2026-05-31:
    a 2,919,004 steakUSDC mint at the May-EoD pin block was missing from
    Dune but visible via RPC ``balanceOf``) is attributed as a synthetic
    inflow row at ``period.end`` priced at ``price_at_block(pin_block)`` —
    the same block used to read the on-chain Δshares. A discrepancy whose
    true economic event was mid-period (not at the pin-block boundary)
    will be mispriced by the (EoM − event-date) pps drift; the warning
    log captures the discrepancy magnitude so reviewers can spot-check.
    Emits a warning so the reconciliation kick-in is visible in logs.

    Returns DataFrame ``[block_date, daily_inflow, cum_inflow]``.
    """
    import pandas as pd
    from datetime import datetime, time, timezone

    holder = venue.holder_override or prime.alm[venue.chain]
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
    # Withdrawal-queue Transfers: vaults with a cooldown queue (Maple PoolV2,
    # any ERC-4626 with deferred redemptions) Transfer the user's shares to
    # a queue contract before the in-tx burn. Without netting these against
    # the gross-mint side, the inflow classifier sees a phantom loss equal
    # to the gross redeem amount. See ``Venue.share_burn_destinations`` and
    # Q-S26 in QUESTIONS.md.
    # Wrap each burn-destination query in a Dune-degradation guard: if the
    # underlying source 402s / times out, fall back to an empty frame for
    # that destination (i.e. don't net the redemption, accept the phantom
    # loss for that month rather than crash the cell).
    #
    # Net BOTH directions: ALM→queue is a burn (sign=−1) AND queue→ALM is
    # a refund (sign=+1, cancelled/partial-fulfillment redemptions). Without
    # the symmetric refund leg the closed-form formula over-debits any month
    # where Maple returns shares to the ALM (verified for Spark S15 in
    # 2026-04: 21.5M syrupUSDT shares came back from the queue, which we
    # must add to the inflow side or revenue is over-credited by ~$23M).
    from ..extract.dune import DuneError as _DuneError
    import requests as _requests
    import logging as _logging
    queue_flow_dfs: list = []  # list of (df, sign)
    for q in venue.share_burn_destinations:
        for (frm, to, sign, _label) in (
            (holder.value, q.value,      -1, "ALM→queue (burn)"),
            (q.value,      holder.value, +1, "queue→ALM (refund)"),
        ):
            try:
                qdf = balance_source.directed_inflow_timeseries(
                    chain=venue.chain.value, token=venue.token.address.value,
                    from_addr=frm, to_addr=to,
                    start=prime.start_date, pin_block=pin_block,
                )
            except (_DuneError, _requests.HTTPError, _requests.ConnectionError,
                    _requests.Timeout) as _e:
                _logging.getLogger(__name__).warning(
                    "_shares_to_usd_inflow_timeseries: %s query failed for "
                    "venue %s (queue=%s, %s) — accepting partial accounting "
                    "for this period. Cause: Dune credits exhausted (402) / "
                    "throttling / transient network.",
                    _label, venue.id, q.hex, _e,
                )
                qdf = pd.DataFrame(
                    {"block_date": [], "daily_inflow": [], "cum_inflow": []},
                )
            queue_flow_dfs.append((qdf, sign))

    # Per-day signed share net = mints − burns. Coerce both sides to Decimal
    # so the running cumsum stays on the Decimal contract.
    by_date: dict = {}
    burn_sources: list = [(mint_df, 1), (burn_df, -1)]
    burn_sources.extend(queue_flow_dfs)
    for df, sign in burn_sources:
        if df.empty:
            continue
        for _, row in df.iterrows():
            d = row["block_date"]
            shares = row["daily_inflow"]
            shares_d = shares if isinstance(shares, Decimal) else Decimal(str(shares))
            by_date[d] = by_date.get(d, Decimal("0")) + sign * shares_d

    # EoM reconciliation against on-chain ``balanceOf``. The Dune
    # ``tokens.transfers`` table can miss Transfer events at the exact
    # pin-block boundary (see Grove E23 2026-05-31 where a mint at block
    # 46741326 = May-EoD UTC didn't surface in Dune but did show up in
    # RPC ``balanceOf``). Without this step the missing mint inflates
    # ``actual_revenue`` by the deposit value (~$3M for that case).
    #
    # We compare two PERIOD-ONLY deltas (NOT cumulative-from-prime-start):
    #   * events delta  = Σ by_date[d] for d in [period.start, period.end]
    #   * on-chain delta = balanceOf(eom_block) − balanceOf(som_block)
    # Any discrepancy is attributed as a synthetic inflow row at
    # ``period.end`` priced at ``pps_eom``. This keeps the reconciliation
    # confined to the period being settled.
    #
    # **Skipped for venues with ``share_burn_destinations``** (Maple-style
    # withdrawal queues, currently S14/S15/E37). For those, the in-tx burn
    # transfers shares to a queue contract; the events-vs-balanceOf invariant
    # is broken by the pending-redemption window even when Dune indexes
    # everything correctly. The queue/refund netting in ``queue_flow_dfs``
    # is the right mechanism for those venues; layering an on-chain anchor
    # on top would over-correct (observed for Spark S14/S15 in Apr 2026
    # where the discrepancy spans 89M shares — a Maple defensive unwind,
    # not a Dune indexing gap).
    if (
        som_block is not None
        and balance_at is not None
        and not venue.share_burn_destinations
    ):
        scale = Decimal(10 ** venue.token.decimals)
        eom_block = pin_block
        som_shares = Decimal(balance_at(
            venue.chain.value, venue.token.address.value, holder.value, som_block,
        )) / scale
        eom_shares = Decimal(balance_at(
            venue.chain.value, venue.token.address.value, holder.value, eom_block,
        )) / scale
        actual_delta = eom_shares - som_shares
        tracked_delta = sum(
            (v for d, v in by_date.items() if period.start <= d <= period.end),
            Decimal("0"),
        )
        discrepancy = actual_delta - tracked_delta
        # Tolerance: 1 wei-equivalent of a share. Real Dune-missed mints
        # observed in practice are ≥ 1 share (millions in the Grove E23
        # 2026-05 case); rounding noise stays well under this.
        # Pure-synthetic = no real event on ``period.end`` AT ALL. If a
        # legitimate EoM mint/burn happened on that date too, we keep its
        # standard EoD pricing path (don't silently re-price it at the
        # pin_block — small bps difference, but a behavior change for a
        # totally normal event we shouldn't introduce as a side effect of
        # the reconciliation).
        eom_is_pure_synthetic = (
            period.end not in by_date and abs(discrepancy) > Decimal("0.000001")
        )
        if abs(discrepancy) > Decimal("0.000001"):
            _logging.getLogger(__name__).warning(
                "_shares_to_usd_inflow_timeseries: EoM reconciliation found "
                "%.6f-share gap for venue %s (period events-tracked=%.6f, "
                "on-chain period Δ=%.6f). Attributing as synthetic inflow "
                "row at period.end — most likely cause: Dune "
                "tokens.transfers missed a Transfer event at the pin-block "
                "boundary.",
                float(discrepancy), venue.id,
                float(tracked_delta), float(actual_delta),
            )
            by_date[period.end] = by_date.get(period.end, Decimal("0")) + discrepancy
    else:
        eom_is_pure_synthetic = False

    if not by_date:
        return pd.DataFrame({
            "block_date": [], "daily_inflow": [], "cum_inflow": [],
        })

    rows = []
    for d in sorted(by_date):
        # Pure-synthetic period.end row → price at the canonical ``pin_block``
        # (the same block used to read on-chain Δshares — dodges drift
        # between the resolver's EoD definition and the orchestrator pin).
        # Anything else (mid-period mints/burns, real EoM mints, dates
        # without reconciliation) → standard EoD-block lookup.
        if d == period.end and eom_is_pure_synthetic:
            block = pin_block
        else:
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
    from ..domain.sky_tokens import KNOWN_PAR_STABLES_ETHEREUM, KNOWN_YIELD_BEARING_ETHEREUM
    from .prices import _curve_lp_unit_price

    if venue.chain.value != "ethereum":
        raise UnsupportedPricingError(
            f"Venue {venue.id}: Curve inflow only registered for ethereum in Phase 2.A"
        )

    holder = venue.holder_override or prime.alm[venue.chain]
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
        if (
            coin.value not in KNOWN_PAR_STABLES_ETHEREUM
            and coin.value not in KNOWN_YIELD_BEARING_ETHEREUM
        ):
            raise UnsupportedPricingError(
                f"Venue {venue.id}: Curve coin {coin.hex} not in par-stable or "
                "yield-bearing-4626 registries."
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


def _erc4626_event_inflow_timeseries(
    prime: "Prime",
    venue: "Venue",
    period,
    *,
    block_resolver,
) -> "pd.DataFrame":
    """Cat E (Centrifuge / ERC-4626 vault) inflow via Deposit / Withdraw events.

    Replaces ``_rwa_inflow_timeseries`` for venues that have
    ``centrifuge_vault`` set.  Instead of tracking net token-balance changes
    and re-pricing them at NAV, this function reads the exact underlying-asset
    (e.g. USDC) amounts from ERC-4626 events on the vault contract.

    Accounting identity::

        revenue = EOM_usd − SOM_usd − inflow_usd
                = (SOM + net_capital + yield_accrual) − SOM − net_capital
                = yield_accrual

    so the existing revenue formula naturally isolates yield accrual once the
    inflow is sourced from events.

    Returns
    -------
    DataFrame  [block_date, daily_inflow, cum_inflow, daily_net_shares_raw,
                cum_net_shares_raw]

    The extra ``*_shares_raw`` columns (integer, not decimal-adjusted) are
    consumed by the caller for the share-balance sanity check.
    """
    import logging as _logging
    import pandas as pd
    from decimal import Decimal
    from pathlib import Path as _Path

    from ..extract.dune import execute_query, DuneError

    assert venue.centrifuge_vault is not None, (
        "_erc4626_event_inflow_timeseries called without centrifuge_vault"
    )

    # Underlying decimals (e.g. 6 for USDC).  Prefer venue.underlying when
    # present; fall back to 6 (safe for all current Centrifuge USDC vaults).
    underlying_dec = (
        venue.underlying.decimals if venue.underlying is not None else 6
    )
    underlying_divisor = Decimal(10 ** underlying_dec)

    holder = venue.holder_override or prime.alm[venue.chain]
    pin_block = period.pin_blocks[venue.chain]

    queries_dir = _Path(__file__).resolve().parent.parent / "queries"

    try:
        df = execute_query(
            queries_dir / "erc4626_centrifuge_flow.sql",
            params={
                "vault":      venue.centrifuge_vault.value,
                "holder":     holder.value,
                "start_date": str(prime.start_date),
            },
            pin_block=pin_block,
        )
    except DuneError as exc:
        _logging.getLogger(__name__).warning(
            "_erc4626_event_inflow_timeseries: Dune query failed for venue %s"
            " — falling back to empty inflow (revenue = Δvalue). Cause: %s",
            venue.id, exc,
        )
        return pd.DataFrame({
            "block_date":           pd.Series(dtype="object"),
            "daily_inflow":         pd.Series(dtype="object"),
            "daily_assets_out":     pd.Series(dtype="object"),
            "cum_inflow":           pd.Series(dtype="object"),
            "daily_net_shares_raw": pd.Series(dtype="object"),
            "cum_net_shares_raw":   pd.Series(dtype="object"),
        })

    if df.empty:
        return pd.DataFrame({
            "block_date":           pd.Series(dtype="object"),
            "daily_inflow":         pd.Series(dtype="object"),
            "daily_assets_out":     pd.Series(dtype="object"),
            "cum_inflow":           pd.Series(dtype="object"),
            "daily_net_shares_raw": pd.Series(dtype="object"),
            "cum_net_shares_raw":   pd.Series(dtype="object"),
        })

    df["block_date"] = pd.to_datetime(df["block_date"]).dt.date

    def _to_dec(x) -> Decimal:
        return Decimal(str(x)) if x is not None else Decimal(0)

    rows = []
    for _, row in df.iterrows():
        assets_in  = _to_dec(row.get("assets_in_raw",  0))
        assets_out = _to_dec(row.get("assets_out_raw", 0))
        shares_in  = _to_dec(row.get("shares_in_raw",  0))
        shares_out = _to_dec(row.get("shares_out_raw", 0))

        daily_inflow_usd  = (assets_in - assets_out) / underlying_divisor
        # Gross USDC withdrawn this day — kept separately so callers can
        # weight sd_share computations by redemption size rather than using
        # the net inflow (which would cancel deposits against withdrawals).
        daily_assets_out_usd = assets_out / underlying_divisor
        daily_net_shares      = shares_in - shares_out

        rows.append({
            "block_date":           row["block_date"],
            "daily_inflow":         daily_inflow_usd,
            "daily_assets_out":     daily_assets_out_usd,
            "daily_net_shares_raw": daily_net_shares,
        })

    out = (
        pd.DataFrame(rows)
        .sort_values("block_date")
        .reset_index(drop=True)
    )
    out["cum_inflow"]          = out["daily_inflow"].cumsum()
    out["cum_net_shares_raw"]  = out["daily_net_shares_raw"].cumsum()
    return out


