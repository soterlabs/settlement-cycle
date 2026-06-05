"""Dune-backed ``IPsm3Source``.

Replaces per-day RPC calls to PSM3's non-standard ABI (``shares(holder)`` +
``convertToAssetValue(shares)``) with two Dune queries per (chain, holder):

  * ``psm3_shares_history.sql``        — per-event cumulative shares for the holder
  * ``psm3_total_shares_history.sql``  — per-event cumulative pool shares

Both bulk-load once per chain (then memoised in-process) and answer block-
pinned lookups via bisect.

Motivation: the RPC variant ``RPCPsm3Source`` issues ~5 RPC calls per day per
chain (shares_of + convert_to_asset_value + 3 × balanceOf for leg reserves).
L2 providers (Alchemy on Arbitrum / Unichain) intermittently return 500s on
``convertToAssetValue`` — each retry chain costs ~5 min × 31 days × 4 chains
of wall time on a Spark cell. Bulk Dune load eliminates that variance.

The interface (``IPsm3Source``) only needs ``shares_of`` and
``convert_to_asset_value``. Pool-reserve reads (``balanceOf(USDC/USDS/sUSDS,
psm3)``) stay on the existing ``IPositionBalanceSource`` (RPC) path — they
hit standard ERC-20 tokens, not the flaky PSM3 contract itself, and the
existing ``_legs_at`` already handles their failure with carry-forward.

``convert_to_asset_value(num_shares, block)`` is computed locally as::

    num_shares × pool_total_assets(block) / pool_total_shares(block)

where ``pool_total_assets`` = USDC_reserve + USDS_reserve + sUSDS_reserve ×
eth_sUSDS_pps. The reserves and pps are fetched via the existing cached
RPC Sources (``IPositionBalanceSource`` for the per-asset reserves,
``IConvertToAssetsSource`` for the Ethereum sUSDS pps) — duplicating the
reads from ``_legs_at`` is cheap because they all hit ``@cached``.
"""

from __future__ import annotations

import bisect
import logging
from datetime import datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ...domain.primes import Address, Chain
from ...domain.sky_tokens import PSM3_LEG_TOKENS, sUSDS_ETHEREUM
from ...extract.dune import execute_query

_log = logging.getLogger(__name__)

_QUERIES_DIR = Path(__file__).resolve().parent.parent.parent / "queries"


