"""ERC-4626 vault NAV reader — ``convertToAssets(1e18)`` scaled by asset decimals.

Calls ``convertToAssets(10**18)`` on any ERC-4626-compatible vault and divides
the result by ``10**asset_decimals``, where ``asset_decimals`` is supplied by the
caller (from the ``nav_oracle.asset_decimals`` / ``nav_oracle.fallback_asset_decimals``
field in the prime YAML config).

Common values:
  - ``asset_decimals=6``  — USDC-backed vaults (e.g. Apollo, Maple syrupUSDC)
  - ``asset_decimals=18`` — DAI / USDS-backed vaults
"""

from __future__ import annotations

from decimal import Decimal

from ...domain.primes import Address, Chain
from ..cache import cached
from ..rpc import RPCError, eth_call

# keccak256("convertToAssets(uint256)") → first 4 bytes
SEL_CONVERT_TO_ASSETS = "0x07a2d13a"


class ERC4626ReadError(RuntimeError):
    """Raised when ``convertToAssets(1e18)`` reverts or returns zero/empty.

    Common causes: the vault was not yet deployed or funded at the requested
    block. The Cat E oracle dispatcher in ``normalize.prices`` catches this and
    walks to the next fallback candidate.
    """


@cached(source_id="erc4626.convert_to_assets")
def read(chain: Chain, vault: Address, block: int, *, asset_decimals: int) -> Decimal:
    """Read the vault NAV at ``block``. Returns a USD-denominated ``Decimal``.

    Calls ``convertToAssets(10**18)`` — ERC-4626 standard — passing one full
    share (18 decimals). The raw uint256 result is divided by
    ``10**asset_decimals`` to produce a dollar value.

    Example: a USDC-backed vault (``asset_decimals=6``) returning 1_016_000
    atoms → ``Decimal('1.016000')``.

    Raises ``ERC4626ReadError`` on RPC revert, empty return, or all-zero
    return (pre-deployment / unfunded vault).
    """
    one_share = (10 ** 18).to_bytes(32, "big").hex()
    data = SEL_CONVERT_TO_ASSETS + one_share
    try:
        result = eth_call(chain, vault, data, block)
    except RPCError as e:
        raise ERC4626ReadError(
            f"convertToAssets(1e18) reverted at {vault.hex} on {chain.value} "
            f"block {block}: {e}"
        ) from e

    if result in ("0x", "0x" + "0" * 64):
        raise ERC4626ReadError(
            f"convertToAssets(1e18) returned zero/empty at {vault.hex} on "
            f"{chain.value} block {block} (likely pre-deployment or unfunded)"
        )

    value_raw = int(result, 16)
    return Decimal(value_raw) / Decimal(10 ** asset_decimals)
