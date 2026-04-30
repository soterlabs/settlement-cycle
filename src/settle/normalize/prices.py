"""Per-category unit-price dispatch.

`get_unit_price(venue, block)` returns the USD price per 1 venue.token unit.
The math branches on `venue.pricing_category`:

| Cat | Branch | Source |
|---|---|---|
| A   | `$1.00` const                                              | none |
| B   | `convertToAssets(1 share) / 10^underlying_dec × p(underlying)` | RPC + const |
| C/D | `p(underlying)` (rebased balance carries underlying value) | const |
| E   | off-chain NAV (issuer API / CoinGecko)                     | not implemented yet |
| F   | `share × Σ balance_i × p_i` — handled in positions.py      | not implemented yet |
| G   | oracle / CoinGecko                                         | not implemented yet |
| H   | DEX price (CoinGecko or Dune `prices.minute`)              | not implemented yet |

Phase 1 scope: A, B, C, D with par-stable underlyings only. Other categories
raise `NotImplementedError` and gate the venue out of position valuation.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from ..domain.pricing import PricingCategory
from ..domain.primes import Token, Venue
from ..domain.sky_tokens import KNOWN_PAR_STABLES_ETHEREUM, KNOWN_YIELD_BEARING_ETHEREUM
from .protocols import IConvertToAssetsSource, INavOracleSource
from .registry import (
    UnknownSourceError,
    get_convert_to_assets_source,
    get_nav_oracle_source,
)
from .sources.curve_pool import CurvePoolSource, CurvePoolState

_log = logging.getLogger(__name__)

# Tokens priced at exactly $1.00 — explicitly accepted simplification.
#
# Even though every entry below has a Chainlink / Chronicle / Pyth / Redstone
# oracle (per docs/pricing/allocation_pricing.csv "CORE ASSETS" section), the
# pipeline does NOT read those oracles. Reasons:
#
# 1. Every token here trades within ±0.5% of $1.00 over the periods MSC settles.
#    The largest historical sustained drift is USDe (±0.5%) and post-depeg USDC
#    (one-week event in 2023). For monthly-grain settlement the precision impact
#    is below the 1% Q6 cost-basis tolerance.
#
# 2. Reading 8 oracles per chain per snapshot adds ~16 RPC calls per settlement
#    run with no measurable accuracy gain.
#
# 3. The convention is symmetrical with how upstream Sky math treats USDS — the
#    SSR yield engine is denominated in $-equivalent USDS, not in oracle-priced
#    USDS. Using oracle prices here would create a USDS<->oracle drift artifact
#    that is not actually part of the agent's PnL.
#
# To override (e.g. if a depeg lasts long enough to matter), drop the symbol
# from this set and add a Source implementation that reads the relevant oracle.
PAR_STABLE_SYMBOLS: frozenset[str] = frozenset({
    "USDC", "USDS", "DAI", "USDT", "PYUSD", "RLUSD", "AUSD", "USDe",
    # sUSDS treated as $1 par when used as the underlying of an outer 4626
    # vault (e.g. fsUSDS). The outer vault's ``convertToAssets`` has already
    # converted shares → sUSDS amount; the additional ~SSR-driven ~5% sUSDS
    # premium is a known approximation. Documented in PRD §17.12. Spark's
    # affected venues (S17/S36/S42 fsUSDS) all have $0 balance in Q1 2026
    # so the error is $0 for the current settlement.
    "sUSDS",
})


class UnsupportedPricingError(NotImplementedError):
    """Raised when a venue's pricing category isn't implemented yet in Phase 1."""


def is_par_stable(token: Token) -> bool:
    return token.symbol in PAR_STABLE_SYMBOLS


def par_stable_price(token: Token) -> Decimal:
    if not is_par_stable(token):
        raise ValueError(
            f"Token {token.symbol!r} is not in the par-stable whitelist; "
            "extend PAR_STABLE_SYMBOLS or add a price source."
        )
    return Decimal("1.00")


def get_unit_price(
    venue: Venue,
    block: int,
    *,
    erc4626_source: IConvertToAssetsSource | None = None,
    nav_oracle_resolver=None,            # callable: (kind) -> INavOracleSource (for tests)
    curve_pool_source: CurvePoolSource | None = None,
    block_resolver=None,                 # IBlockResolver for cross-chain NAV
) -> Decimal:
    """USD price per 1 unit of `venue.token` at `block`."""
    cat = venue.pricing_category

    if cat == PricingCategory.PAR_STABLE:
        return par_stable_price(venue.token)

    if cat == PricingCategory.ERC4626_VAULT:
        if venue.underlying is None:
            raise ValueError(f"Venue {venue.id} (cat B) requires `underlying`")
        src = erc4626_source if erc4626_source is not None else get_convert_to_assets_source()
        shares_in = 10 ** venue.token.decimals
        assets_out_raw = src.convert_to_assets(
            chain=venue.chain.value,
            vault=venue.token.address.value,
            shares=shares_in,
            block=block,
        )
        assets_per_share = Decimal(assets_out_raw) / Decimal(10 ** venue.underlying.decimals)
        return assets_per_share * par_stable_price(venue.underlying)

    if cat in (PricingCategory.AAVE_ATOKEN, PricingCategory.SPARKLEND_SPTOKEN):
        # `balanceOf` already returns the rebased nominal amount, so unit price
        # is just the underlying's price. The "balance × unit_price" composition
        # in positions.py gives the right USD value.
        if venue.underlying is None:
            raise ValueError(f"Venue {venue.id} (cat {cat.value}) requires `underlying`")
        return par_stable_price(venue.underlying)

    if cat == PricingCategory.RWA_TRANCHE:
        return _resolve_rwa_nav(
            venue, block,
            resolver=nav_oracle_resolver,
            block_resolver=block_resolver,
        )

    if cat == PricingCategory.LP_POOL:
        if venue.lp_kind == "curve_stableswap":
            return _curve_lp_unit_price(
                venue, block,
                pool_source=curve_pool_source if curve_pool_source else CurvePoolSource(),
            )
        if venue.lp_kind == "uniswap_v3":
            # Uni V3 positions are non-fungible NFTs — no meaningful unit price.
            # The dedicated branch in ``positions.get_position_value`` enumerates
            # NFTs and sums amounts directly.
            raise UnsupportedPricingError(
                f"Venue {venue.id} (Uni V3): unit price not defined for non-fungible "
                "NFT positions. Call get_position_value(prime, venue, block) instead."
            )
        raise UnsupportedPricingError(
            f"Venue {venue.id} (Cat F) has unknown lp_kind={venue.lp_kind!r}"
        )

    raise UnsupportedPricingError(
        f"Pricing category {cat.value!r} not implemented in Phase 1 "
        f"(venue {venue.id}, token {venue.token.symbol})"
    )


def _curve_lp_unit_price(
    venue: Venue,
    block: int,
    *,
    pool_source: CurvePoolSource,
) -> Decimal:
    """USD price per LP unit for a Curve stableswap pool (Method B per
    VALUATION_METHODOLOGY §6.a — reserves × per-coin price).

    Reads pool state via ``CurvePoolSource``, classifies each coin against the
    par-stable address registry, sums reserves at $1, divides by total supply.

    Raises ``UnsupportedPricingError`` if the pool contains a coin that's not
    in the par-stable registry (e.g. yield-bearing 4626 like sUSDS) — recursive
    pricing of LP underlyings is Phase 2.B+ work.
    """
    state: CurvePoolState = pool_source.read_pool(
        venue.chain.value, venue.token.address.value, block,
    )

    # Map coin addresses to (symbol, decimals) via the chain-specific registry.
    if venue.chain.value != "ethereum":
        raise UnsupportedPricingError(
            f"Venue {venue.id}: Curve LP pricing only registered for ethereum in Phase 2.A "
            f"(needed: par-stable registry for chain {venue.chain.value!r})"
        )
    registry = KNOWN_PAR_STABLES_ETHEREUM

    pool_value_usd = Decimal("0")
    for coin_addr, raw_balance in zip(state.coins, state.balances, strict=True):
        info = registry.get(coin_addr.value)
        if info is not None:
            _symbol, decimals = info
            # par-stable price = $1; sum the per-coin USD value
            pool_value_usd += Decimal(raw_balance) / Decimal(10**decimals)
            continue
        # Yield-bearing 4626 (e.g. sUSDS) — price recursively via
        # ``convertToAssets(1 share) / 10**underlying_decimals × par_underlying``.
        # The underlying must be a par-stable so the recursion bottoms out at
        # a $1 fixed price; chained yield-bearing wrappers (4626-on-4626) are
        # unsupported.
        yb = KNOWN_YIELD_BEARING_ETHEREUM.get(coin_addr.value)
        if yb is not None:
            _y_sym, share_decimals, underlying_addr, underlying_decimals = yb
            par = registry.get(underlying_addr)
            if par is None:
                raise UnsupportedPricingError(
                    f"Venue {venue.id}: yield-bearing coin {coin_addr.hex} maps to "
                    f"underlying {underlying_addr.hex()} which is missing from the "
                    "par-stable registry."
                )
            from ..extract import rpc as _rpc
            from ..domain.primes import Address as _Addr, Chain as _Chain
            assets_per_share_raw = _rpc.convert_to_assets(
                _Chain(venue.chain.value), _Addr(coin_addr.value),
                shares=10 ** share_decimals, block=block,
            )
            assets_per_share = (
                Decimal(assets_per_share_raw) / Decimal(10**underlying_decimals)
            )
            # Underlying par price is $1 by definition of being in the
            # par-stable registry. Multiply explicitly so the math is
            # auditable and survives any future change to par_stable_price.
            par_underlying_usd = Decimal("1")
            price_per_share = assets_per_share * par_underlying_usd
            pool_value_usd += (
                Decimal(raw_balance) / Decimal(10**share_decimals) * price_per_share
            )
            continue
        raise UnsupportedPricingError(
            f"Venue {venue.id}: pool coin {coin_addr.hex} is not in the "
            "par-stable or yield-bearing-4626 registries. Add it to one of them."
        )

    total_supply_units = Decimal(state.total_supply) / Decimal(10**venue.token.decimals)
    if total_supply_units == 0:
        return Decimal("0")
    return pool_value_usd / total_supply_units


def _resolve_rwa_nav(
    venue: Venue,
    block: int,
    *,
    resolver=None,
    block_resolver=None,
) -> Decimal:
    """Walk a Cat E venue's NAV-oracle chain (primary → fallback).

    Each candidate is read via the registered ``INavOracleSource`` for its kind.
    On failure (revert, unknown kind, missing source), log a warning and try
    the next candidate. If all candidates exhaust, raise ``UnsupportedPricingError``
    so the caller knows the venue is unpriced (no silent $1 fall-through —
    that masks bugs).

    Cross-chain: if ``venue.nav_oracle.oracle_chain`` differs from
    ``venue.chain`` (e.g. an Avalanche venue whose NAV is published only on
    Ethereum), the venue-chain ``block`` is translated to the oracle-chain
    block at the same calendar day-end via ``block_resolver``. NAV is global
    for tokens like JAAA/JTRSY so this is exact, modulo end-of-day granularity.
    """
    from datetime import datetime, time, timezone

    if venue.nav_oracle is None:
        raise ValueError(
            f"Venue {venue.id} (Cat E) has no nav_oracle config — set one in YAML"
        )

    oracle_chain = venue.nav_oracle.oracle_chain or venue.chain
    if oracle_chain != venue.chain:
        if block_resolver is None:
            raise ValueError(
                f"Venue {venue.id}: nav_oracle.oracle_chain={oracle_chain.value!r} "
                f"differs from venue.chain={venue.chain.value!r}; cross-chain "
                "NAV resolution requires a block_resolver to translate blocks."
            )
        block_date = block_resolver.block_to_date(venue.chain.value, block)
        eod = datetime.combine(block_date, time.max, tzinfo=timezone.utc)
        oracle_block = block_resolver.block_at_or_before(oracle_chain.value, eod)
    else:
        oracle_block = block

    candidates: list[tuple[str, bytes | None]] = [
        (venue.nav_oracle.kind,
         venue.nav_oracle.address.value if venue.nav_oracle.address else None),
    ]
    if venue.nav_oracle.fallback:
        candidates.append((
            venue.nav_oracle.fallback,
            venue.nav_oracle.fallback_address.value if venue.nav_oracle.fallback_address else None,
        ))

    from ..extract.oracles.chronicle import ChronicleReadError
    from ..extract.rpc import RPCError

    _resolve = resolver if resolver is not None else get_nav_oracle_source
    last_err: Exception | None = None
    # Catch only the known oracle-failure shapes. ``Exception`` would mask
    # ``KeyboardInterrupt`` / ``MemoryError`` / programming bugs (`AttributeError`
    # etc.), making "all oracles failed" a catch-all hiding real bugs.
    _ORACLE_FAILURES: tuple[type[BaseException], ...] = (
        UnknownSourceError, NotImplementedError, ValueError,
        ChronicleReadError, RPCError,
    )
    for kind, addr in candidates:
        try:
            src: INavOracleSource = _resolve(kind)
            return src.nav_at(oracle_chain.value, addr, oracle_block)
        except _ORACLE_FAILURES as e:
            _log.warning("NAV oracle %r failed for venue %s: %s", kind, venue.id, e)
            last_err = e

    raise UnsupportedPricingError(
        f"All NAV oracles failed for venue {venue.id} ({venue.token.symbol}). "
        f"Last error: {last_err!r}. Add a fallback in YAML or fix the primary."
    )
