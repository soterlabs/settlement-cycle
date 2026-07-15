"""JSON-RPC client. Raw HTTP — no web3.py dependency.

Per-chain RPC URLs are read from env vars (e.g. `ETH_RPC`, `BASE_RPC`). All calls
that take a `block` parameter pin to that block; never use "latest" in production.
"""

from __future__ import annotations

import logging
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
SEL_TOTAL_ASSETS = "0x01e1d114"         # totalAssets() — ERC-4626
# PSM3 (Spark) — non-standard ABI: shares are internal accounting (no Transfer
# events) and the rate uses ``convertToAssetValue(uint256)`` rather than the
# ERC-4626 ``convertToAssets(uint256)``.
SEL_PSM3_SHARES = "0xce7c2ac2"          # shares(address)
SEL_PSM3_CONVERT_TO_ASSET_VALUE = "0x41c094e0"  # convertToAssetValue(uint256)
# Curve-specific selectors (get_virtual_price, balances) live in extract/curve.py.
# ERC-7540 async vault in-flight request queries (request_id=0 per standard).
SEL_PENDING_DEPOSIT_REQUEST   = "0x26c6f96c"   # pendingDepositRequest(uint256,address)
SEL_CLAIMABLE_DEPOSIT_REQUEST = "0x995ea21a"   # claimableDepositRequest(uint256,address)
SEL_PENDING_REDEEM_REQUEST    = "0xf5a23d8d"   # pendingRedeemRequest(uint256,address)
SEL_CLAIMABLE_REDEEM_REQUEST  = "0xeaed1d07"   # claimableRedeemRequest(uint256,address)

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


DEFAULT_RETRY_ATTEMPTS = 10
DEFAULT_RETRY_BACKOFF_SEC = 0.3
RETRY_BACKOFF_CAP_SEC = 3.0
# Null-block retry is shorter than the transport-level retry: a null result
# is a node-lag artifact that resolves in seconds, not a transport outage.
# Keeping this tight avoids compound stalls when nested with ``_post``'s
# own retry loop (each iteration here can already absorb 10 transport
# retries).
_NULL_BLOCK_RETRY_ATTEMPTS = 5

# JSON-RPC error codes/messages that indicate a transient upstream failure
# rather than a deterministic call problem. drpc surfaces upstream-node
# flakiness as ``-32001`` ("wrong json-rpc response") and ``19`` ("Temporary
# internal error"). Retrying these is safe because the call itself is
# well-formed; the load balancer just hit a bad node.
_TRANSIENT_RPC_CODES = {-32001, 19}
_TRANSIENT_RPC_MSG_FRAGMENTS = (
    "wrong json-rpc response",
    "temporary internal error",
    "rate limit",
)


def _is_transient_rpc_error(err: Any) -> bool:
    """True if a JSON-RPC ``error`` payload reflects provider/load-balancer
    flakiness (drpc upstream issues, rate limits) rather than a deterministic
    call problem (revert, bad params).
    """
    if isinstance(err, dict):
        if err.get("code") in _TRANSIENT_RPC_CODES:
            return True
        msg = err.get("message", "").lower()
    else:
        msg = str(err).lower()
    return any(frag in msg for frag in _TRANSIENT_RPC_MSG_FRAGMENTS)


_rpc_log = logging.getLogger(__name__)


