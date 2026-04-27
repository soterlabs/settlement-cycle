"""RPC-backed `IPositionBalanceSource` + `IConvertToAssetsSource`."""

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
