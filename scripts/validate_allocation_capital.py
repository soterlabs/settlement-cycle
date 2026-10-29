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
from dataclasses import replace
from datetime import UTC, date, datetime, time
from pathlib import Path

from settle.compute.allocation_capital import replay_history
from settle.domain.config import load_prime_by_id
from settle.domain.primes import Chain
from settle.normalize.allocation_capital import fetch_capital_history
from settle.normalize.allocation_history_cache import fingerprint, load_history, save_history
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
    parser.add_argument("--capture-only", action="store_true",
                        help="Save normalized principal inputs without funding-origin replay")
    parser.add_argument("--provenance", type=Path,
                        help="Use the existing settlement's exact closing block pins")
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
    omitted = {}
    if args.provenance:
        control = json.loads(args.provenance.read_text())
        if (control["prime_id"] != args.prime or control["period"]["start"] != str(args.start)
                or control["period"]["end"] != str(args.end)):
            parser.error("provenance must match the requested prime and period")
        pins = {Chain(c): b for c, b in control["pin_blocks_eom"].items()}
        # A current configuration can contain chains onboarded after this close.
        # Never invent historical pins for those positions.
        omitted = {v.id: "Chain absent from the reviewed settlement pins"
                   for v in prime.venues if v.chain not in pins}
        prime = replace(prime, venues=[v for v in prime.venues if v.chain in pins],
                        alm={c: a for c, a in prime.alm.items() if c in pins},
                        alm_start_dates={c: d for c, d in prime.alm_start_dates.items() if c in pins},
                        subproxy={c: a for c, a in prime.subproxy.items() if c in pins})
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if any(manifest.get(k) != v for k, v in identity.items()):
            parser.error("output directory belongs to a different validation period")
        saved_pins = {Chain(k): v for k, v in manifest["pins"].items()}
        if pins is not None and saved_pins != pins:
            parser.error("existing history pins differ from the settlement")
        pins = saved_pins
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
        history_path = args.output_dir / "history.jsonl.gz"
        history_key = fingerprint(prime, pins)
        history = load_history(history_path, history_key)
        if history is None:
            history = fetch_capital_history(prime, pins, block_resolver=resolver)
            history = replace(history, unsupported={**history.unsupported, **omitted})
            save_history(history_path, history, history_key)
            logging.info("Saved normalized history before replay: %s", history_path)
        else:
            logging.info("Reusing normalized history: %s", history_path)
        if args.capture_only:
            result = dict(identity, batches=len(history.batches), unsupported=history.unsupported,
                          normalized_only=True)
            save(args.output_dir / "result.json", result)
            save(status_path, dict(identity, status="completed", ended=datetime.now(UTC)))
            print(json.dumps(result, indent=2))
            return
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
