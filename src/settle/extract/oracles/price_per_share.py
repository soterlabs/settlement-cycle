"""Price-per-share feed reader — ``convertToAssets(1e18)`` on a feed contract.

Centrifuge / Janus Anemoy and Apollo publish per-tranche NAV via dedicated
oracle contracts implementing ERC-4626's ``convertToAssets(uint256)``. The
underlying is USDC at $1, so the result is dollar-denominated NAV directly.

Primary feed for Centrifuge tranche tokens (JAAA, JTRSY, ACRDX) per Grove
team's Feb 2026 PnL workbook. Chronicle Scribe oracles remain as fallback.
"""

from __future__ import annotations

from decimal import Decimal

from ...domain.primes import Address, Chain
from ..cache import cached
from ..rpc import RPCError, eth_call

SEL_CONVERT_TO_ASSETS = "0x07a2d13a"


class PricePerShareReadError(RuntimeError):
    """Raised when ``convertToAssets(1e18)`` reverts on the feed contract."""


@cached(source_id="price_per_share.convert_to_assets")
def read(chain: Chain, oracle: Address, block: int) -> Decimal:
    """Read NAV at ``block``. Returns USD-denominated `Decimal` (e.g. 1.0324).

    Pre-deployment / pre-funding blocks revert; the dispatcher in
    ``normalize.prices`` walks the venue's ``nav_oracle.fallback`` chain
    (typically ``const_one``) on failure.
    """
    one_token = (10 ** 18).to_bytes(32, "big").hex()
    data = SEL_CONVERT_TO_ASSETS + one_token
    try:
        result = eth_call(chain, oracle, data, block)
    except RPCError as e:
        raise PricePerShareReadError(
            f"convertToAssets(1e18) reverted at {oracle.hex} on {chain.value} "
            f"block {block}: {e}"
        ) from e
    if result == "0x":
        raise PricePerShareReadError(
            f"convertToAssets(1e18) returned empty at {oracle.hex} on "
            f"{chain.value} block {block} (likely pre-deployment)"
        )

    value_raw = int(result, 16)
    return Decimal(value_raw) / Decimal(10 ** 18)
