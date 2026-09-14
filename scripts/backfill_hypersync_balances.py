"""Warm finalized per-token balance streams with one scan per chain/holder.

Only cache population: no settlement/config changes and no Dune requests.
Each materialized child is an exact token subset of a fully covered parent.
"""

import argparse
from datetime import UTC, datetime, time

from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.domain.primes import Chain
from settle.domain.sky_tokens import USDS_BY_CHAIN, USDS_ETHEREUM, sUSDS_ETHEREUM
from settle.extract import hypersync, postgres_store
from settle.extract import hypersync_store as store
from settle.normalize.sources.hypersync_balances import (
    _addr_topic,
    _default_start_block,
    _touching_selections,
)


def merge_coverage(old, new):
    if old is None:
        return new
    if new[0] <= old[1] + 1 and new[1] >= old[0] - 1:
        return min(old[0], new[0]), max(old[1], new[1])
    return old  # Never claim an unfetched gap between disjoint islands.


def backfill(chain, tokens, holder, start, end):
    conn = postgres_store._get_conn()
    if conn is None:
        raise RuntimeError("DATABASE_URL is required to retain the backfill")
    store._ensure_schema_once(conn)
    # Already covered children need no fresh broad scan.
    missing = []
    for token in sorted(tokens):
        child = store._stream_key(chain, _touching_selections(token, holder))
        coverage = store._get_coverage(conn, child)
        if coverage is None or not coverage[0] <= start <= end <= coverage[1]:
            missing.append(token)
    if not missing:
        print(chain, "holder already covered", "0x" + holder.hex(), flush=True)
        return
    selections = _touching_selections(missing[0], holder)
    for selection in selections:
        selection["address"] = ["0x" + t.hex() for t in missing]
    print(chain, "scan", len(missing), "tokens", start, end, flush=True)
    rows = store.fetch_logs(chain, selections, start, end)
    coverage = store._get_coverage(conn, store._stream_key(chain, selections))
    if coverage is None or not coverage[0] <= start <= end <= coverage[1]:
        raise RuntimeError("Parent stream is not fully finalized/covered; refusing to seed child coverage")
    by_token = {"0x" + t.hex(): [] for t in missing}
    who = _addr_topic(holder)
    for row in rows:
        if row.address not in by_token or who not in (row.topic1, row.topic2):
            raise ValueError("Parent response contains a log outside the requested selection")
        by_token[row.address].append(row)
    for token in missing:
        child = store._stream_key(chain, _touching_selections(token, holder))
        subset = by_token["0x" + token.hex()]
        store._persist(conn, child, subset)
        bounds = merge_coverage(store._get_coverage(conn, child), (start, end))
        store._set_coverage(conn, child, *bounds)
        print(chain, "cached", "0x" + token.hex(), len(subset), "logs", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prime", required=True)
    parser.add_argument("--chain", required=True)
    parser.add_argument("--month", default="2026-08")
    args = parser.parse_args()
    prime, chain, month = load_prime_by_id(args.prime), Chain(args.chain), Month.parse(args.month)
    groups = {}
    for venue in prime.venues:
        if venue.chain != chain or venue.skip or venue.pricing_category.value == "S2" or venue.lp_kind in {"uniswap_v3", "uniswap_v4"}:
            continue
        holder = venue.holder_override or prime.alm.get(chain)
        if holder:
            groups.setdefault(holder.value, set()).add(venue.token.address.value)
    if chain in prime.alm and chain in USDS_BY_CHAIN:
        groups.setdefault(prime.alm[chain].value, set()).add(USDS_BY_CHAIN[chain].address.value)
    if chain == Chain.ETHEREUM and chain in prime.subproxy:
        groups.setdefault(prime.subproxy[chain].value, set()).update([USDS_ETHEREUM.address.value, sUSDS_ETHEREUM.address.value])
    start = _default_start_block(chain.value, prime.start_date)
    end = hypersync.find_block_at_or_before(chain.value, int(datetime.combine(month.last_day, time(23, 59, 59), UTC).timestamp()))
    for holder, tokens in groups.items():
        backfill(chain.value, tokens, holder, start, end)


if __name__ == "__main__":
    main()
