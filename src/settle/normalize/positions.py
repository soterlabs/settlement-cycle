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
from ..domain.sky_tokens import KNOWN_PAR_STABLES_ETHEREUM
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
    user_padded_hex = "00" * 12 + prime.alm[venue.chain].value.hex()

    queries_dir = _Path(__file__).resolve().parent.parent / "queries"
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
    df = execute_query(
        queries_dir / "atoken_external_inflow.sql",
        params={
            "chain":      venue.chain.value,
            "token":      venue.token.address.value,
            "holder":     prime.alm[venue.chain].value,
            "sender":     sender.value,
            "start_date": period.start.isoformat(),
            "end_date":   period.end.isoformat(),
        },
        pin_block=period.pin_blocks[venue.chain],
    )
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

    **Approximation when external rewards arrive mid-period.** The two
    closed-form expressions are mathematically identical only when no
    scaled-balance changes happen between SoM and EoM. A mid-period Aave
    mint of ``Δscaled`` (e.g. a Merkl claim redeeming through the
    staticAToken wrapper, captured separately via ``_atoken_external_
    revenue_usd``) makes the second form report yield on the
    *end-of-period* scaled basis rather than the entering principal,
    introducing an error of order ``Δscaled / (scaled_som + Δscaled) ×
    pool_yield``. For Grove Feb 2026 (≈$40M position, $2.96M Merkl drop,
    ≈$67K pool yield on aEthRLUSD), this is ≈5 % of pool yield but only
    ~$3.4K absolute — negligible relative to the $2.96M external_revenue
    that's added on top. Promote to per-event index reads (or scale the
    Merkl drop's scaled-balance contribution out of ``scaled_eom``) if
    the relative error ever becomes material on a settlement.
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
    # Aave V3 never burns the last raw unit on full exit, so a fully
    # withdrawn position presents as scaled_eom ≈ 0..tiny dust. Without a
    # guard, ``bal_eom × scaled_som / scaled_eom`` would divide by ~1 and
    # report a phantom multi-hundred-K loss on a clean withdrawal (Feb 2026
    # E2 aHorRwaUSDC saw −$232K). Treat scaled_eom < 0.1% of scaled_som as
    # a clean exit (yield=0). The lost true yield is bounded by ~one Aave
    # month, ≈$20K/mo for an $11M Horizon-sized position.
    if scaled_eom == 0 or (scaled_som > 0 and scaled_eom * 1000 < scaled_som):
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
    from ..domain.sky_tokens import KNOWN_PAR_STABLES_ETHEREUM, KNOWN_YIELD_BEARING_ETHEREUM
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


