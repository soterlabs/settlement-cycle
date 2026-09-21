#!/usr/bin/env python3
"""Replay allocation capital without regenerating settlement artifacts.

Repeat the same command after a failure: finalized log checkpoints and RPC
reads are reused from Postgres. Deterministic accounting errors remain fatal.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import UTC, date, datetime, time
from pathlib import Path

from settle.compute.allocation_capital import replay_history
from settle.domain.config import load_prime_by_id
from settle.domain.primes import Chain
from settle.normalize.allocation_capital import fetch_capital_history
from settle.normalize.sources.hypersync_block_resolver import HyperSyncBlockResolver


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prime")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output-dir", type=Path, required=True,
                        help="Dedicated directory; do not run concurrent jobs in it")
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("start must not be after end")
    if not os.environ.get("DATABASE_URL") or os.environ.get("HYPERSYNC_NO_STORE") == "1":
        parser.error("DATABASE_URL with HyperSync persistence is required for restartable validation")
    os.environ["SETTLE_REQUIRE_POSTGRES"] = "1"
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    prime = load_prime_by_id(args.prime)
    resolver = HyperSyncBlockResolver()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output_dir / "pins.json"
    identity = dict(prime=args.prime, start=str(args.start), end=str(args.end))
    pins = None
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if any(manifest.get(k) != v for k, v in identity.items()):
            parser.error("output directory belongs to a different validation period")
        pins = {Chain(k): v for k, v in manifest["pins"].items()}
        if set(pins) != set(prime.chains):
            parser.error("configured chains changed; use a new output directory")
    status_path = args.output_dir / "status.json"
    save(status_path, dict(identity, status="running", started=datetime.now(UTC)))
    try:
        if pins is None:
            cutoff = datetime.combine(args.end, time(23, 59, 59), UTC)
            pins = {}
            for chain in prime.chains:
                logging.info("Resolving %s end-of-period block", chain.value)
                pins[chain] = resolver.block_at_or_before(chain.value, cutoff)
            save(manifest_path, dict(identity, pins={c.value: v for c, v in pins.items()}))
        history = fetch_capital_history(prime, pins, block_resolver=resolver)
        replay = replay_history(history, args.start, args.end)
        result = dict(identity, batches=len(history.batches), unsupported=history.unsupported,
                      unmatched_receipts=len(replay.unmatched_receipts),
                      unmatched_outflows=len(replay.unmatched_outflows))
        save(args.output_dir / "result.json", result)
        save(status_path, dict(identity, status="completed", ended=datetime.now(UTC)))
        print(json.dumps(result, indent=2, default=str))
    except Exception as exc:
        # Exception messages may include provider URLs. Keep status safe to share;
        # the local process log retains the full traceback for diagnosis.
        save(status_path, dict(identity, status="failed", error_type=type(exc).__name__,
                               ended=datetime.now(UTC)))
        raise


if __name__ == "__main__":
    main()
