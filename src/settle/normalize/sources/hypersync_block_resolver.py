"""HyperSync-backed ``IBlockResolver`` — block↔timestamp off HyperSync, not RPC.

Block resolution (block-at-EoD-timestamp, per day per chain) is the single
largest RPC bucket in a settlement run (~40% of calls — the binary-search
``block_timestamp`` probes). This resolver serves those probes from HyperSync
instead of the archive RPC:

  * identical results — the binary search mirrors
    ``extract.rpc._find_block_at_or_before_rpc`` and HyperSync block timestamps
    are byte-identical to ``eth_getBlockByNumber`` (verified);
  * off the archive RPC — frees it for the ``eth_call`` pricing that only it
    can serve, and is faster/cheaper per probe;
  * works where the RPC is lagging/pruned — e.g. monad, whose public RPC can't
    serve historical blocks but whose HyperSync archive is well ahead of head.

Drop-in behind the ``IBlockResolver`` protocol (registry name ``hypersync``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone

from ...extract import hypersync


class HyperSyncBlockResolver:
    """Implements ``IBlockResolver`` via HyperSync block timestamps.

    ``find_fn`` / ``ts_fn`` are injectable for tests; they default to the
    cached ``extract.hypersync`` helpers.
    """

    def __init__(
        self,
        *,
        find_fn: Callable[[str, int], int] = hypersync.find_block_at_or_before,
        ts_fn: Callable[[str, int], int] = hypersync.block_timestamp,
    ) -> None:
        self._find = find_fn
        self._ts = ts_fn

    def block_at_or_before(self, chain: str, anchor_utc: datetime) -> int:
        if anchor_utc.tzinfo is None:
            anchor_utc = anchor_utc.replace(tzinfo=timezone.utc)
        return self._find(chain, int(anchor_utc.timestamp()))

    def block_to_date(self, chain: str, block: int) -> date:
        return datetime.fromtimestamp(self._ts(chain, block), tz=timezone.utc).date()
