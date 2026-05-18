"""Canonical position balance + position value primitives.

Position value = balance × unit_price for fungible holdings.

Uniswap V3 positions are non-fungible NFTs, so they bypass the balance × price
formulation: ``get_position_value`` for ``lp_kind=uniswap_v3`` enumerates
NFT positions, computes ``(amount0, amount1)`` per position via tick math, and
sums each amount × per-token par-stable price to a USD total.
"""

from __future__ import annotations

from decimal import Decimal

from ..domain.primes import Chain, Prime, Venue
from ..domain.sky_tokens import KNOWN_PAR_STABLES_ETHEREUM, PAR_STABLES_BY_CHAIN
from ..extract.dune import execute_query
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
    holder = venue.holder_override or prime.alm[venue.chain]
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
    positions = source.positions_in_pool(
        chain=venue.chain.value,
        owner=holder.value,
        pool=venue.token.address.value,
        block=block,
    )
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

    if is_clean_exit and som_block < eom_block:
        # Two recovery paths for the closed-form degeneration:
        #   (1) Single mid-period withdrawal — binary-search for the burn
        #       block, then read rebased balance just before it.
        #   (2) Multiple partial withdrawals — give up and return yield=0,
        #       since the binary search would land on the LAST burn and
        #       ``bal_pre_W`` would only reflect the residual.
        #
        # We discriminate by reading scaled_balance at the period midpoint
        # *before* running the search. If it's still ≈ scaled_som the
        # withdrawal happened in the second half; if it's ≈ dust the
        # withdrawal happened in the first half — either way single
        # withdrawal. If it's anywhere in between (10-90% of scaled_som),
        # the position is being drained in stages and we bail out.
        if scaled_som == 0:
            yield_raw = 0
        else:
            mid_block = (som_block + eom_block) // 2
            sb_mid = scaled_balance_at(chain_value, token_addr, holder.value, mid_block)
            # Multi-withdrawal sentinel: midpoint is intermediate.
            dust_threshold  = scaled_som // 1000   # 0.1%
            stable_threshold = (scaled_som * 9) // 10  # 90%
            if dust_threshold < sb_mid < stable_threshold:
                # Multi-withdrawal detected. Conservative fallback.
                yield_raw = 0
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "_atoken_index_weighted_inflow: venue %s drained in "
                    "stages (scaled at midpoint = %s, scaled_som = %s); "
                    "falling back to yield=0. Multi-segment yield "
                    "attribution requires per-event index reads — "
                    "deferred.", venue.id, sb_mid, scaled_som,
                )
            else:
                # Single withdrawal — binary-search for the burn block.
                lo, hi = som_block + 1, eom_block
                threshold = scaled_som // 10
                while lo < hi:
                    mid = (lo + hi) // 2
                    sb = scaled_balance_at(chain_value, token_addr, holder.value, mid)
                    if sb <= threshold:
                        hi = mid
                    else:
                        lo = mid + 1
                withdrawal_block = lo
                # bal at the block right before withdrawal = pre-withdrawal
                # rebased value with the correct ``index_W`` folded in.
                bal_pre_W = balance_at(chain_value, token_addr, holder.value,
                                       withdrawal_block - 1)
                candidate_yield = bal_pre_W - bal_som
                # Final safety check: yield must be non-negative for an
                # accruing token. If negative, the search converged
                # unexpectedly — fall back to yield=0.
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
        # Closed-form path (the common case). Round-half-even on the Decimal
        # remainder. ``int()`` truncates toward zero, biasing a slightly-
        # negative result up by one raw unit under partial-withdrawal
        # precision noise.
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


def _cat_a_capital_inflow_timeseries(
    prime: Prime,
    venue: Venue,
    period,
    *,
    balance_source,
    external_sources: set,
    principal_return_overrides: dict | None = None,
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
            # External yield source registered but no per-counterparty data
            # available — can't classify; refuse to guess. Caller sees
            # period_inflow = 0 and revenue = Δvalue, which is wrong but
            # explicit (vs. silently zeroing real yield).
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
    is_external = norm.apply(lambda b: b in external_sources)

    # Apply principal-return overrides: an inflow that's nominally from an
    # external source but matches a registered (date, amount) override is
    # reclassified as capital (e.g., a tri-party loan principal correction
    # or loan-termination return). Match tolerance: ±$1.
    if principal_return_overrides:
        from decimal import Decimal as _Decimal
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

        # Annotate rows for the closure
        detail = detail.copy()
        detail["_cp_bytes"] = norm
        is_principal_return = detail.apply(_is_override, axis=1)
        # Capital = (not external) OR (external AND override-matched)
        capital_mask = ~is_external | is_principal_return
        capital = detail[capital_mask].drop(columns="_cp_bytes")
    else:
        capital = detail[~is_external]
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


