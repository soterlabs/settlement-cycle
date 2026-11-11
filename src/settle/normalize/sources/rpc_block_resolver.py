"""RPC-backed `IBlockResolver` — wraps `extract.rpc.find_block_at_or_before`."""

from __future__ import annotations

from datetime import UTC, date, datetime

from ...domain.primes import Chain
from ...extract import rpc


class RPCBlockResolver:
    """Implements `IBlockResolver` via JSON-RPC binary search + block timestamp.

    Roughly 25 RPC calls per resolution. Result is cached at the Extract layer
    (via ``rpc.find_block_at_or_before`` → cached primitives), so repeat calls
    at the same anchor are free.
    """

    def __init__(self):
        self._verified: dict[tuple[Chain, int], int] = {}

    def block_at_or_before(self, chain: str, anchor_utc: datetime) -> int:
        if anchor_utc.tzinfo is None:
            anchor_utc = anchor_utc.replace(tzinfo=UTC)
        network = Chain(chain)
        target = int(anchor_utc.timestamp())
        if (network, target) in self._verified:
            return self._verified[network, target]
        block = rpc.find_block_at_or_before(network, anchor_utc)

        def brackets_cutoff(candidate: int) -> bool:
            return (
                rpc.block_timestamp(network, candidate)
                <= target
                < rpc.block_timestamp(network, candidate + 1)
            )

        # Legacy lookups could cache the current head before the requested
        # day had ended. Its timestamp is <= cutoff, but it is not the last
        # block of that day. Check the successor rather than trusting the
        # cache entry or scaling downstream balances to fit a control.
        if not brackets_cutoff(block):
            block = rpc._find_block_at_or_before_rpc(network, anchor_utc, target)
            if not brackets_cutoff(block):
                raise ValueError(f"{chain}: RPC blocks do not bracket the requested cutoff")
        self._verified[network, target] = block
        return block

    def block_to_date(self, chain: str, block: int) -> date:
        ts = rpc.block_timestamp(Chain(chain), block)
        return datetime.fromtimestamp(ts, tz=UTC).date()
