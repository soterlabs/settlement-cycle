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

from decimal import Decimal

from ..domain.pricing import PricingCategory
from ..domain.primes import Token, Venue
from .protocols import IConvertToAssetsSource
from .registry import get_convert_to_assets_source

# Tokens the pipeline treats as $1.00 USD pegs. Drift is documented in
# VALUATION_METHODOLOGY (e.g. USDe ±0.5%); for Phase 1 we accept the simplification.
PAR_STABLE_SYMBOLS: frozenset[str] = frozenset({
    "USDC", "USDS", "DAI", "USDT", "PYUSD", "RLUSD", "AUSD", "USDe",
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

    raise UnsupportedPricingError(
        f"Pricing category {cat.value!r} not implemented in Phase 1 "
        f"(venue {venue.id}, token {venue.token.symbol})"
    )