def _post(url: str, method: str, params: list[Any]) -> Any:
    """JSON-RPC POST with bounded retry on transient transport errors.

    Retries on ``Timeout``, ``ConnectionError``, HTTP 5xx, and a small set of
    JSON-RPC error codes/messages known to be transient at provider load
    balancers (drpc upstream-node flakiness, rate-limits). Other JSON-RPC
    application errors (revert, bad params) are NOT retried.
    """
    import time as _time
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last_exc: Exception | None = None
    # Mask the API key in log output (last path segment of the URL).
    _url_label = url.rsplit("/", 1)[-1][:12] + "…"
    for attempt in range(DEFAULT_RETRY_ATTEMPTS):
        try:
            r = requests.post(url, json=body, timeout=DEFAULT_TIMEOUT)
            # 408 (Request Timeout) and 429 (Too Many Requests) are
            # provider-level transients; treat like 5xx for retry purposes.
            if 500 <= r.status_code < 600 or r.status_code in (408, 429):
                last_exc = requests.HTTPError(
                    f"{r.status_code} {r.reason}", response=r,
                )
            else:
                r.raise_for_status()
                payload = r.json()
                if "error" not in payload:
                    return payload["result"]
                err = payload["error"]
                if not _is_transient_rpc_error(err):
                    raise RPCError(f"{method} error: {err}")
                last_exc = RPCError(f"{method} transient error: {err}")
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e

        # Log on first failure so it's immediately visible, then every 10
        # attempts so we can see that the retry loop is spinning.
        if attempt == 0:
            _rpc_log.warning(
                "RPC transient failure (attempt 1/%d) — %s %s: %s",
                DEFAULT_RETRY_ATTEMPTS, method, _url_label, last_exc,
            )
        elif attempt % 10 == 0:
            _rpc_log.warning(
                "RPC still retrying (attempt %d/%d) — %s %s: %s",
                attempt + 1, DEFAULT_RETRY_ATTEMPTS, method, _url_label, last_exc,
            )

        if attempt < DEFAULT_RETRY_ATTEMPTS - 1:
            backoff = min(
                DEFAULT_RETRY_BACKOFF_SEC * (2 ** attempt),
                RETRY_BACKOFF_CAP_SEC,
            )
            _time.sleep(backoff)

    _rpc_log.error(
        "RPC exhausted all %d retries — %s %s: %s",
        DEFAULT_RETRY_ATTEMPTS, method, _url_label, last_exc,
    )
    assert last_exc is not None
    raise last_exc


from ._abi import pad_address as _pad_address, pad_uint as _pad_uint  # noqa: E402


# ----------------------------------------------------------------------------
# Read methods — all pinned to a block
# ----------------------------------------------------------------------------

# Explicit gas cap sent in every eth_call. Needed because drpc's Monad
# endpoint rejects requests with no ``gas`` field, claiming the implicit
# block-gas exceeds its provider limit ("user-specified gas exceeds
# provider limit", JSON-RPC code -32603). All other providers we use
# (publicnode Ethereum/Base, official Avalanche, Plume) tolerate this
# cap, so we set it unconditionally for simpler code. 10M is well above
# any single-contract read we make (V3 ticks, ERC-4626 conversion, etc.)
# and below every chain's block gas limit, so simulator behaviour is
# unchanged on the chains where it wasn't required.
_ETH_CALL_GAS_CAP_HEX = "0x989680"  # 10,000,000


@cached(source_id="rpc.eth_call")
def eth_call(chain: Chain, contract: Address, data: str, block: int) -> str:
    """Raw eth_call. `data` = 0x-prefixed hex selector + abi-encoded args."""
    return _post(
        rpc_url(chain),
        "eth_call",
        [
            {"to": contract.hex, "data": data, "gas": _ETH_CALL_GAS_CAP_HEX},
            hex(block),
        ],
    )


def _decode_uint(raw: str) -> int:
    """Decode a uint256 eth_call return. Maps empty/zero-length results (``"0x"``
    or ``"0x0"``) to 0.

    ⚠ Disambiguating ``"0x"``: most JSON-RPC providers return ``"0x"`` for ANY
    EVM revert that doesn't include error data — including a deployed contract
    that reverts because it's paused, has a custom guard, or returns no data
    on edge inputs. The 0 we substitute is the right answer for the common
    case (contract not yet deployed at this block) but masks legitimate
    reverts as a $0 balance / share. If you're calling this from a context
    where a real revert on an existing contract should NOT be silently
    zeroed, check ``is_contract_deployed`` first OR catch the cached zero
    upstream and re-validate.

    Callers in this module that currently rely on the ``"0x"`` → 0 mapping:
    ``balance_of``, ``convert_to_assets``, ``total_supply_of``,
    ``scaled_balance_of`` (via the same-named helper), ``vault_share_to_assets``,
    ``psm3_shares``, ``psm3_convert_to_asset_value``. All accept the
    conflation — for our Grove + Spark use cases the affected contracts
    either exist throughout the period or are queried at pre-deployment
    blocks where the 0 is correct.
    """
    if raw is None or raw in ("0x", "0x0"):
        return 0
    try:
        return int(raw, 16)
    except ValueError:
        return 0


@cached(source_id="rpc.is_contract_deployed")
def is_contract_deployed(chain: Chain, contract: Address, block: int) -> bool:
    """Return True if *contract* has non-empty bytecode at *block*.

    Uses ``eth_getCode`` so it works correctly for contracts not yet deployed
    at the SoM block (e.g. a vault first deployed mid-period).
    """
    raw = _post(rpc_url(chain), "eth_getCode", [contract.hex, hex(block)])
    return bool(raw) and raw != "0x"


