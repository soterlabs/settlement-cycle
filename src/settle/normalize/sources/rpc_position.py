"""RPC-backed `IPositionBalanceSource` + `IConvertToAssetsSource` + `IPsm3Source`."""

from __future__ import annotations

from ...domain.primes import Address, Chain
from ...extract import rpc


class RPCPositionBalanceSource:
    """Snapshot ERC-20 balance via `eth_call` `balanceOf(holder)`."""

    def balance_at(self, chain: str, token: bytes, holder: bytes, block: int) -> int:
        return rpc.balance_of(Chain(chain), Address(token), Address(holder), block)


class RPCConvertToAssetsSource:
    """ERC-4626 `convertToAssets(shares)` via `eth_call`."""

    def convert_to_assets(self, chain: str, vault: bytes, shares: int, block: int) -> int:
        return rpc.convert_to_assets(Chain(chain), Address(vault), shares, block)


class RPCPsm3Source:
    """Spark PSM3 reads via `eth_call`: `shares(holder)` and
    `convertToAssetValue(numShares)`."""

    def shares_of(self, chain: str, psm3: bytes, holder: bytes, block: int) -> int:
        return rpc.psm3_shares(Chain(chain), Address(psm3), Address(holder), block)

    def convert_to_asset_value(self, chain: str, psm3: bytes, num_shares: int, block: int) -> int:
        return rpc.psm3_convert_to_asset_value(Chain(chain), Address(psm3), num_shares, block)
