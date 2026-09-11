"""Decoded Smart Burn Engine facts: writers (cron) and readers (API).

Rows are on-chain facts keyed by ``(block_number, log_index)``; a re-run is
``ON CONFLICT DO NOTHING`` and reports how many rows were new. ``first_seen_run``
records which run introduced the row — the audit trail for "when did we first
know this".
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..compute.tmf_history import HistoryDataset, build_history_dataset
from ..domain.tmf import SbeKick, SbeParamChange, SkyBurn
from .runs import latest_run


class IncompleteRunError(RuntimeError):
    """The latest ok run does not carry the block range its rows are bounded by,
    so no document can honestly state a range. Written by our own cron, so this
    means the summary shape changed (or the row was hand-edited)."""

__all__ = [
    "IncompleteRunError",
    "history_document",
    "load_burns",
    "load_kicks",
    "load_param_changes",
    "upsert_burns",
    "upsert_kicks",
    "upsert_param_changes",
]

# ── writers ──────────────────────────────────────────────────────────────────


def upsert_kicks(conn: Any, kicks: list[SbeKick], run_id: int) -> int:
    """Rows new to the store. Does NOT commit: the caller commits all three
    upserts and ``finish_run`` together, so a run that dies midway leaves no
    durable rows for a later document to over-report against."""
    if not kicks:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO sbe_kicks (block_number, log_index, block_time, tx_hash, usds_total,
                usds_buyback, usds_to_stakers, sky_bought, splitter_burn, splitter_hop,
                farm, flapper, first_seen_run)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (block_number, log_index) DO NOTHING
            """,
            [(k.block, k.log_index, k.ts, k.tx, k.tot, k.lot, k.pay, k.bought, k.burn, k.hop,
              k.farm, k.flapper, run_id) for k in kicks],
            returning=False,
        )
        n = int(cur.rowcount)
    return max(n, 0)


def upsert_burns(conn: Any, burns: list[SkyBurn], run_id: int) -> int:
    if not burns:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO sky_burns (block_number, log_index, block_time, tx_hash, sender, sink,
                sky_amount, protocol, first_seen_run)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (block_number, log_index) DO NOTHING
            """,
            [(b.block, b.log_index, b.ts, b.tx, b.sender, b.sink, b.amount, b.protocol, run_id)
             for b in burns],
            returning=False,
        )
        n = int(cur.rowcount)
    return max(n, 0)


def upsert_param_changes(conn: Any, changes: list[SbeParamChange], run_id: int) -> int:
    if not changes:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO sbe_param_changes (block_number, log_index, block_time, tx_hash,
                contract, address, what, value, first_seen_run)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (block_number, log_index) DO NOTHING
            """,
            [(c.block, c.log_index, c.ts, c.tx, c.contract, c.address, c.what, str(c.value), run_id)
             for c in changes],
            returning=False,
        )
        n = int(cur.rowcount)
    return max(n, 0)


# ── readers ──────────────────────────────────────────────────────────────────


def _window(
    from_ts: int | None, to_ts: int | None, to_block: int | None = None
) -> tuple[str, list[Any]]:
    """``to_block`` bounds the rows to a run's pin, so a document can never
    report totals outside the block range it states (a ``--to-block`` backfill
    becoming the latest run is the case that needs this)."""
    clauses: list[str] = []
    params: list[Any] = []
    if from_ts is not None:
        clauses.append("block_time >= %s")
        params.append(from_ts)
    if to_ts is not None:
        clauses.append("block_time <= %s")
        params.append(to_ts)
    if to_block is not None:
        clauses.append("block_number <= %s")
        params.append(to_block)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def load_kicks(conn: Any, *, from_ts: int | None = None, to_ts: int | None = None,
               to_block: int | None = None, limit: int | None = None) -> list[SbeKick]:
    """Oldest first. ``limit`` keeps the LAST n rows in the window."""
    where, params = _window(from_ts, to_ts, to_block)
    lim = f" LIMIT {int(limit)}" if limit else ""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT block_number, log_index, block_time, tx_hash, usds_total, usds_buyback, "
            "usds_to_stakers, sky_bought, splitter_burn, splitter_hop, farm, flapper "
            f"FROM sbe_kicks{where} ORDER BY block_number DESC, log_index DESC{lim}",
            params,
        )
        rows = cur.fetchall()
    return [SbeKick(block=r[0], log_index=r[1], ts=r[2], tx=r[3], tot=Decimal(r[4]),
                    lot=Decimal(r[5]), pay=Decimal(r[6]), bought=Decimal(r[7]),
                    burn=Decimal(r[8]), hop=r[9], farm=r[10], flapper=r[11])
            for r in reversed(rows)]


