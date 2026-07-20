"""HyperSync-direct ``IBalanceSource`` — ERC-20 Transfer reconstruction.

Drop-in replacement for ``DuneBalanceSource``: reconstructs the same three
timeseries from raw ``Transfer`` logs fetched via HyperSync (through the
reorg-safe ``hypersync_store``), instead of Dune's ``tokens.transfers``.

Two differences from the Dune path, both handled here:
  * Dune's ``tokens.transfers.amount`` is decimal-adjusted; raw logs carry the
    integer ``value``. We divide by ``10**decimals`` (read once via RPC,
    injectable) — summing raw ints first, dividing once at the end, so results
    match Dune's DECIMAL exactly.
  * Dune prunes by ``block_date >= start``; HyperSync needs a block floor, so we
    resolve ``start`` → a block (injectable, RPC by default) as a safe lower
    bound and re-apply the ``block_date >= start`` filter in Python.

Output frames are identical to ``DuneBalanceSource`` (same columns/dtypes).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timezone
from decimal import Decimal, localcontext
from typing import Any

import pandas as pd

from ...extract import hypersync_store

# Transfer topic0 + the 20↔32-byte topic codec live in extract/transfer_logs
# (single implementation; the strict 20-byte check there raises on a wrong-
# width address instead of silently padding into a topic that matches nothing).
from ...extract.transfer_logs import TRANSFER_TOPIC0 as _TRANSFER_T0


def _addr_topic(addr: bytes) -> str:
    """20-byte address → 32-byte topic hex (left-zero-padded), lower-case."""
    from ...extract.transfer_logs import _to_topic_hex
    return _to_topic_hex(bytes(addr))


def _topic_to_addr(topic: str) -> bytes:
    """32-byte topic hex → 20-byte address bytes (low 20 bytes)."""
    h = topic.removeprefix("0x").rjust(64, "0")
    return bytes.fromhex(h[-40:])


def _default_start_block(chain: str, start: date) -> int:
    # Resolve the start-of-range block floor off HyperSync (not the archive RPC):
    # it's the same result as rpc.find_block_at_or_before but keeps this path off
    # the archive RPC and working on lagging-RPC chains (e.g. monad) — the whole
    # point of the migration. Only a safe lower bound anyway (the caller re-applies
    # the ``block_date >= start`` filter in Python), so exactness is not required.
    from ...extract import hypersync
    midnight = datetime.combine(start, time.min, tzinfo=timezone.utc)
    return hypersync.find_block_at_or_before(chain, int(midnight.timestamp()))


def _default_decimals(chain: str, token: bytes, block: int) -> int:
    from ...domain.primes import Address, Chain
    from ...extract import rpc
    return rpc.decimals_of(Chain(chain), Address.from_str("0x" + bytes(token).hex()), block)


class HyperSyncBalanceSource:
    """Implements ``IBalanceSource`` via HyperSync Transfer-log queries.

    ``fetch_logs`` / ``resolve_start_block`` / ``decimals_of`` are injectable for
    tests; they default to the reorg-safe store + RPC.
    """

    def __init__(
        self,
        *,
        fetch_logs: Callable[..., list[Any]] = hypersync_store.fetch_logs,
        resolve_start_block: Callable[[str, date], int] = _default_start_block,
        decimals_of: Callable[[str, bytes, int], int] = _default_decimals,
    ) -> None:
        self._fetch = fetch_logs
        self._resolve_start = resolve_start_block
        self._decimals_of = decimals_of

    # -- IBalanceSource -----------------------------------------------------

    def cumulative_balance_timeseries(
        self, chain: str, token: bytes, holder: bytes, start: date, pin_block: int,
        min_transfer_amount: Decimal = Decimal(0),
    ) -> pd.DataFrame:
        cols = ["block_date", "daily_net", "cum_balance"]
        scale = 10 ** self._decimals_of(chain, token, pin_block)
        min_raw = min_transfer_amount * Decimal(scale)
        h = _addr_topic(holder)
        daily: dict[date, int] = {}
        for x in self._transfers_touching(chain, token, holder, start, pin_block):
            if Decimal(x["value"]) < min_raw:          # Dune: amount >= min_transfer_amount
                continue
            net = (x["value"] if x["to"] == h else 0) - (x["value"] if x["from"] == h else 0)
            daily[x["date"]] = daily.get(x["date"], 0) + net
        if not daily:
            return pd.DataFrame(columns=cols)
        return _to_cumulative_frame(daily, scale, "daily_net", "cum_balance")

    def directed_inflow_timeseries(
        self, chain: str, token: bytes, from_addr: bytes, to_addr: bytes,
        start: date, pin_block: int,
    ) -> pd.DataFrame:
        cols = ["block_date", "daily_inflow", "cum_inflow"]
        scale = 10 ** self._decimals_of(chain, token, pin_block)
        sel = [{
            "address": ["0x" + bytes(token).hex()],
            "topics": [[_TRANSFER_T0], [_addr_topic(from_addr)], [_addr_topic(to_addr)]],
        }]
        daily: dict[date, int] = {}
        for x in self._fetch_range(chain, sel, start, pin_block):
            daily[x["date"]] = daily.get(x["date"], 0) + x["value"]
        if not daily:
            return pd.DataFrame(columns=cols)
        return _to_cumulative_frame(daily, scale, "daily_inflow", "cum_inflow")

    def inflow_by_counterparty(
        self, chain: str, token: bytes, holder: bytes, start: date, pin_block: int,
    ) -> pd.DataFrame:
        cols = ["block_date", "counterparty", "signed_amount"]
        scale = 10 ** self._decimals_of(chain, token, pin_block)
        h = _addr_topic(holder)
        agg: dict[tuple[date, str], int] = {}      # (block_date, counterparty_topic) → signed raw
        for x in self._transfers_touching(chain, token, holder, start, pin_block):
            if x["from"] == h and x["to"] == h:
                # Self-transfer (holder→holder): no external counterparty and
                # nets to zero. ``_transfers_touching`` dedups it to one row,
                # so the ``x["to"] == h`` branch below would otherwise record a
                # spurious one-sided +value inflow attributed to the holder
                # itself, with no offsetting outflow leg. Skip it — matching
                # ``cumulative_balance_timeseries``, which nets it to 0.
                continue
            if x["to"] == h:                       # inflow: counterparty = from
                key = (x["date"], x["from"])
                agg[key] = agg.get(key, 0) + x["value"]
            else:                                  # outflow: counterparty = to
                key = (x["date"], x["to"])
                agg[key] = agg.get(key, 0) - x["value"]
        if not agg:
            return pd.DataFrame(columns=cols)
        with localcontext() as ctx:
            ctx.prec = 60
            recs = [
                {"block_date": d, "counterparty": _topic_to_addr(cp),
                 "signed_amount": Decimal(raw) / Decimal(scale)}
                for (d, cp), raw in agg.items()
            ]
        return pd.DataFrame(recs).sort_values(
            ["block_date", "counterparty"]
        ).reset_index(drop=True)[cols]

    # -- helpers ------------------------------------------------------------

    def _transfers_touching(
        self, chain: str, token: bytes, holder: bytes, start: date, pin_block: int
    ) -> list[dict[str, Any]]:
        """All Transfer logs of ``token`` where ``holder`` is from OR to."""
        ht = _addr_topic(holder)
        tok = "0x" + bytes(token).hex()
        sel = [
            {"address": [tok], "topics": [[_TRANSFER_T0], [ht]]},        # from == holder
            {"address": [tok], "topics": [[_TRANSFER_T0], [], [ht]]},    # to == holder
        ]
        return self._fetch_range(chain, sel, start, pin_block)

    def _fetch_range(
        self, chain: str, selections: list[dict[str, Any]], start: date, pin_block: int
    ) -> list[dict[str, Any]]:
        from_block = self._resolve_start(chain, start)
        rows = self._fetch(chain, selections, from_block, pin_block)
        seen: set[tuple[int, int]] = set()
        out: list[dict[str, Any]] = []
        for r in rows:
            k = (r.block_number, r.log_index)
            if k in seen:                          # self-transfer matches both selections
                continue
            seen.add(k)
            d = datetime.fromtimestamp(r.block_time, tz=timezone.utc).date()
            if d < start:                          # match Dune's block_date >= start
                continue
            out.append({
                "date": d, "from": r.topic1, "to": r.topic2,
                "value": int(r.data, 16),
            })
        return out


def _to_cumulative_frame(
    daily_raw: dict[date, int], scale: int, daily_col: str, cum_col: str
) -> pd.DataFrame:
    """Exact-int daily map → [block_date, <daily_col>, <cum_col>] Decimal frame."""
    with localcontext() as ctx:
        ctx.prec = 60
        out = []
        cum = 0
        for d, v in sorted(daily_raw.items()):
            cum += v
            out.append({
                "block_date": d,
                daily_col: Decimal(v) / Decimal(scale),
                cum_col: Decimal(cum) / Decimal(scale),
            })
    return pd.DataFrame(out)