@cached(source_id="rpc.balance_of")
def balance_of(chain: Chain, token: Address, holder: Address, block: int) -> int:
    """ERC-20 `balanceOf(holder)` at a specific block. Returns 0 if the token
    contract didn't exist at this block (RPC reverts with 0x — handled inside
    ``_decode_uint`` without raising).

    On exhausted-retry RPC failure (RPCError / HTTPError after all attempts in
    ``eth_call``) this function **raises**. Caching an error as ``0`` here
    would poison the cache: subsequent runs would silently use the bad
    zero as if it were a real balance, and the warning that pointed to the
    transient outage would not re-fire. Earlier this function returned 0
    with a WARNING, which produced exactly that bug — a Grove May 2026
    re-run with a fresh RPC URL kept serving the cached 0 from the prior
    publicnode-archive failure. Hard-fail is the right contract: callers
    that genuinely want soft-fail can wrap a try/except, but the cache
    layer never persists an error as a successful value.
    """
    data = SEL_BALANCE_OF + _pad_address(holder)
    return _decode_uint(eth_call(chain, token, data, block))


@cached(source_id="rpc.total_supply_of")
def total_supply_of(chain: Chain, token: Address, block: int) -> int:
    """ERC-20 ``totalSupply()`` at a specific block.

    For Aave V3 aTokens and SparkLend spTokens this returns the *rebased*
    total supply (the sum of all depositors' ``balanceOf`` values), denominated
    in underlying token units. Combined with ``balance_of(token, holder, block)``
    it gives the holder's proportional share of the pool:
    ``share = balanceOf(holder) / totalSupply()``.

    Returns 0 if the token didn't exist at this block (RPC reverts with 0x —
    handled inside ``_decode_uint`` without raising). On RPC infra failure
    this **raises**: caching an error as 0 here would silently zero out
    every pool-share calculation that depends on it, and the SparkLend
    caller (``monthly_pnl.lending_idle_usds``) already wraps the call in
    a try/except that carries forward the prior value on error — the same
    semantics the old soft-fail provided, but without cache poisoning.
    """
    return _decode_uint(eth_call(chain, token, SEL_TOTAL_SUPPLY, block))


@cached(source_id="rpc.total_assets_of")
def total_assets_of(chain: Chain, vault: Address, block: int) -> int:
    """ERC-4626 ``totalAssets()`` at a specific block — total underlying-token
    value backing all shares.

    Used by the Spark Savings V2 (S2) VSR-liability computation:
    ``totalAssets()`` ≈ ``totalSupply() × pps``, and is the depositor
    liability that Spark owes at any moment. Reading it directly avoids a
    separate ``convertToAssets(totalSupply)`` call.

    Hard-fails on RPC infra error (same semantics as ``total_supply_of``):
    silently caching 0 here would under-state Spark's VSR liability and
    over-state ``prime_agent_revenue``.
    """
    return _decode_uint(eth_call(chain, vault, SEL_TOTAL_ASSETS, block))


SEL_ILKS = "0xd9638d36"              # ilks(bytes32) → (Art, rate, spot, line, dust)
_RAY = 10 ** 27

SEL_SCALED_BALANCE_OF = "0x1da24f3e"   # scaledBalanceOf(address)


