"""One-time backfill: lift cached raw data from the local pickle cache into Postgres.

The pickle cache (`~/.cache/msc-settle/` by default, or `$SETTLE_CACHE_DIR`)
fills naturally as the pipeline runs. This script ports an existing cache
into the Postgres ``raw_data`` table so other agents and CI can read from a
shared source instead of each having to re-fetch every Dune query and RPC
call from upstream.

Idempotent: ``ON CONFLICT (source, args_hash) DO NOTHING`` — re-running is
safe and any rows already in Postgres are left untouched. Historical raw
data is immutable.

**Args column caveat.** Cached pickle filenames store only ``source`` +
``SHA256(args)``, not the original args themselves. Backfilled rows set
``args = {"_backfilled_from_pickle": true, "_source_id": ..., "_args_hash":
...}`` as a placeholder. ``(source, args_hash, payload)`` are correct, so
the read-through cache works against backfilled rows just like fresh ones;
only the human-readable ``args`` JSONB column is a placeholder for
backfilled rows. Future fetches (via the read-through cache in
``cache.py``) write the real args column.

Run with::

    set -a; source .env; set +a
    PYTHONPATH=src python3 scripts/backfill_cache_to_postgres.py

Override the cache directory::

    SETTLE_CACHE_DIR=/path/to/other/cache python3 scripts/backfill_cache_to_postgres.py
"""

from __future__ import annotations

import os
import pickle
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from settle.extract import postgres_store  # noqa: E402

# Cache filename format from ``cache.py``: ``<source_id>_<64-char hex>.pkl``.
_FILENAME_RE = re.compile(r"^(?P<source>.+)_(?P<hash>[0-9a-f]{64})$")


def _count_rows() -> int | None:
    conn = postgres_store._get_conn()
    if conn is None:
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM raw_data")
        row = cur.fetchone()
    return int(row[0]) if row else None


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set — aborting (this script targets Postgres).")
        return 1
    cache_dir = Path(os.environ.get("SETTLE_CACHE_DIR", "~/.cache/msc-settle")).expanduser()
    if not cache_dir.is_dir():
        print(f"Cache dir {cache_dir} does not exist — nothing to backfill.")
        return 1
    if not postgres_store.is_enabled():
        print("Postgres unreachable — check DATABASE_URL / network.")
        return 1

    files = sorted(cache_dir.glob("*.pkl"))
    print(f"Cache dir: {cache_dir}")
    print(f"Scanning {len(files)} pickle file(s)")
    print()

    rows_before = _count_rows()
    n_attempted = 0
    n_unparseable = 0
    n_unpickle_err = 0
    n_encode_err = 0
    by_source: dict[str, int] = {}

    for path in files:
        m = _FILENAME_RE.match(path.stem)
        if not m:
            print(f"  skip (unparseable filename): {path.name}")
            n_unparseable += 1
            continue
        source = m.group("source")
        args_hash = m.group("hash")

        # Unpickle the payload.
        try:
            with path.open("rb") as f:
                payload = pickle.load(f)
        except Exception as e:
            print(f"  unpickle failed for {path.name}: {type(e).__name__}: {e}")
            n_unpickle_err += 1
            continue

        # Encode upfront so unsupported types fail loudly here instead of
        # being swallowed by ``put()``'s broad exception handler.
        try:
            postgres_store.encode_payload(payload)
        except TypeError as e:
            print(f"  encode failed for {path.name}: {e}")
            n_encode_err += 1
            continue

        args = {
            "_backfilled_from_pickle": True,
            "_source_id": source,
            "_args_hash": args_hash,
        }
        postgres_store.put(source, args_hash, args, payload)
        n_attempted += 1
        by_source[source] = by_source.get(source, 0) + 1

    rows_after = _count_rows()
    inserted = (rows_after or 0) - (rows_before or 0)

    print()
    print(f"raw_data rows before:    {rows_before}")
    print(f"raw_data rows after:     {rows_after}  (Δ +{inserted} newly inserted)")
    print(f"Attempted INSERT:        {n_attempted}")
    print(f"Already in Postgres:     {n_attempted - inserted}  (no-op, ON CONFLICT)")
    print(f"Skipped (unparseable):   {n_unparseable}")
    print(f"Errors (unpickle):       {n_unpickle_err}")
    print(f"Errors (encode):         {n_encode_err}")
    print()
    print("Per-source breakdown of attempted inserts:")
    for src, n in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>5}  {src}")
    return 0 if (n_unpickle_err == 0 and n_encode_err == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