def load_burns(conn: Any, *, from_ts: int | None = None, to_ts: int | None = None,
               to_block: int | None = None, limit: int | None = None) -> list[SkyBurn]:
    """Oldest first. ``limit`` keeps the LAST n — the table is externally
    growable (anyone may send SKY to the dead address), so callers that serve
    it over HTTP must bound it."""
    where, params = _window(from_ts, to_ts, to_block)
    lim = f" LIMIT {int(limit)}" if limit else ""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT block_number, log_index, block_time, tx_hash, sender, sink, sky_amount, protocol "
            f"FROM sky_burns{where} ORDER BY block_number DESC, log_index DESC{lim}",
            params,
        )
        rows = cur.fetchall()
    return [SkyBurn(block=r[0], log_index=r[1], ts=r[2], tx=r[3], sender=r[4], sink=r[5],
                    amount=Decimal(r[6]), protocol=bool(r[7])) for r in reversed(rows)]


def load_param_changes(conn: Any, *, to_block: int | None = None,
                       limit: int | None = None) -> list[SbeParamChange]:
    """Oldest first. ``limit`` keeps the LAST n."""
    where, params = _window(None, None, to_block)
    lim = f" LIMIT {int(limit)}" if limit else ""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT block_number, log_index, block_time, tx_hash, contract, address, what, value "
            f"FROM sbe_param_changes{where} ORDER BY block_number DESC, log_index DESC{lim}",
            params,
        )
        rows = list(reversed(cur.fetchall()))
    out = []
    for r in rows:
        v: Decimal | str = r[7] if str(r[7]).startswith("0x") else Decimal(r[7])
        out.append(SbeParamChange(block=r[0], log_index=r[1], ts=r[2], tx=r[3], contract=r[4],
                                  what=r[6], value=v, address=r[5]))
    return out


def history_document(conn: Any, *, contracts: dict[str, str], notes: list[str]) -> dict[str, Any] | None:
    """The ``sbe_history.json`` document (schema 1.1.0) rebuilt from the tables,
    stamped with the latest ok ``tmf_history`` run's pin. None when no run yet.

    The rows are bounded by that run's ``pin_block``, so ``source.to_block`` /
    ``to_ts`` always describe exactly what the totals cover. ``generated_at`` is
    the run's ``finished_at``, which makes the document byte-stable per run —
    the ETag only moves when the data does.
    """
    run = latest_run(conn, "tmf_history")
    if run is None:
        return None
    summary = run.get("summary") or {}
    pin = run.get("pin_block")
    missing = [k for k in ("from_block", "to_ts") if summary.get(k) is None]
    if pin is None or missing:
        raise IncompleteRunError(
            f"run {run['run_id']} is marked ok but lacks "
            + ", ".join(["pin_block"] * (pin is None) + missing)
            + " — refusing to publish a document whose stated block range is a guess"
        )
    pin = int(pin)
    ds = HistoryDataset(
        from_block=int(summary["from_block"]),
        to_block=pin,
        to_ts=int(summary["to_ts"]),
        kicks=load_kicks(conn, to_block=pin),
        burns=load_burns(conn, to_block=pin),
        param_changes=load_param_changes(conn, to_block=pin),
        contracts=contracts, notes=notes,
    )
    doc = build_history_dataset(ds, generated_at=run["finished_at"])
    doc["run"] = {"run_id": run["run_id"], "finished_at": run["finished_at"],
                  "settle_version": run["settle_version"]}
    return doc