@cached(source_id="rpc.scaled_balance_of")
def scaled_balance_of(chain: Chain, token: Address, holder: Address, block: int) -> int:
    """``scaledBalanceOf(holder)`` for Aave V3 aTokens / SparkLend spTokens.

    Returns the *un-rebased* principal in scaled units. Combined with
    ``balanceOf`` (rebased), it lets us derive the liquidity index per holder:
    ``index = balanceOf × RAY / scaledBalanceOf`` — and from there the rebase
    yield over a period: ``yield = scaled_som × (index_eom − index_som) / RAY``.

    The model is exact for Aave V3 / SparkLend (which expose ``scaledBalanceOf``).
    Tokens without a scaled-balance accessor revert; that case is preserved by
    returning 0 so the caller can fall back to face-value inflow accounting.

    Execution-revert handling: most nodes return ``result: "0x"`` for a revert
    without data, which ``_decode_uint`` already maps to 0. A few nodes
    instead surface the revert as a JSON-RPC error with "execution reverted"
    in the message — we treat that string specifically as the same case.

    Any OTHER RPC error (transient transport failures that exhausted retries,
    timeouts, gateway errors) propagates. Caching an infra failure as 0 here
    used to corrupt yield math for the entire Aave/Spark stack until the
    cache was hand-purged.
    """
    data = SEL_SCALED_BALANCE_OF + _pad_address(holder)
    try:
        return _decode_uint(eth_call(chain, token, data, block))
    except (RPCError, requests.HTTPError) as e:
        # Catch BOTH RPCError (JSON-RPC 200 with error payload) and HTTPError
        # (some providers — drpc under load, Infura on certain plans — surface
        # an execution revert as an HTTP 5xx instead of a JSON-RPC error). The
        # ``"execution reverted"`` substring lives in the response body in
        # either case; if it's present we still want the soft-fail-to-0 path
        # (token lacks ``scaledBalanceOf`` accessor). Without HTTPError in the
        # catch, the 5xx path would crash callers that previously soft-failed.
        if "execution reverted" in str(e).lower():
            return 0
        raise


SEL_POOL = "0x7535d246"                  # POOL()
SEL_UNDERLYING_ASSET = "0xb16a19de"      # UNDERLYING_ASSET_ADDRESS()


@cached(source_id="rpc.aave_pool")
def aave_pool(chain: Chain, token: Address, block: int) -> bytes:
    """``POOL()`` on an Aave V3 aToken / SparkLend spToken — the LendingPool
    that emits ``ReserveDataUpdated`` for the reserve. Immutable per aToken;
    one cached read is enough. Returns the 20-byte pool address."""
    return _decode_uint(eth_call(chain, token, SEL_POOL, block)).to_bytes(20, "big")


@cached(source_id="rpc.aave_underlying_asset")
def aave_underlying_asset(chain: Chain, token: Address, block: int) -> bytes:
    """``UNDERLYING_ASSET_ADDRESS()`` on an aToken/spToken — the reserve key
    (topic1) of its ``ReserveDataUpdated`` events. Immutable; cached."""
    return _decode_uint(eth_call(chain, token, SEL_UNDERLYING_ASSET, block)).to_bytes(20, "big")


@cached(source_id="rpc.ilk_rate")
def ilk_rate(chain: Chain, vat: Address, ilk: bytes, block: int) -> int:
    """``Vat.ilks(ilk).rate`` at ``block`` — the accumulated stability-fee
    index in raw ray units (1e27 = 1.0).

    Used to convert normalised debt (``Art``, in wad) to actual outstanding
    USDS: ``actual_usds = Art * rate / 1e45``.

    Returns ``10**27`` (= 1.0 ray) for uninitialised ilks (``rate == 0``
    on-chain) or empty responses, so callers treating it as a multiplier
    degrade gracefully to Art-only semantics — correct for ilks whose rate
    is always 1.0 (e.g. ALLOCATOR-BLOOM-A).
    """
    data = SEL_ILKS + ilk.hex()
    raw = eth_call(chain, vat, data, block)
    hx = raw[2:] if raw.startswith("0x") else raw
    if len(hx) < 128:
        return _RAY
    rate_raw = int(hx[64:128], 16)
    return rate_raw if rate_raw > 0 else _RAY


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
    """ERC-4626 `convertToAssets(shares)`. Returns 0 if vault didn't exist at this
    block (RPC reverts with 0x — handled inside ``_decode_uint`` without raising).

    On exhausted-retry RPC failure (RPCError / HTTPError after all attempts in
    ``eth_call``) this function **raises** — same hard-fail contract as
    ``balance_of``. Caching an error as 0 here poisons the cache: every
    subsequent read returns the cached 0, the vault's share price evaluates
    to 0, and any position priced as ``balance × convertToAssets`` reports
    $0 even with a real on-chain balance. Earlier this function returned 0
    silently, which produced exactly that bug on Grove May 2026 (E19/E23
    on Base): an upstream Alchemy hiccup during fixture capture cached 0,
    and the position was reported as -$100M phantom loss until the cache
    was hand-purged.
    """
    data = SEL_CONVERT_TO_ASSETS + _pad_uint(shares)
    return _decode_uint(eth_call(chain, vault, data, block))


