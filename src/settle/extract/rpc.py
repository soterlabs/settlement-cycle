"""JSON-RPC client. Raw HTTP — no web3.py dependency.

Per-chain RPC URLs are read from env vars (e.g. `ETH_RPC`, `BASE_RPC`). All calls
that take a `block` parameter pin to that block; never use "latest" in production.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

from ..domain.primes import Address, Chain
from .cache import cached

# Function selectors (first 4 bytes of keccak256 of the signature)
SEL_BALANCE_OF = "0x70a08231"           # balanceOf(address)
SEL_DECIMALS = "0x313ce567"             # decimals()
SEL_TOTAL_SUPPLY = "0x18160ddd"         # totalSupply()
SEL_CONVERT_TO_ASSETS = "0x07a2d13a"    # convertToAssets(uint256)
SEL_GET_VIRTUAL_PRICE = "0xbb7b8b80"    # get_virtual_price()
SEL_BALANCES_UINT256 = "0x4903b0d1"     # balances(uint256) — Curve

DEFAULT_TIMEOUT = 30


class RPCError(RuntimeError):
    """Raised on JSON-RPC error responses."""


# Explicit chain → env-var mapping. Avoids the silent breakage that would happen
# if someone added a new chain without realising `Chain.ETHEREUM` already had an
# alias (`ETH_RPC`, not `ETHEREUM_RPC`).
RPC_ENV_VARS: dict[Chain, str] = {
    Chain.ETHEREUM:    "ETH_RPC",
    Chain.BASE:        "BASE_RPC",
    Chain.ARBITRUM:    "ARBITRUM_RPC",
    Chain.OPTIMISM:    "OPTIMISM_RPC",
    Chain.UNICHAIN:    "UNICHAIN_RPC",
    Chain.AVALANCHE_C: "AVALANCHE_C_RPC",
    Chain.PLUME:       "PLUME_RPC",
    Chain.MONAD:       "MONAD_RPC",
}


def rpc_url(chain: Chain) -> str:
    """Resolve RPC URL for ``chain`` from the explicit mapping in ``RPC_ENV_VARS``.

    Raises if the chain isn't in the mapping (caller hit an unsupported chain)
    or if the env var isn't set.
    """
    if chain not in RPC_ENV_VARS:
        raise RuntimeError(f"No RPC env-var mapping for chain {chain}")
    var = RPC_ENV_VARS[chain]
    url = os.environ.get(var)
    if not url:
        raise RuntimeError(f"Missing env var {var} (RPC URL for chain {chain})")
    return url


def _post(url: str, method: str, params: list[Any]) -> Any:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    r = requests.post(url, json=body, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    payload = r.json()
    if "error" in payload:
        raise RPCError(f"{method} error: {payload['error']}")
    return payload["result"]


def _pad_address(a: Address) -> str:
    return a.value.hex().rjust(64, "0")


def _pad_uint(n: int) -> str:
    if n < 0:
        raise ValueError("only unsigned ints supported")
    return hex(n)[2:].rjust(64, "0")


# ----------------------------------------------------------------------------
# Read methods — all pinned to a block
# ----------------------------------------------------------------------------

@cached(source_id="rpc.eth_call")
def eth_call(chain: Chain, contract: Address, data: str, block: int) -> str:
    """Raw eth_call. `data` = 0x-prefixed hex selector + abi-encoded args."""
    return _post(
        rpc_url(chain),
        "eth_call",
        [{"to": contract.hex, "data": data}, hex(block)],
    )


@cached(source_id="rpc.balance_of")
def balance_of(chain: Chain, token: Address, holder: Address, block: int) -> int:
    """ERC-20 `balanceOf(holder)` at a specific block."""
    data = SEL_BALANCE_OF + _pad_address(holder)
    return int(eth_call(chain, token, data, block), 16)


@cached(source_id="rpc.native_balance")
def native_balance(chain: Chain, holder: Address, block: int) -> int:
    """Native gas balance (wei) at a specific block."""
    return int(_post(rpc_url(chain), "eth_getBalance", [holder.hex, hex(block)]), 16)


@cached(source_id="rpc.decimals")
def decimals_of(chain: Chain, token: Address, block: int) -> int:
    """ERC-20 `decimals()` at a specific block.

    ERC-20 decimals are immutable for canonical tokens but the package treats no
    eth_call as exempt from block-pinning (PRD §10 conv. 1). In production
    settlement, decimals are sourced from `Token.decimals` in the YAML config —
    this RPC call is only used by the `settle debug rpc-balance` ad-hoc tool.
    """
    return int(eth_call(chain, token, SEL_DECIMALS, block), 16)


@cached(source_id="rpc.convert_to_assets")
def convert_to_assets(chain: Chain, vault: Address, shares: int, block: int) -> int:
    """ERC-4626 `convertToAssets(shares)`."""
    data = SEL_CONVERT_TO_ASSETS + _pad_uint(shares)
    return int(eth_call(chain, vault, data, block), 16)


# ----------------------------------------------------------------------------
# Block-time helpers
# ----------------------------------------------------------------------------

def latest_block(chain: Chain) -> int:
    return int(_post(rpc_url(chain), "eth_blockNumber", []), 16)


def block_timestamp(chain: Chain, block: int) -> int:
    """UNIX timestamp of the given block."""
    raw = _post(rpc_url(chain), "eth_getBlockByNumber", [hex(block), False])
    return int(raw["timestamp"], 16)


def find_block_at_or_before(chain: Chain, ts: datetime) -> int:
    """Binary search for the highest block whose timestamp ≤ `ts` (UTC).

    Used by `Period.from_month` to resolve `pin_blocks`. Roughly 25 RPC calls per chain.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    target = int(ts.timestamp())

    high = latest_block(chain)
    if block_timestamp(chain, high) <= target:
        return high

    low = 0
    while low < high:
        mid = (low + high + 1) // 2
        mid_ts = block_timestamp(chain, mid)
        if mid_ts <= target:
            low = mid
        else:
            high = mid - 1
    return low
