"""Capture verified archive USDS wallets at supplied daily pins; read-only RPC."""

from __future__ import annotations

import argparse
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from scripts.audit_usds_bridge_transit_2026 import verified_wallet_usds
from settle.domain.config import load_prime_by_id
from settle.domain.sky_tokens import USDS_BY_CHAIN
from settle.extract import rpc

CHAIN_IDS = {"ethereum": 1, "base": 8453, "arbitrum": 42161, "optimism": 10, "unichain": 130}


def capture(pin_source, output, *, workers=2):
    prime = load_prime_by_id("spark")
    previous = json.loads(output.read_text()) if output.exists() else None
    document = previous or {"schema": 1, "prime": "spark", "days": {}, "chains": {}}
    if document.get("prime") != prime.id:
        raise ValueError("Checkpoint belongs to another prime")
    pins = json.loads(pin_source.read_text())
    pins = {
        day: {chain: row["block"] for chain, row in states.items()}
        for day, states in pins["days"].items()
    }
    expected_chains = {c for c in prime.alm if c in USDS_BY_CHAIN}
    for chain in expected_chains:
        token = USDS_BY_CHAIN[chain]
        chain_id = int(rpc._post(rpc.rpc_url(chain), "eth_chainId", []), 16)
        if chain_id != CHAIN_IDS[chain.value]:
            raise ValueError("Archive endpoint serves another chain")
        meta = {
            "chain_id": chain_id,
            "holder": prime.alm[chain].hex,
            "token": token.address.hex,
            "decimals": token.decimals,
        }
        if chain.value in document["chains"] and document["chains"][chain.value] != meta:
            raise ValueError("Checkpoint wallet configuration changed")
        document["chains"][chain.value] = meta
    document["status"] = "capturing"

    def save():
        partial = output.with_suffix(output.suffix + ".partial")
        partial.write_text(json.dumps(document, indent=2) + "\n")
        partial.replace(output)

    def get(job):
        day, chain = job
        block = pins[day][chain.value]
        old = document["days"].get(day, {}).get(chain.value)
        if old is not None:
            if old["block"] != block:
                raise ValueError("Checkpoint daily pin changed")
            return day, chain.value, old
        quantity = rpc.balance_of(chain, USDS_BY_CHAIN[chain].address, prime.alm[chain], block)
        headers = [
            rpc._post(rpc.rpc_url(chain), "eth_getBlockByNumber", [hex(b), False])
            for b in [block, block + 1]
        ]
        return (
            day,
            chain.value,
            {
                "block": block,
                "raw_balance": str(quantity),
                "headers": [
                    {k: h[k] for k in ["number", "hash", "parentHash", "timestamp"]}
                    for h in headers
                ],
            },
        )

    jobs = []
    for day in sorted(pins):
        active = {c for c in expected_chains if c in prime.chains_at(date.fromisoformat(day))}
        if set(pins[day]) != {c.value for c in active}:
            raise ValueError("Pin source misses an active wallet")
        jobs.extend((day, c) for c in sorted(active, key=lambda c: c.value))
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        for index, future in enumerate(as_completed([pool.submit(get, j) for j in jobs]), 1):
            day, chain, state = future.result()
            document["days"].setdefault(day, {})[chain] = state
            if index % 10 == 0:
                save()
                print(f"Captured {index}/{len(jobs)} wallet states", flush=True)
        # Validate each complete day, including checkpoint data, before attesting completion.
        document["status"] = "complete"
        for day in pins:
            verified_wallet_usds(document, prime, day)
    except BaseException:
        document["status"] = "incomplete"
        save()
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        pool.shutdown(wait=True)
        save()
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.env_file:
        from dotenv import dotenv_values

        for name, value in dotenv_values(args.env_file).items():
            if name in rpc.RPC_ENV_VARS.values() and value:
                os.environ[name] = value
    os.environ["SETTLE_NO_CACHE"] = "1"
    os.environ["SETTLE_REQUIRE_POSTGRES"] = "0"
    # The lower-level retry logger includes endpoint fragments. Report only
    # sanitized exception types here; no credential fragments in capture logs.
    logging.getLogger("settle.extract.rpc").disabled = True
    try:
        capture(args.pin_source, args.output)
    except Exception as error:
        raise SystemExit(
            f"Capture incomplete ({type(error).__name__}); checkpoint retained"
        ) from None


if __name__ == "__main__":
    main()
