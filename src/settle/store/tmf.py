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

__all__ = [
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
    conn.commit()
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
    conn.commit()
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
    conn.commit()
    return max(n, 0)


# ── readers ──────────────────────────────────────────────────────────────────


def _window(from_ts: int | None, to_ts: int | None) -> tuple[str, list[Any]]:
    clauses, params = [], []
    if from_ts is not None:
        clauses.append("block_time >= %s")
        params.append(from_ts)
    if to_ts is not None:
        clauses.append("block_time <= %s")
        params.append(to_ts)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def load_kicks(conn: Any, *, from_ts: int | None = None, to_ts: int | None = None,
               limit: int | None = None) -> list[SbeKick]:
    where, params = _window(from_ts, to_ts)
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


def load_burns(conn: Any, *, from_ts: int | None = None, to_ts: int | None = None) -> list[SkyBurn]:
    where, params = _window(from_ts, to_ts)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT block_number, log_index, block_time, tx_hash, sender, sink, sky_amount, protocol "
            f"FROM sky_burns{where} ORDER BY block_number, log_index",
            params,
        )
        rows = cur.fetchall()
    return [SkyBurn(block=r[0], log_index=r[1], ts=r[2], tx=r[3], sender=r[4], sink=r[5],
                    amount=Decimal(r[6]), protocol=bool(r[7])) for r in rows]


def load_param_changes(conn: Any) -> list[SbeParamChange]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT block_number, log_index, block_time, tx_hash, contract, address, what, value "
            "FROM sbe_param_changes ORDER BY block_number, log_index"
        )
        rows = cur.fetchall()
    out = []
    for r in rows:
        v: Decimal | str = r[7] if str(r[7]).startswith("0x") else Decimal(r[7])
        out.append(SbeParamChange(block=r[0], log_index=r[1], ts=r[2], tx=r[3], contract=r[4],
                                  what=r[6], value=v, address=r[5]))
    return out


def history_document(conn: Any, *, contracts: dict[str, str], notes: list[str]) -> dict[str, Any] | None:
    """The ``sbe_history.json`` document (schema 1.1.0) rebuilt from the tables,
    stamped with the latest ok ``tmf_history`` run's pin. None when no run yet."""
    run = latest_run(conn, "tmf_history")
    if run is None:
        return None
    summary = run.get("summary") or {}
    ds = HistoryDataset(
        from_block=int(summary.get("from_block", 0)),
        to_block=int(run["pin_block"] or 0),
        to_ts=int(summary.get("to_ts", 0)),
        kicks=load_kicks(conn), burns=load_burns(conn), param_changes=load_param_changes(conn),
        contracts=contracts, notes=notes,
    )
    doc = build_history_dataset(ds)
    doc["run"] = {"run_id": run["run_id"], "finished_at": run["finished_at"],
                  "settle_version": run["settle_version"]}
    return doc
