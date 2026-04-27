"""Canonical position balance + position value primitives.

Position value = balance × unit_price. Both inputs are pinned to the same
`block_number`, so values are reproducible byte-for-byte.
"""

from __future__ import annotations

from decimal import Decimal

from ..domain.primes import Prime, Venue
from .prices import get_unit_price
from .protocols import IConvertToAssetsSource, IPositionBalanceSource
from .registry import get_position_balance_source


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
) -> Decimal:
    """USD value of the position. Composes balance × unit_price at the same block."""
    balance = get_position_balance(prime, venue, block, source=balance_source)
    price = get_unit_price(venue, block, erc4626_source=erc4626_source)
    return balance * price
