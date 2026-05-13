"""ERC-4626 vault NAV reader — ``convertToAssets(10**share_decimals)``.

Calls ``convertToAssets(10**share_decimals)`` on any ERC-4626-compatible vault
(passing one full share) and divides the result by ``10**underlying_decimals``
to produce a USD-denominated NAV.

Both parameters come from the prime YAML config:

  ``share_decimals``      — decimal count of the vault's *share* token
                            (the input to ``convertToAssets``).
  ``underlying_decimals`` — decimal count of the vault's *underlying* asset
                            (the divisor applied to the raw return value).

Common configurations:
  - USDC-backed, 18-decimal shares (e.g. Apollo ACRDX):
      share_decimals=18, underlying_decimals=6
  - DAI/USDS-backed, 18-decimal shares:
      share_decimals=18, underlying_decimals=18
  - Both 6 (older style vaults following OZ default of asset-matched shares):
      share_decimals=6, underlying_decimals=6
"""

from __future__ import annotations

from decimal import Decimal

from ...domain.primes import Address, Chain
from ..cache import cached
from ..rpc import RPCError, eth_call

# keccak256("convertToAssets(uint256)") → first 4 bytes
SEL_CONVERT_TO_ASSETS = "0x07a2d13a"


class ERC4626ReadError(RuntimeError):
    """Raised when ``convertToAssets`` reverts or returns zero/empty.

    Common causes: the vault was not yet deployed or funded at the requested
    block. The Cat E oracle dispatcher in ``normalize.prices`` catches this and
    walks to the next fallback candidate.
    """


@cached(source_id="erc4626.convert_to_assets")
def read(
    chain: Chain,
    vault: Address,
    block: int,
    *,
    share_decimals: int,
    underlying_decimals: int,
) -> Decimal:
    """Read the vault NAV at ``block``. Returns a USD-denominated ``Decimal``.

    Calls ``convertToAssets(10**share_decimals)`` — passing one full share —
    and divides the raw uint256 result by ``10**underlying_decimals``.

    Example (Apollo ACRDX — 18-decimal shares, USDC underlying):
      ``read(..., share_decimals=18, underlying_decimals=6)``
      raw return 1_016_000 → ``Decimal('1.016000')``

    Raises ``ERC4626ReadError`` on RPC revert, empty return, or all-zero
    return (pre-deployment / unfunded vault).
    """
    one_share = (10 ** share_decimals).to_bytes(32, "big").hex()
    data = SEL_CONVERT_TO_ASSETS + one_share
    try:
        result = eth_call(chain, vault, data, block)
    except RPCError as e:
        raise ERC4626ReadError(
            f"convertToAssets(1e{share_decimals}) reverted at {vault.hex} on "
            f"{chain.value} block {block}: {e}"
        ) from e

    if result in ("0x", "0x" + "0" * 64):
        raise ERC4626ReadError(
            f"convertToAssets(1e{share_decimals}) returned zero/empty at "
            f"{vault.hex} on {chain.value} block {block} "
            f"(likely pre-deployment or unfunded)"
        )

    value_raw = int(result, 16)
    return Decimal(value_raw) / Decimal(10 ** underlying_decimals)