@cached(source_id="rpc.psm3_shares")
def psm3_shares(chain: Chain, psm3: Address, holder: Address, block: int) -> int:
    """Spark PSM3 ``shares(holder)``. PSM3 shares are internal accounting (no
    ERC-20 Transfer events), so we read them from the contract directly.
    Raises on persistent transport/RPC failure — a missing contract returns
    ``0x`` from ``eth_call`` which decodes to 0; an exception means the call
    couldn't complete and silently returning 0 would corrupt the timeseries."""
    data = SEL_PSM3_SHARES + _pad_address(holder)
    return _decode_uint(eth_call(chain, psm3, data, block))


@cached(source_id="rpc.psm3_convert_to_asset_value")
def psm3_convert_to_asset_value(chain: Chain, psm3: Address, num_shares: int, block: int) -> int:
    """Spark PSM3 ``convertToAssetValue(numShares)`` — returns the USDS-
    equivalent value (18 decimals) of ``numShares`` PSM3 shares at ``block``.
    Distinct from ERC-4626 ``convertToAssets`` (which PSM3 also exposes but
    requires an asset address). Raises on persistent transport/RPC failure
    (see ``psm3_shares`` for rationale)."""
    data = SEL_PSM3_CONVERT_TO_ASSET_VALUE + _pad_uint(num_shares)
    return _decode_uint(eth_call(chain, psm3, data, block))



# ----------------------------------------------------------------------------
# Block-time helpers
# ----------------------------------------------------------------------------

def latest_block(chain: Chain) -> int:
    return int(_post(rpc_url(chain), "eth_blockNumber", []), 16)


@cached(source_id="rpc.block_timestamp")
def block_timestamp(chain: Chain, block: int) -> int:
    """UNIX timestamp of the given block.

    Deterministic given (chain, block) so it caches cleanly — used by the
    binary search in ``find_block_at_or_before`` and by ``DuneBlockResolver``
    fallback paths.

    JSON-RPC returns ``{"result": null}`` (not an error) when a load-balanced
    node hasn't synced the requested block yet — observed on Monad / Plume /
    Unichain in the binary-search ``mid`` queries that approach
    ``latest_block``. Treat ``None`` as transient and retry a few times
    before surfacing a clear error; the outer retry loop in ``_post`` already
    covers timeouts / 5xx / known transient JSON-RPC error codes.

    The null-retry budget is capped at ``_NULL_BLOCK_RETRY_ATTEMPTS`` (5)
    rather than ``DEFAULT_RETRY_ATTEMPTS`` (10). Each iteration here calls
    ``_post`` which has its OWN 10-retry loop for transport errors; a deeper
    null-retry budget would compound into ~50-minute worst-case stalls per
    ThreadPoolExecutor worker. Node-lag (the actual cause of a null result)
    resolves in seconds; 5 attempts with exponential backoff is enough.
    """
    import time as _time
    for attempt in range(_NULL_BLOCK_RETRY_ATTEMPTS):
        raw = _post(rpc_url(chain), "eth_getBlockByNumber", [hex(block), False])
        if raw is not None:
            return int(raw["timestamp"], 16)
        if attempt == 0:
            _rpc_log.warning(
                "block_timestamp(%s, %d) returned null (attempt 1/%d) — "
                "load-balancer node lacks state; retrying",
                chain.value, block, _NULL_BLOCK_RETRY_ATTEMPTS,
            )
        elif attempt == _NULL_BLOCK_RETRY_ATTEMPTS - 1:
            _rpc_log.warning(
                "block_timestamp(%s, %d) still null on attempt %d/%d — "
                "about to raise",
                chain.value, block, attempt + 1, _NULL_BLOCK_RETRY_ATTEMPTS,
            )
        if attempt < _NULL_BLOCK_RETRY_ATTEMPTS - 1:
            backoff = min(
                DEFAULT_RETRY_BACKOFF_SEC * (2 ** attempt),
                RETRY_BACKOFF_CAP_SEC,
            )
            _time.sleep(backoff)
    raise RPCError(
        f"eth_getBlockByNumber({chain.value}, {block}) returned null after "
        f"{_NULL_BLOCK_RETRY_ATTEMPTS} attempts — block likely past the chain's "
        f"synced head on every load-balanced node we tried."
    )


# Default chunk for `eth_getLogs` pagination — Alchemy free tier caps at 10k
# blocks per call. Override via the kwarg if a provider supports more.
LOGS_CHUNK_BLOCKS = 10_000

# Per-chain overrides. Monad's public RPC (rpc.monad.xyz) rejects ranges
# larger than 100 blocks; use that as a safe hardcoded limit.
_LOGS_CHUNK_BY_CHAIN: dict[Chain, int] = {
    Chain.MONAD: 100,
}


