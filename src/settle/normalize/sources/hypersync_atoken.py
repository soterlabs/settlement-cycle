"""HyperSync Aave/SparkLend aToken reconstruction (Cat C).

Wraps ``extract.aave_reconstruct`` with the per-token metadata it needs — the
LendingPool (``POOL()``) and reserve (``UNDERLYING_ASSET_ADDRESS()``), two
immutable cached RPC reads per token — plus the HyperSync block-timestamp
source. Exposes wei-exact ``reconstruct_balance`` (rebased) and
``reconstruct_scaled`` (scaled principal), both from events.

No self-verification here; the caller (HyperSyncPositionBalanceSource) probes
the reconstruction against RPC once per token before trusting it, so a wrong
pool/reserve or an unexpected event layout falls back to RPC rather than
shipping a bad number.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...extract import aave_reconstruct, hypersync, hypersync_store


def _default_metadata(chain: str, token: bytes, block: int) -> tuple[bytes, bytes]:
    from ...domain.primes import Address, Chain
    from ...extract import rpc
    tok = Address.from_str("0x" + bytes(token).hex())
    return (rpc.aave_pool(Chain(chain), tok, block),
            rpc.aave_underlying_asset(Chain(chain), tok, block))


class HyperSyncAaveSource:
    """Event-reconstructed aToken balances. ``fetch_logs`` / ``block_ts`` /
    ``metadata_fn`` are injectable for tests."""

    def __init__(
        self,
        *,
        fetch_logs: Callable[..., list[Any]] = hypersync_store.fetch_logs,
        block_ts: Callable[[str, int], int] = hypersync.block_timestamp,
        metadata_fn: Callable[[str, bytes, int], tuple[bytes, bytes]] = _default_metadata,
    ) -> None:
        self._fetch = fetch_logs
        self._block_ts = block_ts
        self._metadata_fn = metadata_fn
        self._meta: dict[tuple[str, str], tuple[bytes, bytes]] = {}

    def _metadata(self, chain: str, token: bytes, block: int) -> tuple[bytes, bytes]:
        key = (chain, bytes(token).hex())
        if key not in self._meta:
            self._meta[key] = self._metadata_fn(chain, token, block)
        return self._meta[key]

    def is_atoken(self, chain: str, token: bytes, block: int) -> bool:
        """True iff ``token`` is an Aave V3 / SparkLend aToken — i.e. it exposes
        ``POOL()`` and ``UNDERLYING_ASSET_ADDRESS()`` as non-zero addresses.

        This is the *structural* rebasing test the position-balance hybrid needs:
        Σ(Transfer) can equal ``balanceOf`` for a rebasing aToken at a block with
        no accrued interest (index ~RAY right after mint), so a value match alone
        can't tell rebasing from non-rebasing — but only aTokens carry these two
        immutable getters. The reads are memoised via the metadata cache, so a
        subsequent ``reconstruct_balance`` reuses them (no extra RPC)."""
        # Clean negative (non-aToken) is NOT an exception: ``rpc._decode_uint``
        # maps reverts / empty returns ("0x") to 0, so a plain ERC-20 yields
        # zero addresses here. A raised exception is therefore a TRANSPORT
        # failure (timeout, rate limit) and MUST propagate — swallowing it as
        # "not an aToken" would let a network blip bypass the structural gate
        # and pin a rebasing token to stale event sums (fail-open).
        pool, reserve = self._metadata(chain, token, block)
        zero = b"\x00" * 20
        return pool != zero and reserve != zero

    def reconstruct_scaled(self, chain: str, token: bytes, holder: bytes, block: int) -> int:
        return aave_reconstruct.scaled_balance_at(
            chain, token, holder, block, fetch_logs=self._fetch
        )

    def reconstruct_balance(self, chain: str, token: bytes, holder: bytes, block: int) -> int:
        pool, reserve = self._metadata(chain, token, block)
        return aave_reconstruct.rebased_balance_at(
            chain, token, holder, block,
            pool=pool, reserve=reserve,
            fetch_logs=self._fetch, block_ts=self._block_ts,
        )
