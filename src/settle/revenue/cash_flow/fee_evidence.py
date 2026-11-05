"""Reuse reviewed MetaMorpho fee-event authentication for the reporting cost book."""

import os
from pathlib import Path

from ...domain.primes import Chain
from ...extract import hypersync, hypersync_store
from ...normalize.allocation_morpho_fees import (
    ACCRUE_INTEREST,
    ACCRUE_INTEREST_V2,
    V2_VAULTS,
    VAULTS,
    fee_mints,
)
from ...store.db import connect
from ..verification import digest
from .collector import durable_capture, event, read_capture, write_capture
from .liability import address


def collect(chain, block, cutoff, config, cache_dir):
    revision = digest({"schema": "cash-flow-fee-proof.v1", "configuration": config["revision"]})
    path = Path(cache_dir) / f"{revision}-{chain}-{cutoff}.json.gz"
    saved = read_capture(path) if path.exists() else None
    saved = saved or durable_capture(revision, chain, cutoff)
    if saved and saved["block"] == block:
        return saved["events"]
    prior = None
    for candidate in sorted(Path(cache_dir).glob(f"{revision}-{chain}-*.json.gz"), reverse=True):
        prior = read_capture(candidate)
        if prior:
            break
    prior = prior or durable_capture(revision, chain)
    rows = [r for r in (prior or {}).get("events", []) if r["block"] <= block]
    start = (prior or {}).get("block", -1) + 1
    contracts = sorted(VAULTS.get(Chain(chain), set()) | V2_VAULTS.get(Chain(chain), set()))
    queries = [{"address": contracts, "topics": [[ACCRUE_INTEREST, ACCRUE_INTEREST_V2]]}]
    if start <= block and contracts:
        cached, missing = read_source_cache(chain, queries, start, block)
        rows.extend(event(r, chain) for r in cached)
        for lo, hi in missing:
            rows.extend(
                event(r, chain)
                for r in hypersync.query_logs(
                    chain,
                    queries,
                    lo,
                    hi,
                    log_fields=[*hypersync._DEFAULT_LOG_FIELDS, "transaction_hash"],
                    max_num_logs=100000,
                ).rows
            )
    rows = list({(r["block"], r["log_index"]): r for r in rows}.values())
    result = {"status": "available", "chain": chain, "block": block, "events": rows}
    write_capture(path, result)
    durable_capture(revision, chain, cutoff, document=result)
    return rows


def read_source_cache(chain, queries, lo, hi):
    """Read existing certified source coverage; never write settlement caches."""
    if not os.environ.get("DATABASE_URL"):
        return [], [(lo, hi)]
    try:
        stream = hypersync_store._stream_key(
            chain, queries, [*hypersync._DEFAULT_LOG_FIELDS, "transaction_hash"]
        )
        with connect(autocommit=True) as conn:
            with conn.transaction():
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                ranges = hypersync_store._coverage_ranges(conn, stream)
                missing = hypersync_store._missing_ranges(lo, hi, ranges)
                rows = []
                for start, end in ranges:
                    a, b = max(lo, start), min(hi, end)
                    if a <= b:
                        rows.extend(hypersync_store._read_rows(conn, stream, a, b))
        return rows, missing
    except Exception:
        return [], [(lo, hi)]


def verified_mint(chain, mint, fee_rows):
    owner = address(mint["topic2"])
    peers = [
        r
        for r in fee_rows
        if r["tx_hash"] == mint["tx_hash"]
        and r["block"] == mint["block"]
        and r["address"] == mint["address"]
    ]
    if not peers:
        return False

    def row(e):
        return hypersync.LogRow(
            block_number=e["block"],
            log_index=e["log_index"],
            block_time=e["timestamp"],
            address=e["address"],
            topic0=e["topic0"],
            topic1=e["topic1"],
            topic2=e["topic2"],
            topic3=e["topic3"],
            data=e["data"],
            transaction_hash=e["tx_hash"],
        )

    fees = fee_mints(
        Chain(chain), [row(mint), *(row(r) for r in peers)], {(mint["address"], owner)}
    )
    return fees.get((mint["address"], owner), 0) == int(mint["data"], 16) > 0
