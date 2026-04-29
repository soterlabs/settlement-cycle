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
# Curve-specific selectors (get_virtual_price, balances) live in extract/curve.py.

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


DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SEC = 0.5


def _post(url: str, method: str, params: list[Any]) -> Any:
    """JSON-RPC POST with bounded retry on transient transport errors.

    Retries ``Timeout``, ``ConnectionError`` and HTTP 5xx — typical flaky-node
    failures. JSON-RPC application errors (the ``"error"`` field on a 200 OK
    response) are NOT retried since they reflect deterministic call problems
    (revert, bad params, etc.).
    """
    import time as _time
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last_exc: Exception | None = None
    for attempt in range(DEFAULT_RETRY_ATTEMPTS):
        try:
            r = requests.post(url, json=body, timeout=DEFAULT_TIMEOUT)
            if 500 <= r.status_code < 600:
                last_exc = requests.HTTPError(
                    f"{r.status_code} {r.reason}", response=r,
                )
            else:
                r.raise_for_status()
                payload = r.json()
                if "error" in payload:
                    raise RPCError(f"{method} error: {payload['error']}")
                return payload["result"]
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
        if attempt < DEFAULT_RETRY_ATTEMPTS - 1:
            _time.sleep(DEFAULT_RETRY_BACKOFF_SEC * (2 ** attempt))
    assert last_exc is not None
    raise last_exc


from ._abi import pad_address as _pad_address, pad_uint as _pad_uint  # noqa: E402


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


def _decode_uint(raw: str) -> int:
    """Decode a uint256 eth_call return. Treats empty/zero-length results as 0
    (token contract didn't exist at this block, or the call reverted)."""
    if raw is None or raw in ("0x", "0x0"):
        return 0
    try:
        return int(raw, 16)
    except ValueError:
        return 0


@cached(source_id="rpc.balance_of")
def balance_of(chain: Chain, token: Address, holder: Address, block: int) -> int:
    """ERC-20 `balanceOf(holder)` at a specific block. Returns 0 if the token
    contract didn't exist at this block (RPC reverts with 0x or rejects the
    call). The caller may need to distinguish "non-existent contract" from
    "real 0 balance" — for settlement, treating both as 0 is correct."""
    data = SEL_BALANCE_OF + _pad_address(holder)
    try:
        return _decode_uint(eth_call(chain, token, data, block))
    except (RPCError, requests.HTTPError):
        return 0


SEL_SCALED_BALANCE_OF = "0x1da24f3e"   # scaledBalanceOf(address)


@cached(source_id="rpc.scaled_balance_of")
def scaled_balance_of(chain: Chain, token: Address, holder: Address, block: int) -> int:
    """``scaledBalanceOf(holder)`` for Aave V3 aTokens / SparkLend spTokens.

    Returns the *un-rebased* principal in scaled units. Combined with
    ``balanceOf`` (rebased), it lets us derive the liquidity index per holder:
    ``index = balanceOf × RAY / scaledBalanceOf`` — and from there the rebase
    yield over a period: ``yield = scaled_som × (index_eom − index_som) / RAY``.

    The model is exact for Aave V3 / SparkLend (which expose ``scaledBalanceOf``).
    Tokens without a scaled-balance accessor will revert; the caller must
    catch and fall back to face-value inflow accounting.
    """
    data = SEL_SCALED_BALANCE_OF + _pad_address(holder)
    try:
        return _decode_uint(eth_call(chain, token, data, block))
    except (RPCError, requests.HTTPError):
        return 0


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
    """ERC-4626 `convertToAssets(shares)`. Returns 0 if vault didn't exist at this block."""
    data = SEL_CONVERT_TO_ASSETS + _pad_uint(shares)
    try:
        return _decode_uint(eth_call(chain, vault, data, block))
    except (RPCError, requests.HTTPError):
        return 0


# ----------------------------------------------------------------------------
# Block-time helpers
# ----------------------------------------------------------------------------

def latest_block(chain: Chain) -> int:
    return int(_post(rpc_url(chain), "eth_blockNumber", []), 16)


def block_timestamp(chain: Chain, block: int) -> int:
    """UNIX timestamp of the given block."""
    raw = _post(rpc_url(chain), "eth_getBlockByNumber", [hex(block), False])
    return int(raw["timestamp"], 16)


# Default chunk for `eth_getLogs` pagination — Alchemy free tier caps at 10k
# blocks per call. Override via the kwarg if a provider supports more.
LOGS_CHUNK_BLOCKS = 10_000


def eth_get_logs(
    chain: Chain,
    address: Address,
    topics: list[str | None],
    from_block: int,
    to_block: int,
    *,
    chunk_blocks: int = LOGS_CHUNK_BLOCKS,
) -> list[dict]:
    """Paginated ``eth_getLogs``.

    ``topics`` matches Ethereum filter semantics: ``None`` for wildcard, a
    single 0x-prefixed 32-byte hex string for a fixed match. Length up to 4.

    Returns the raw log dicts (block_number, transaction_hash, topics, data, …)
    in chronological order. Pagination splits the requested range into
    ``chunk_blocks`` windows so requests stay within Alchemy's free-tier limit.
    """
    if from_block > to_block:
        return []
    out: list[dict] = []
    cursor = from_block
    while cursor <= to_block:
        end = min(cursor + chunk_blocks - 1, to_block)
        params = [{
            "address": address.hex,
            "topics": topics,
            "fromBlock": hex(cursor),
            "toBlock": hex(end),
        }]
        out.extend(_post(rpc_url(chain), "eth_getLogs", params))
        cursor = end + 1
    return out


def find_block_at_or_before(chain: Chain, ts: datetime) -> int:
    """Binary search for the highest block whose timestamp ≤ `ts` (UTC).

    Used by `Period.from_month` to resolve `pin_blocks`. Roughly 25 RPC calls per chain.

    Note: this function intentionally does NOT pin to a specific block (it's
    deciding *which* block to pin to). All other reads in this module enforce
    block-pinning per PRD §10 conv. 1.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    target = int(ts.timestamp())

    high = latest_block(chain)
    if block_timestamp(chain, high) <= target:
        return high

    # Reject targets that precede genesis — otherwise the search collapses to
    # block 0 and silently pins every downstream RPC call to genesis (zero
    # balances, no error).
    if block_timestamp(chain, 0) > target:
        raise ValueError(
            f"find_block_at_or_before({chain}, {ts.isoformat()}): target precedes "
            f"genesis (block 0 timestamp = {block_timestamp(chain, 0)}). "
            "Likely a wrong settlement period or chain mismatch."
        )

    low = 0
    while low < high:
        mid = (low + high + 1) // 2
        mid_ts = block_timestamp(chain, mid)
        if mid_ts <= target:
            low = mid
        else:
            high = mid - 1
    return low
