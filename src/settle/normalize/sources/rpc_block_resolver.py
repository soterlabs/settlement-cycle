"""RPC-backed `IBlockResolver` — wraps `extract.rpc.find_block_at_or_before`."""

from __future__ import annotations

from datetime import date, datetime, timezone

from ...domain.primes import Chain
from ...extract import rpc


class RPCBlockResolver:
    """Implements `IBlockResolver` via JSON-RPC binary search + block timestamp.

    Roughly 25 RPC calls per resolution. Result is cached at the Extract layer
    (via ``rpc.find_block_at_or_before`` → cached primitives), so repeat calls
    at the same anchor are free.
    """

    def block_at_or_before(self, chain: str, anchor_utc: datetime) -> int:
        return rpc.find_block_at_or_before(Chain(chain), anchor_utc)

    def block_to_date(self, chain: str, block: int) -> date:
        ts = rpc.block_timestamp(Chain(chain), block)
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
