"""HyperSync-backed ``IBlockResolver`` — block↔timestamp off HyperSync, not RPC.

Resolves exact date boundaries from HyperSync timestamps. Timestamp-guided
probes with a periodic bisection fallback reduce requests on regular chains;
only observed timestamps determine the result. Archive RPC remains available
for the contract-state reads needed by valuation.

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

    def validate_finalized_boundary(self, chain: str, block: int, anchor_utc: datetime) -> None:
        """Reject a head clamp, wrong cutoff or pin inside the reorg window."""
        from ...extract.hypersync_store import _reorg_margin

        target = int(anchor_utc.timestamp())
        if not self._ts(chain, block) <= target < self._ts(chain, block + 1):
            raise hypersync.HyperSyncError(f"{chain}: block {block} is not the requested UTC boundary")
        margin = _reorg_margin()
        if margin < 0:
            raise ValueError("HYPERSYNC_REORG_MARGIN must be nonnegative")
        if block > hypersync.archive_height(chain) - margin:
            raise hypersync.HyperSyncError(f"{chain}: cutoff block {block} is not finalized; retry later")
