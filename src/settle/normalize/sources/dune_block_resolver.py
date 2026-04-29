"""Dune-backed `IBlockResolver` — bulk-loads (date → max_block) for a date range.

Eliminates the per-day RPC binary search bottleneck that dominates first-run
latency for inflow timeseries pricing. One Dune query covers the entire
prime lifetime; both ``block_at_or_before`` (for end-of-day anchors) and
``block_to_date`` (for arbitrary mid-day blocks) become O(log N) in-memory
lookups.

The RPC implementation in ``rpc_block_resolver.py`` stays as the default for
deployments without a Dune API key — same Protocol shape.
"""

from __future__ import annotations

import bisect
from datetime import date, datetime, timezone

from ...extract.dune import execute_query
from ._paths import QUERIES_DIR


class DuneBlockResolver:
    """Implements `IBlockResolver` by pre-loading (date, max_block) for a span.

    Construction is one Dune query; lookups are constant-time. Mid-day
    anchors (datetimes that aren't end-of-day) snap up to the day's max
    block — a small approximation that's fine for monthly-grain settlement
    where anchors are always end-of-day UTC, and inflow events use
    ``block_to_date`` only (where the inverse mapping is exact).
    """

    def __init__(
        self,
        chain: str,
        start_date: date | None = None,
        end_date: date | None = None,
        pin_block: int | None = None,
        *,
        prefetched_rows: list[dict] | None = None,
    ) -> None:
        """Either fetch from Dune OR accept pre-fetched (date, block) rows.

        Pre-fetched mode lets offline acceptance scripts load the mapping
        from a captured fixture without needing ``DUNE_API_KEY`` at run time.
        """
        self._chain = chain
        if prefetched_rows is not None:
            rows = prefetched_rows
        else:
            # The shared `blocks_at_eod.sql` template hardcodes
            # ``ethereum.blocks``. Live (non-prefetched) construction for any
            # other chain would silently load Ethereum blocks under the wrong
            # chain label — refuse instead. Per-chain runs must use
            # ``prefetched_rows=`` from a chain-specific captured fixture
            # until the SQL is re-templated for cross-chain lookup.
            if chain != "ethereum":
                raise ValueError(
                    f"DuneBlockResolver: live construction supported only for "
                    f"chain='ethereum' (got {chain!r}); pass prefetched_rows "
                    "for non-Ethereum chains."
                )
            if start_date is None or end_date is None or pin_block is None:
                raise ValueError(
                    "DuneBlockResolver: must pass either prefetched_rows or "
                    "(start_date, end_date, pin_block)."
                )
            df = execute_query(
                QUERIES_DIR / "blocks_at_eod.sql",
                params={
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
                pin_block=pin_block,
            )
            if df.empty:
                raise RuntimeError(
                    f"DuneBlockResolver: empty response for {chain} "
                    f"{start_date}→{end_date} — verify the date range covers "
                    "blocks."
                )
            df = df.sort_values("block_date").reset_index(drop=True)
            rows = df.to_dict("records")

        rows_sorted = sorted(rows, key=lambda r: r["block_date"])
        self._dates: list[date] = [
            r["block_date"].date() if hasattr(r["block_date"], "date")
            else (date.fromisoformat(r["block_date"]) if isinstance(r["block_date"], str)
                  else r["block_date"])
            for r in rows_sorted
        ]
        self._max_blocks: list[int] = [int(r["block_number"]) for r in rows_sorted]
        # ``block_to_date``'s ``bisect_left`` requires ``_max_blocks`` to be
        # ascending. The list is built from rows already sorted by date, and
        # block-number normally grows monotonically with date. A re-org or a
        # Dune-side fluke that inverts the order would silently mis-bucket
        # every inflow event. Catch it loudly at construction time.
        for i in range(1, len(self._max_blocks)):
            if self._max_blocks[i] < self._max_blocks[i - 1]:
                raise ValueError(
                    f"DuneBlockResolver({chain!r}): non-monotonic max_block at "
                    f"index {i}: {self._dates[i - 1]}={self._max_blocks[i - 1]} "
                    f"→ {self._dates[i]}={self._max_blocks[i]}"
                )

    def _check_chain(self, chain: str) -> None:
        if chain != self._chain:
            raise ValueError(
                f"DuneBlockResolver bound to {self._chain!r}; got {chain!r}"
            )

    def block_at_or_before(self, chain: str, anchor_utc: datetime) -> int:
        """Last block whose timestamp ≤ ``anchor_utc``.

        Anchors used by Compute are end-of-day UTC, so we return the day's
        ``MAX(block_number)``. For mid-day anchors we'd over-shoot; not a
        concern for monthly settlement.
        """
        self._check_chain(chain)
        if anchor_utc.tzinfo is None:
            anchor_utc = anchor_utc.replace(tzinfo=timezone.utc)
        anchor_date = anchor_utc.date()
        # Find the largest indexed date ≤ anchor_date.
        idx = bisect.bisect_right(self._dates, anchor_date) - 1
        if idx < 0:
            raise ValueError(
                f"DuneBlockResolver: anchor {anchor_utc.isoformat()} is "
                f"before any indexed date (earliest: {self._dates[0]})"
            )
        return self._max_blocks[idx]

    def block_to_date(self, chain: str, block: int) -> date:
        """UTC calendar date of the given block.

        Find the first day whose ``max_block ≥ block``. That day contains it.
        """
        self._check_chain(chain)
        idx = bisect.bisect_left(self._max_blocks, block)
        if idx >= len(self._dates):
            raise ValueError(
                f"DuneBlockResolver: block {block} is after the indexed "
                f"range (latest: {self._dates[-1]} → {self._max_blocks[-1]})"
            )
        return self._dates[idx]


class MultiChainBlockResolver:
    """Wraps per-chain ``IBlockResolver``s and dispatches by chain arg.

    Each chain has independent block-time relationships, so we keep separate
    pre-loaded mappings (typically one ``DuneBlockResolver`` per chain). For
    chains without a Dune-coverage fixture, you can pass an ``RPCBlockResolver``
    instead — same Protocol shape.
    """

    def __init__(self, resolvers: dict) -> None:
        # dict[str, IBlockResolver] keyed by Chain.value (e.g. 'ethereum').
        self._resolvers = dict(resolvers)

    def _get(self, chain: str):
        try:
            return self._resolvers[chain]
        except KeyError:
            raise ValueError(
                f"MultiChainBlockResolver: no resolver registered for {chain!r}. "
                f"Have: {sorted(self._resolvers)}"
            ) from None

    def block_at_or_before(self, chain: str, anchor_utc: datetime) -> int:
        return self._get(chain).block_at_or_before(chain, anchor_utc)

    def block_to_date(self, chain: str, block: int) -> date:
        return self._get(chain).block_to_date(chain, block)
