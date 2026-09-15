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
