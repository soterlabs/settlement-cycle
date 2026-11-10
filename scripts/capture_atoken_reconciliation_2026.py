"""Retain public inputs for the expanded #220 reconciliation, without settling.

Copies verified financial baselines, captures complete holder logs, and records
pinned RPC state. No monthly calculation, publication or payment is performed.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
from pathlib import Path
from unittest.mock import patch

import requests

from settle.domain import Chain
from settle.domain.config import load_prime_by_id
from settle.domain.pricing import PricingCategory
from settle.extract import aave_reconstruct as aave
from settle.extract import hypersync, rpc
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.revenue.verification import digest

ROOT = Path(__file__).resolve().parents[1]
CAT = {PricingCategory.AAVE_ATOKEN, PricingCategory.SPARKLEND_SPTOKEN}


def dump(path, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    tmp = path.with_suffix(path.suffix + ".partial")
    tmp.write_bytes(gzip.compress(raw, compresslevel=1, mtime=0))
    tmp.replace(path)


def baselines(root, retained):
    old = json.loads((ROOT / "reconciliation/atoken_event_principal_2026_jan_aug.json").read_text())
    records = []
    for name, expected in old["artifact_sha256"].items():
        path = root / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(
                "Historical provenance differs from the reviewed frozen baseline: " + name
            )
        d = json.loads(path.read_text())
        records.append(
            dict(
                prime=d["prime_id"],
                month=d["month"],
                period=d["period"],
                opening=d["pin_blocks_som"],
                closing=d["pin_blocks_eom"],
                venues=d["venue_breakdown"],
                results=d["results"],
                source=name,
                source_sha256=expected,
            )
        )
    for name in ["spark", "grove", "osero"]:
        d = json.loads((retained / f"recon-forum-{name}-september-retained.json").read_text())
        if (
            d["code_version"] != "536f1492089ed82fbf6eaabb956f7a194b887d47"
            or digest(d["result"]) != d["result_hash"]
        ):
            raise ValueError("Invalid September retained result")
        p = d["result"]
        records.append(
            dict(
                prime=name,
                month="2026-09",
                period=p["period"],
                opening=p["pin_blocks_som"],
                closing=p["period"]["pin_blocks"],
                venues=p["venue_breakdown"],
                results={
                    k: p[k]
                    for k in [
                        "sky_revenue",
                        "sde_revenue",
                        "prime_agent_revenue",
                        "agent_rate",
                        "distribution_rewards",
                        "chronicle_points",
                    ]
                },
                source_revision=d["revision_id"],
                source_result_hash=d["result_hash"],
                code_version=d["code_version"],
            )
        )
    return records


def capture(args):
    if args.env_file:
        from dotenv import dotenv_values

        allowed = set(rpc.RPC_ENV_VARS.values()) | {"ENVIO_API_TOKEN"}
        for key, value in dotenv_values(args.env_file).items():
            if key in allowed and value:
                os.environ[key] = value
    os.environ["SETTLE_NO_CACHE"] = "1"
    logging.disable(logging.CRITICAL)  # Never print provider URLs in errors.
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "inputs.json.gz"
    document = (
        json.loads(gzip.decompress(path.read_bytes()))
        if path.exists()
        else dict(
            status="capturing",
            baselines=baselines(args.baseline_root, args.retained_root),
            venues={},
            rpc={},
            queries={},
            errors=[],
        )
    )
    expected = {
        name: hashlib.sha256((ROOT / "config" / f"{name}.yaml").read_bytes()).hexdigest()
        for name in ["spark", "grove", "osero"]
    }
    if document.get("config_sha256", expected) != expected:
        raise ValueError("Capture config changed")
    document["config_sha256"] = expected
    lock = threading.RLock()
    real = rpc._post
    urls = {
        rpc.rpc_url(c): c.value
        for c in [Chain.ETHEREUM, Chain.BASE, Chain.ARBITRUM, Chain.AVALANCHE_C]
    }

    def post(url, method, params):
        key = json.dumps([urls[url], method, params], sort_keys=True, separators=(",", ":"))
        with lock:
            if key in document["rpc"]:
                return document["rpc"][key]
        value = real(url, method, params)
        if method == "eth_getBlockByNumber" and value:
            value = {k: value[k] for k in ["number", "timestamp", "hash", "parentHash"]}
        with lock:
            document["rpc"][key] = value
        return value

    def query(chain, selections, pin, start=0):
        key = json.dumps([chain, selections, start, pin], sort_keys=True, separators=(",", ":"))
        if key not in document["queries"]:
            document.setdefault("query_parts", {})
            rows = []
            heights = []
            for lower in range(start, pin + 1, 2_000_000):
                upper = min(pin, lower + 1_999_999)
                part = json.dumps(
                    [chain, selections, lower, upper], sort_keys=True, separators=(",", ":")
                )
                if part not in document["query_parts"]:
                    response = hypersync.query_logs(
                        chain,
                        selections,
                        lower,
                        upper,
                        log_fields=[*hypersync._DEFAULT_LOG_FIELDS, "transaction_hash"],
                    )
                    if response.archive_height < upper:
                        raise ValueError("Incomplete source archive")
                    document["query_parts"][part] = dict(
                        archive_height=response.archive_height,
                        rows=[asdict(r) for r in response.rows],
                    )
                    dump(path, document)
                    print(
                        "History interval",
                        chain,
                        lower,
                        upper,
                        len(response.rows),
                        "logs",
                        flush=True,
                    )
                saved = document["query_parts"][part]
                rows.extend(saved["rows"])
                heights.append(saved["archive_height"])
            document["queries"][key] = dict(
                from_block=start,
                to_block=pin,
                archive_height=min(heights),
                selections=selections,
                rows=rows,
            )
            dump(path, document)
        return key

    with patch.object(rpc, "_post", post):
        for name in ["spark", "grove", "osero"]:
            prime = load_prime_by_id(name)
            periods = [r for r in document["baselines"] if r["prime"] == name]
            for v in prime.venues:
                if not v.settles or v.pricing_category not in CAT:
                    continue
                key = name + "/" + v.id
                if document["venues"].get(key, {}).get("history_complete"):
                    continue
                pin = max(
                    r["closing"][v.chain.value] for r in periods if v.chain.value in r["closing"]
                )
                holder = v.holder_override or prime.alm[v.chain]
                ht = aave._addr_topic(holder.value)
                token = v.token.address.hex
                sel = [
                    {"address": [token], "topics": [[TRANSFER_TOPIC0], [ht]]},
                    {"address": [token], "topics": [[TRANSFER_TOPIC0], [], [ht]]},
                    {"address": [token], "topics": [[aave.MINT_T0], [], [ht]]},
                    {"address": [token], "topics": [[aave.BURN_T0], [ht]]},
                    {"address": [token], "topics": [[aave.BT_T0], [ht]]},
                    {"address": [token], "topics": [[aave.BT_T0], [], [ht]]},
                ]
                opening = min(
                    r["opening"][v.chain.value] for r in periods if v.chain.value in r["opening"]
                )
                print(
                    "Capturing", key, "from verified opening", opening, "through", pin, flush=True
                )
                source = query(v.chain.value, sel, pin, opening)
                pool = rpc.aave_pool(v.chain, v.token.address, pin)
                reserve = rpc.aave_underlying_asset(v.chain, v.token.address, pin)
                if not any(pool) or not any(reserve):
                    raise ValueError("Missing Aave protocol metadata")
                states = {}
                for record in periods:
                    if v.chain.value not in record["closing"]:
                        continue
                    for b in [record["opening"][v.chain.value], record["closing"][v.chain.value]]:
                        if b not in states:
                            states[b] = dict(
                                balance_raw=str(
                                    rpc.balance_of(v.chain, v.token.address, holder, b)
                                ),
                                scaled_raw=str(
                                    rpc.scaled_balance_of(v.chain, v.token.address, holder, b)
                                ),
                            )
                document["venues"][key] = dict(
                    prime=name,
                    venue=v.id,
                    chain=v.chain.value,
                    token=token,
                    holder=holder.hex,
                    decimals=v.token.decimals,
                    pool="0x" + pool.hex(),
                    reserve="0x" + reserve.hex(),
                    query=source,
                    states=states,
                    history_complete=True,
                )
                dump(path, document)
                print(
                    "Captured",
                    key,
                    len(document["queries"][source]["rows"]),
                    "logs;",
                    len(states),
                    "state pins",
                    flush=True,
                )
        from settle.normalize.positions import _MERKL_DISTRIBUTORS
        from settle.normalize.sources.hypersync_venue_events import CLAIMED

        document.setdefault("external_queries", {})
        for name in ["spark", "grove", "osero"]:
            prime = load_prime_by_id(name)
            records = [r for r in document["baselines"] if r["prime"] == name]
            for chain in {v.chain for v in prime.venues if v.settles and v.pricing_category in CAT}:
                senders = {
                    s.value
                    for r in records
                    for s in prime.external_sources_for_period(
                        chain, date.fromisoformat(r["period"]["start"])
                    )
                }
                for sender in senders & _MERKL_DISTRIBUTORS.get(chain, set()):
                    pin = max(
                        r["closing"][chain.value] for r in records if chain.value in r["closing"]
                    )
                    holder = aave._addr_topic(prime.alm[chain].value)
                    selectors = [
                        {"address": ["0x" + sender.hex()], "topics": [[CLAIMED], [holder]]}
                    ]
                    opening = min(
                        r["opening"][chain.value] for r in records if chain.value in r["opening"]
                    )
                    source = query(chain.value, selectors, pin, opening)
                    document["external_queries"][name + "/" + chain.value + "/" + sender.hex()] = (
                        source
                    )
        document["status"] = "histories_and_boundary_states_complete"
        dump(path, document)
        # Retain the exact same balanceOf/scaledBalanceOf archive calls used
        # by production at each Transfer block and block-minus-one. Batch
        # transport changes neither their parameters nor their pinned results.
        jobs = []
        for _key, v in document["venues"].items():
            records = [r for r in document["baselines"] if r["prime"] == v["prime"]]
            opening = min(r["opening"][v["chain"]] for r in records if v["chain"] in r["opening"])
            closing = max(r["closing"][v["chain"]] for r in records if v["chain"] in r["closing"])
            points = {
                b
                for r in document["queries"][v["query"]]["rows"]
                if r["topic0"] == TRANSFER_TOPIC0 and opening < r["block_number"] <= closing
                for b in [r["block_number"] - 1, r["block_number"]]
            }
            points.update(int(b) for b in v["states"])
            for b in sorted(points):
                for signature in ["balanceOf(address)", "scaledBalanceOf(address)"]:
                    selector = (
                        "0x" + keccak256(signature.encode())[:4].hex() + v["holder"][2:].zfill(64)
                    )
                    params = [dict(to=v["token"], data=selector, gas="0x989680"), hex(b)]
                    ident = json.dumps(
                        [v["chain"], "eth_call", params], sort_keys=True, separators=(",", ":")
                    )
                    if ident not in document["rpc"]:
                        jobs.append((v["chain"], ident, params))
        # Coalesce overlapping callback points before batching.
        jobs = list({j[1]: j for j in jobs}.values())
        groups = []
        for chain in sorted({j[0] for j in jobs}):
            chain_jobs = [j for j in jobs if j[0] == chain]
            groups.extend(chain_jobs[i : i + 24] for i in range(0, len(chain_jobs), 24))
        print(
            "Exact archive state calls to capture",
            len(jobs),
            "in",
            len(groups),
            "batches",
            flush=True,
        )
        completed = 0

        def batch(group):
            nonlocal completed
            chain = Chain(group[0][0])
            body = [
                dict(jsonrpc="2.0", id=i, method="eth_call", params=j[2])
                for i, j in enumerate(group)
            ]
            response = None
            for attempt in range(5):
                try:
                    r = requests.post(rpc.rpc_url(chain), json=body, timeout=60)
                    r.raise_for_status()
                    value = r.json()
                    if isinstance(value, list):
                        indexed = {x.get("id"): x for x in value}
                        if len(indexed) != len(group) or set(indexed) != set(range(len(group))):
                            raise ValueError("Invalid batched RPC identities")
                        response = indexed
                    break
                except (requests.RequestException, ValueError):
                    if attempt == 4:
                        raise
                    time.sleep(min(2**attempt, 8))
            for i, job in enumerate(group):
                entry = response[i] if response else {}
                if "result" in entry:
                    result = entry["result"]
                    if not isinstance(result, str) or (len(result) != 66 and result != "0x"):
                        raise ValueError("Invalid raw archive state response")
                    with lock:
                        document["rpc"][job[1]] = result
                else:
                    # Unsupported batches/individual errors use the normal
                    # production transport and its revert/error handling.
                    post(rpc.rpc_url(chain), "eth_call", job[2])
            with lock:
                completed += 1
                if completed % 40 == 0:
                    dump(path, document)
                    print("State batches", completed, "/", len(groups), flush=True)

        with ThreadPoolExecutor(max_workers=2) as executor:
            list(executor.map(batch, groups))
        document["status"] = "event_states_complete"
        dump(path, document)
    print("Capture complete", len(document["venues"]), "venues", flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--baseline-root", type=Path, required=True)
    p.add_argument("--retained-root", type=Path, required=True)
    p.add_argument("--env-file", type=Path)
    args = p.parse_args()
    try:
        capture(args)
    except Exception as exc:
        print("Capture stopped:", type(exc).__name__, flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