def eth_get_logs(
    chain: Chain,
    address: Address,
    topics: list[str | None],
    from_block: int,
    to_block: int,
    *,
    chunk_blocks: int | None = None,
) -> list[dict]:
    """Paginated ``eth_getLogs``.

    ``topics`` matches Ethereum filter semantics: ``None`` for wildcard, a
    single 0x-prefixed 32-byte hex string for a fixed match. Length up to 4.

    Returns the raw log dicts (block_number, transaction_hash, topics, data, …)
    in chronological order. Pagination splits the requested range into
    ``chunk_blocks`` windows so requests stay within the provider's limit.
    When ``chunk_blocks`` is not supplied, ``_LOGS_CHUNK_BY_CHAIN`` is
    consulted first, falling back to ``LOGS_CHUNK_BLOCKS``.
    """
    if chunk_blocks is None:
        chunk_blocks = _LOGS_CHUNK_BY_CHAIN.get(chain, LOGS_CHUNK_BLOCKS)
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


# Chains for which we trust ``evms.blocks`` on Dune to answer "highest block
# at or before <ts>" — mirrors the orchestrator's ``_DUNE_BLOCK_CHAINS``
# whitelist. Unichain / Plume / Monad fall back to RPC binary search.
_DUNE_TIMESTAMP_CHAINS = frozenset({
    Chain.ETHEREUM, Chain.BASE, Chain.ARBITRUM,
    Chain.OPTIMISM, Chain.AVALANCHE_C,
})


def _find_block_at_or_before_rpc(chain: Chain, ts: datetime, target: int) -> int:
    """RPC binary search — ~25 ``block_timestamp`` calls (cached) plus one
    ``latest_block`` call (intrinsically non-deterministic, never cached)."""
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


def _find_block_at_or_before_dune(chain: Chain, ts: datetime) -> int | None:
    """One-shot Dune query against ``evms.blocks``. Returns ``None`` if Dune
    returned no row (e.g. ts precedes the chain's earliest indexed block)
    or if Dune isn't available — caller falls back to RPC."""
    import os
    if not os.environ.get("DUNE_API_KEY"):
        return None
    # Local imports — keeps the module import-time graph clean (extract.dune
    # has its own top-level imports and we don't want a hard cycle).
    from pathlib import Path as _Path
    from .dune import execute_query
    queries_dir = _Path(__file__).resolve().parent.parent / "queries"
    # ``pin_block=0`` — this query is parameter-deterministic on (chain, ts);
    # we don't need a snapshot anchor here. The 0 keeps the cache key stable.
    try:
        df = execute_query(
            queries_dir / "block_at_or_before.sql",
            params={"chain": chain.value, "ts": ts.replace(tzinfo=None).isoformat(sep=" ")},
            pin_block=0,
        )
    except Exception as e:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Dune block_at_or_before(%s, %s) failed (%s) — falling back to RPC binary search",
            chain.value, ts.isoformat(), e,
        )
        return None
    if df.empty or df["block_number"].iloc[0] is None:
        return None
    return int(df["block_number"].iloc[0])


@cached(source_id="rpc.find_block_at_or_before")
def find_block_at_or_before(chain: Chain, ts: datetime) -> int:
    """Highest block on ``chain`` whose timestamp ≤ ``ts`` (UTC).

    Used by ``Period.from_month`` to resolve pin blocks. Result is
    deterministic given (chain, ts) so it's wrapped in ``@cached`` —
    re-runs at the same anchor hit cache.

    Strategy:
      * For chains in ``_DUNE_TIMESTAMP_CHAINS`` and when ``DUNE_API_KEY`` is
        set: single Dune query against ``evms.blocks`` (one round-trip,
        cached after).
      * Otherwise: RPC binary search (~25 ``block_timestamp`` calls — each
        cached individually — plus one ``latest_block`` call).

    Note: this function intentionally does NOT pin to a specific block (it's
    deciding *which* block to pin to). All other reads in this module enforce
    block-pinning per PRD §10 conv. 1.
    """
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    target = int(ts.timestamp())

    if chain in _DUNE_TIMESTAMP_CHAINS:
        dune_result = _find_block_at_or_before_dune(chain, ts)
        if dune_result is not None:
            return dune_result

    return _find_block_at_or_before_rpc(chain, ts, target)