class DunePsm3Source:
    """``IPsm3Source`` impl that bulk-loads share-balance histories from Dune
    decoded PSM3 event tables.

    Per-(chain, holder) cache: the deposit/withdraw event history for that
    holder. Per-chain cache: the pool-wide total-shares history. Both are
    keyed by ``pin_block`` (the upper bound used in the SQL); a later
    settlement run with a higher pin_block triggers a fresh fetch.

    For ``convert_to_asset_value`` to match the on-chain value at an
    arbitrary block, we need the pool's reserves + sUSDS pps at the block.
    We fetch them via the existing cached Sources (``IPositionBalanceSource``,
    ``IConvertToAssetsSource``, ``IBlockResolver``) injected at construction.
    Duplication with later reads inside ``_legs_at`` is cheap because the
    underlying calls are ``@cached`` end-to-end.
    """

    def __init__(
        self,
        *,
        position_balance_source: Any = None,
        convert_to_assets_source: Any = None,
        block_resolver: Any = None,
    ) -> None:
        # Process-local cache: one bulk Dune query per (chain, holder) covers
        # all per-day ``shares_of(block_X)`` lookups for the whole settlement
        # period. Stored as (list_of_block_cum_pairs, max_pin_block_loaded).
        # If a later call needs a higher block we re-fetch to extend coverage.
        self._holder_history: dict[tuple[str, str], tuple[list[tuple[int, int]], int]] = {}
        self._pool_history:   dict[str, tuple[list[tuple[int, int]], int]] = {}
        # Per-(chain, psm3) pool-reserve history, one entry per token. Each
        # entry maps ``token_address_hex → (list[(block_number, cum_raw)], pin_block)``.
        # ``cum_raw`` is the **raw uint256 token-units** balance (already
        # in the same units that ``IPositionBalanceSource.balance_at``
        # returns); no rescaling happens in ``pool_reserve_at``. The SQL
        # ``cum_balance_raw`` column already casts ``value`` as
        # ``DECIMAL(38,0)`` so the Python conversion is just ``int(...)``.
        self._reserves_history: dict[tuple[str, str], dict[str, tuple[list[tuple[int, int]], int]]] = {}
        # Sources for pool-reserve + Ethereum sUSDS pps reads inside
        # ``convert_to_asset_value``. Lazily resolved from the registry if
        # not passed (matches the convention in other Sources).
        self._pos_bal = position_balance_source
        self._c2a = convert_to_assets_source
        self._block_resolver = block_resolver

    # ----------------------------------------------------------------------
    # Preload — orchestrator hook for bulk-loading the full settlement period
    # in one Dune query per (chain, holder) instead of one per day.
    # ----------------------------------------------------------------------

    def preload(
        self, chain: str, holder: bytes, *, pin_block: int,
        psm3: bytes | None = None,
    ) -> None:
        """Warm the in-process cache for (chain, holder) up to ``pin_block``.

        Callers (the orchestrator) invoke this once with the period's EoM
        block before the per-day ``_legs_at`` loop. Without it, the first
        day's ``shares_of`` call sets the cached pin_block to the first
        day's block, and each subsequent day's higher block forces a
        re-fetch — costing one Dune query per day instead of one per cell.

        If ``psm3`` is given, also bulk-loads pool reserves (USDC/USDS/sUSDS)
        for that PSM3 contract — enables ``pool_reserve_at`` to bypass RPC.

        **Known limitation — partial-failure state is not transactional.**
        The three underlying loaders (holder / pool / reserves) fire
        sequentially. A mid-call failure on, say, the pool loader leaves
        ``_holder_history`` populated but ``_pool_history`` empty. The
        orchestrator catches the exception once at this boundary and logs
        once, then per-day reads continue: ``shares_of`` will hit the
        cached holder history (fast), but ``convert_to_asset_value`` will
        re-attempt ``_load_pool_history`` for every day — and if Dune is
        still degraded, every day fails again silently. Behaviour is
        correct (RPC fallback works downstream), but log noise scales with
        the period length. Tracked for a follow-up: either cache empty
        sentinels per-loader so per-day reads short-circuit, or make
        ``preload`` transactional (rollback all three caches on any
        single-loader failure).
        """
        self._load_holder_history(chain, holder, pin_block=pin_block)
        self._load_pool_history(chain, pin_block=pin_block)
        if psm3 is not None:
            self._load_reserves_history(chain, psm3, pin_block=pin_block)

    # ----------------------------------------------------------------------
    # Dune-backed pool-reserve lookup — used by ``_legs_at`` when present
    # to skip the per-day ``balanceOf`` RPC calls that fail on Arbitrum +
    # Unichain. Returns ``None`` if the chain/psm3/token isn't preloaded
    # (caller should fall back to RPC ``pos_bal.balance_at``).
    # ----------------------------------------------------------------------

    def pool_reserve_at(
        self, chain: str, token: bytes, psm3: bytes, block: int, *, decimals: int,
    ) -> int | None:
        """Cumulative ``token`` balance at the PSM3 contract at ``block``,
        in raw uint256 units.

        Returns ``None`` to signal "no Dune-backed answer, fall back to
        RPC" in any of three cases:
          * Reserves haven't been preloaded for ``(chain, psm3)`` at all.
          * The chain isn't in ``_RESERVES_SQL_BY_CHAIN`` (no per-chain
            ERC-20 spell), so an empty sentinel was cached.
          * The Dune result didn't include any rows for ``token``. Note
            this is **not** safe to interpret as "balance is 0": the
            ``start_month`` partition filter in the SQL excludes pre-floor
            transfers, so a token whose entire history predates the filter
            would also have an empty result. Returning ``None`` lets the
            caller fall back to RPC ``balanceOf`` for a definitive read.

        Returns ``int`` (0 included) only when there IS an event-driven
        cumulative trail for the token, in which case ``bisect_right``
        finds the last cum balance ≤ ``block``. Empty trail (the rare
        case where the SQL returned exactly one zeroing event before
        ``block``) is also reported as ``int``.

        ``decimals`` is accepted for API symmetry with ``balance_at``
        callers but unused — the loaded history is already in raw uint256
        units (no rescaling needed).
        """
        del decimals  # noqa: F841 — unused, kept for API parity
        key = (chain, "0x" + bytes(psm3).hex())
        per_token = self._reserves_history.get(key)
        if per_token is None or not per_token:
            # No preload yet, OR the empty-sentinel cached for an
            # unsupported chain. Caller falls back to RPC.
            return None
        token_hex = "0x" + bytes(token).hex()
        history_entry = per_token.get(token_hex)
        if history_entry is None:
            # The reserves were loaded but Dune returned no rows for this
            # token. Could be "no activity since start_month" OR "all
            # activity predates start_month" — we can't tell from here.
            # Return None so the caller's RPC fallback fires and reads
            # the actual on-chain balance. Worst-case cost: one extra
            # ``balanceOf`` per (chain, token) per cell.
            return None
        history, _ = history_entry
        if not history:
            return 0
        blocks = [b for b, _ in history]
        idx = bisect.bisect_right(blocks, block) - 1
        if idx < 0:
            return 0
        return history[idx][1]

    # ----------------------------------------------------------------------
    # IPsm3Source interface
    # ----------------------------------------------------------------------

    def shares_of(self, chain: str, psm3: bytes, holder: bytes, block: int) -> int:
        from ...extract.dune import DuneError
        try:
            history = self._load_holder_history(chain, holder, pin_block=block)
            return _bisect_cum_at_or_before(history, block)
        except DuneError:
            # Dune outage / 402 quota: fall back to a direct RPC read of
            # ``shares(holder)`` at this block. The PSM3 contract exposes a
            # public ``shares(address)(uint256)`` getter — fully equivalent
            # to the event-reconstruction path for a point-in-time snapshot
            # (the event path is only preferred when callers need the
            # *timeseries*; for a single-block read RPC is exact).
            from ...domain import Address, Chain
            from ...extract import rpc as _rpc
            return _rpc.psm3_shares(
                Chain(chain), Address(psm3), Address(holder), block,
            )

    def convert_to_asset_value(self, chain: str, psm3: bytes, num_shares: int, block: int) -> int:
        """USDS-equivalent value of ``num_shares`` at ``block``.

        On-chain definition: ``num_shares × totalAssets(block) / totalShares(block)``,
        where ``totalAssets`` = USDC + USDS + sUSDS × eth_sUSDS_pps.

        ``totalShares`` from Dune event reconstruction (deterministic).
        Per-asset reserves from ``IPositionBalanceSource`` (cached
        ``balanceOf`` calls — fast on the second hit from inside
        ``_legs_at``). The Ethereum sUSDS pps from
        ``IConvertToAssetsSource`` (cached ``convertToAssets`` on
        Ethereum, the chain that always works).

        Note (math redundancy with ``_legs_at``): the caller in
        ``compute_monthly_pnl._legs_at`` reads the same three reserves
        independently after this returns, computes ``pool_total`` in USDS
        units, and derives ``spark_share = spark_claim / pool_total``.
        Algebraically that reduces to ``shares / total_shares`` — the
        ``total_assets`` factor cancels. Both reads of the same reserves
        hit ``@cached`` so the second is free, but the redundancy is
        structurally fragile. The cleaner refactor is to expose a
        ``total_shares_at(block)`` method on ``IPsm3Source`` and have
        ``_legs_at`` compute ``spark_share`` directly. Tracked as a
        follow-up; today's behaviour is correct via cache reuse.
        """
        if num_shares == 0:
            return 0
        from ...extract.dune import DuneError
        try:
            pool_total = _bisect_cum_at_or_before(
                self._load_pool_history(chain, pin_block=block), block,
            )
        except DuneError:
            # Dune outage / 402 quota: fall back to RPC
            # ``convertToAssetValue(num_shares)`` direct call. Skips the
            # leg-by-leg decomposition — the caller in ``_legs_at`` still
            # decomposes via reserve reads (which only need ``pos_bal``,
            # not Dune), but the *value* we return is exact. The
            # decomposition-via-reserves step computes
            # ``spark_share × leg_reserve`` per leg; the spark_share itself
            # is derived from this returned value, so the legs end up
            # consistent.
            from ...domain import Address, Chain as _Chain
            from ...extract import rpc as _rpc
            return _rpc.psm3_convert_to_asset_value(
                _Chain(chain), Address(psm3), num_shares, block,
            )
        if pool_total <= 0:
            return 0

        # Lazy registry imports — keeps this module importable from tests
        # that haven't wired up a registry.
        if self._pos_bal is None:
            from ..registry import get_position_balance_source
            self._pos_bal = get_position_balance_source()
        if self._c2a is None:
            from ..registry import get_convert_to_assets_source
            self._c2a = get_convert_to_assets_source()
        if self._block_resolver is None:
            from ..registry import get_block_resolver
            self._block_resolver = get_block_resolver()

        chain_enum = Chain(chain)
        leg_tokens = PSM3_LEG_TOKENS[chain_enum]

        # Prefer Dune-backed reserves (preloaded) over RPC. Falls back to
        # ``pos_bal.balance_at`` only if the caller hasn't called preload().
        def _reserve(token_addr: bytes, dec: int) -> int:
            r = self.pool_reserve_at(chain, token_addr, psm3, block, decimals=dec)
            if r is not None:
                return r
            return self._pos_bal.balance_at(chain, token_addr, psm3, block)

        usdc_raw  = _reserve(leg_tokens["USDC"].address.value,  6)
        usds_raw  = _reserve(leg_tokens["USDS"].address.value,  18)
        susds_raw = _reserve(leg_tokens["sUSDS"].address.value, 18)

        # sUSDS pps anchored on Ethereum — translate this L2 block to the
        # matching Eth block by going through ``block_to_date`` (resolves to
        # the L2 day's EoD timestamp, then to the Eth block at that EoD).
        l2_date = self._block_resolver.block_to_date(chain, block)
        eod = datetime.combine(l2_date, time.max, tzinfo=timezone.utc)
        eth_block = self._block_resolver.block_at_or_before(Chain.ETHEREUM.value, eod)
        susds_pps_raw = self._c2a.convert_to_assets(
            chain=Chain.ETHEREUM.value,
            vault=sUSDS_ETHEREUM.address.value,
            shares=10**18, block=eth_block,
        )

        scale_18 = Decimal(10**18)
        scale_6  = Decimal(10**6)
        usdc_usds_eq  = Decimal(usdc_raw)  * scale_18 / scale_6  # 6-dec → 18-dec
        usds_usds_eq  = Decimal(usds_raw)
        susds_usds_eq = Decimal(susds_raw) * Decimal(susds_pps_raw) / scale_18
        total_assets  = usdc_usds_eq + usds_usds_eq + susds_usds_eq
        return int(Decimal(num_shares) * total_assets / Decimal(pool_total))

    def susds_pps(self, chain: str, block: int) -> int:
        """USDS value of 1e18 sUSDS at ``block`` (18-decimal raw integer).

        Fetches the Ethereum sUSDS ``convertToAssets(1e18)`` at the Ethereum
        block matching ``block``'s date — the same rate already computed
        inside ``convert_to_asset_value``. Cached end-to-end via ``@cached``
        on the underlying RPC call."""
        if self._c2a is None:
            from ..registry import get_convert_to_assets_source
            self._c2a = get_convert_to_assets_source()
        if self._block_resolver is None:
            from ..registry import get_block_resolver
            self._block_resolver = get_block_resolver()
        from datetime import datetime, time, timezone
        l2_date = self._block_resolver.block_to_date(chain, block)
        eod = datetime.combine(l2_date, time.max, tzinfo=timezone.utc)
        eth_block = self._block_resolver.block_at_or_before(Chain.ETHEREUM.value, eod)
        return self._c2a.convert_to_assets(
            chain=Chain.ETHEREUM.value,
            vault=sUSDS_ETHEREUM.address.value,
            shares=10**18,
            block=eth_block,
        )

    # ----------------------------------------------------------------------
    # Loaders
    # ----------------------------------------------------------------------

    def _load_holder_history(
        self, chain: str, holder: bytes, *, pin_block: int,
    ) -> list[tuple[int, int]]:
        holder_hex = "0x" + bytes(holder).hex()
        key = (chain, holder_hex)
        cached = self._holder_history.get(key)
        if cached is not None and cached[1] >= pin_block:
            return cached[0]
        df = execute_query(
            _QUERIES_DIR / "psm3_shares_history.sql",
            params={"chain": chain, "holder": holder},
            pin_block=pin_block,
        )
        history = _df_to_block_cum(df, value_col="cum_shares")
        self._holder_history[key] = (history, pin_block)
        return history

    def _load_pool_history(
        self, chain: str, *, pin_block: int,
    ) -> list[tuple[int, int]]:
        cached = self._pool_history.get(chain)
        if cached is not None and cached[1] >= pin_block:
            return cached[0]
        df = execute_query(
            _QUERIES_DIR / "psm3_total_shares_history.sql",
            params={"chain": chain},
            pin_block=pin_block,
        )
        history = _df_to_block_cum(df, value_col="cum_total_shares")
        self._pool_history[chain] = (history, pin_block)
        return history

    # Per-chain SQL files. Dune doesn't support ``{{param}}`` substitution in
    # FROM identifiers, so we ship one query per chain pointing at that
    # chain's ``erc20_<chain>.evt_transfer`` spell. (The multi-chain
    # ``tokens.transfers`` 402s on the community-tier plan.)
    _RESERVES_SQL_BY_CHAIN = {
        "arbitrum": "psm3_reserves_history_arbitrum.sql",
        "base":     "psm3_reserves_history_base.sql",
        "optimism": "psm3_reserves_history_optimism.sql",
        "unichain": "psm3_reserves_history_unichain.sql",
    }

    def _load_reserves_history(
        self, chain: str, psm3: bytes, *, pin_block: int,
    ) -> dict[str, tuple[list[tuple[int, int]], int]]:
        """Bulk-load USDC/USDS/sUSDS daily-cum balances at the PSM3 contract.

        One Dune query per (chain, psm3) covers the entire period. Result is
        partitioned per token in the in-process cache. Balances are stored
        as raw uint256 token-units so the bisect lookup returns the same
        integer ``IPositionBalanceSource.balance_at`` would.
        """
        key = (chain, "0x" + bytes(psm3).hex())
        cached = self._reserves_history.get(key)
        if cached is not None:
            if all(pin_b >= pin_block for _, pin_b in cached.values()):
                return cached
        sql_name = self._RESERVES_SQL_BY_CHAIN.get(chain)
        if sql_name is None:
            # Unsupported chain (no per-chain ERC-20 spell). Caller falls
            # back to ``pos_bal.balance_at`` via RPC.
            self._reserves_history[key] = {}
            return {}
        chain_enum = Chain(chain)
        leg_tokens = PSM3_LEG_TOKENS[chain_enum]
        # ``evt_block_date >= start_month`` partition pushdown — keeps the
        # erc20_<chain>.evt_transfer scan small. 2025-08-01 floors comfortably
        # before any Spark PSM3 deployment (the earliest, on Base, was Sep 2025).
        df = execute_query(
            _QUERIES_DIR / sql_name,
            params={
                "psm3":  psm3,
                "usdc":  leg_tokens["USDC"].address.value,
                "usds":  leg_tokens["USDS"].address.value,
                "susds": leg_tokens["sUSDS"].address.value,
                "start_month": "2025-08-01",
            },
            pin_block=pin_block,
        )
        per_token: dict[str, tuple[list[tuple[int, int]], int]] = {}
        if df is not None and not df.empty:
            for token_raw, group in df.groupby("token"):
                token_bytes = token_raw if isinstance(token_raw, (bytes, bytearray)) else bytes.fromhex(
                    str(token_raw).removeprefix("0x")
                )
                token_hex = "0x" + bytes(token_bytes).hex()
                history: list[tuple[int, int]] = []
                for _, row in group.iterrows():
                    bal_raw = row["cum_balance_raw"]
                    bal_int = int(bal_raw) if isinstance(bal_raw, (int, Decimal)) else int(Decimal(str(bal_raw)))
                    history.append((int(row["block_number"]), bal_int))
                history.sort(key=lambda x: x[0])
                per_token[token_hex] = (history, pin_block)
        self._reserves_history[key] = per_token
        return per_token


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def _df_to_block_cum(df: Any, *, value_col: str) -> list[tuple[int, int]]:
    """Convert a Dune result DataFrame with columns ``[block_number, …,
    value_col]`` into a sorted list of ``(block, cum)`` tuples for bisect.

    Empty input → empty list (every ``shares_of(block)`` will return 0,
    matching the on-chain "never deposited" state).
    """
    if df is None or df.empty:
        return []
    out: list[tuple[int, int]] = []
    for _, row in df.iterrows():
        block = int(row["block_number"])
        # Dune returns numeric columns as ``Decimal`` already; coerce
        # defensively in case of pickle round-trips that flatten to str.
        val_raw = row[value_col]
        val = int(val_raw) if isinstance(val_raw, (int, Decimal)) else int(Decimal(str(val_raw)))
        out.append((block, val))
    # SQL ORDER BY guarantees sort, but enforce defensively.
    out.sort(key=lambda x: x[0])
    return out


def _bisect_cum_at_or_before(history: list[tuple[int, int]], target_block: int) -> int:
    """Return the cumulative value at the latest event whose block ≤
    ``target_block``. Returns 0 if no event has happened yet."""
    if not history:
        return 0
    # bisect_right on the keys; the matching index is its result − 1.
    blocks = [b for b, _ in history]
    idx = bisect.bisect_right(blocks, target_block) - 1
    if idx < 0:
        return 0
    return history[idx][1]
