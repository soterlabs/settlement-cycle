"""Read finalized transfer/Savings evidence and independent historical balances.

No provider errors/URLs or credentials are persisted. The cache contains only
public chain evidence, keyed by configuration and certified cutoff.
"""

import gzip
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import requests

from ...extract import hypersync
from ...extract._keccak import keccak256
from ...extract.hypersync import _DEFAULT_LOG_FIELDS
from ..spark_onchain import certified_boundary, chain_origin, topic
from ..verification import digest
from .config import load

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
DEPOSIT = "0x" + keccak256(b"Deposit(address,address,uint256,uint256)").hex()
WITHDRAW = "0x" + keccak256(b"Withdraw(address,address,address,uint256,uint256)").hex()
RPC_KEYS = {
    "ethereum": "ETH_RPC",
    "base": "BASE_RPC",
    "arbitrum": "ARBITRUM_RPC",
    "optimism": "OPTIMISM_RPC",
    "unichain": "UNICHAIN_RPC",
    "avalanche_c": "AVALANCHE_C_RPC",
    "robinhood": "ROBINHOOD_RPC",
    "xlayer": "XLAYER_RPC",
    "bsc": "BSC_RPC",
    "gnosis": "GNOSIS_RPC",
}


def rpc(chain, method, params):
    url = os.environ.get(RPC_KEYS[chain])
    if not url:
        raise ValueError("RPC configuration unavailable")
    for _ in range(3):
        try:
            r = requests.post(
                url,
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                timeout=25,
            )
            result = r.json()
            if r.ok and isinstance(result, dict) and "result" in result:
                return result["result"]
        except (requests.RequestException, ValueError):
            pass
    raise ValueError("Historical RPC read unavailable")


def balance(chain, token, owner, block, *, before_genesis=False):
    if before_genesis:
        return 0
    raw = rpc(
        chain, "eth_call", [{"to": token, "data": "0x70a08231" + owner[2:].zfill(64)}, hex(block)]
    )
    if raw == "0x":
        if rpc(chain, "eth_getCode", [token, hex(block)]) != "0x":
            raise ValueError("Invalid token balance getter")
        return 0
    if not isinstance(raw, str) or len(raw) != 66:
        raise ValueError("Malformed historical balance")
    return int(raw, 16)


def event(row, chain):
    return {
        "chain": chain,
        "block": row.block_number,
        "log_index": row.log_index,
        "timestamp": row.block_time,
        "tx_hash": row.transaction_hash,
        "address": row.address.lower(),
        "topic0": row.topic0,
        "topic1": row.topic1,
        "topic2": row.topic2,
        "topic3": row.topic3,
        "data": row.data,
    }


def boundaries(chain, cutoff):
    first = cutoff.replace(day=1) - timedelta(days=1)
    origin, _ = chain_origin(chain)

    def pin(day):
        before = int(datetime.combine(day, time.max, UTC).timestamp()) < origin
        return {
            "block": 0 if before else certified_boundary(chain, day, 64),
            "before_genesis": before,
        }

    return {"opening": pin(first), "closing": pin(cutoff)}


def durable_capture(config_revision, chain, cutoff=None, document=None):
    if (
        not os.environ.get("DATABASE_URL")
        or os.environ.get("CASH_FLOW_CAPTURE_PERSIST", "1") != "1"
    ):
        return None
    from psycopg.types.json import Jsonb

    from ...store.db import connect

    try:
        with connect(autocommit=True) as conn:
            if document is not None:
                if document.get("status") != "available":
                    return None
                conn.execute(
                    "INSERT INTO reporting_cash_flow_capture_cache VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (config_revision, chain, cutoff, Jsonb(document), digest(document)),
                )
                return None
            if cutoff is None:
                row = conn.execute(
                    "SELECT document,document_hash FROM reporting_cash_flow_capture_cache WHERE configuration_revision=%s AND chain=%s ORDER BY cutoff DESC LIMIT 1",
                    (config_revision, chain),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT document,document_hash FROM reporting_cash_flow_capture_cache WHERE configuration_revision=%s AND chain=%s AND cutoff=%s",
                    (config_revision, chain, cutoff),
                ).fetchone()
            if row and digest(row[0]) == row[1]:
                return row[0]
    except Exception:
        return None  # Cache failure does not make chain evidence successful.
    return None


def collect_chain(chain, cutoff, config, cache_dir, progress=None):
    if not config["tokens"].get(chain) or chain not in hypersync.HYPERSYNC_HOSTS:
        return {
            "chain": chain,
            "status": "unavailable",
            "reason": "event_source_or_token_registry_unconfigured",
            "events": [],
            "balances": [],
        }
    root = Path(cache_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{config['revision']}-{chain}-{cutoff}.json.gz"
    if path.exists():
        with gzip.open(path, "rt") as f:
            cached = json.load(f)
        if cached.get("status") == "available":
            return cached
    saved = durable_capture(config["revision"], chain, cutoff)
    if saved and saved.get("status") == "available":
        return saved
    pins = boundaries(chain, cutoff)
    if pins["closing"]["before_genesis"]:
        result = {
            "chain": chain,
            "status": "available",
            "pins": pins,
            "events": [],
            "balances": [],
            "absence_proof": "cutoff_precedes_chain_genesis",
        }
    else:
        owners = list(config["wallets"][chain])
        tokens = config["tokens"][chain]
        selections = [
            {"address": list(tokens), "topics": [[TRANSFER], [topic(a) for a in owners]]},
            {"address": list(tokens), "topics": [[TRANSFER], [], [topic(a) for a in owners]]},
        ]
        start = pins["opening"]["block"] + 1
        logs = hypersync.query_logs(
            chain,
            selections,
            start,
            pins["closing"]["block"],
            log_fields=[*_DEFAULT_LOG_FIELDS, "transaction_hash"],
            max_num_logs=100000,
        ).rows
        all_events = {(r.block_number, r.log_index): event(r, chain) for r in logs}
        if progress:
            progress(chain, "transfers:" + str(len(all_events)))
        vaults = [v["address"] for v in config["savings"] if v["chain"] == chain]
        savings = []
        if vaults:
            # A later archive capture can supply immutable earlier logs, never
            # later balances/valuations. Filter strictly at this month's pin.
            candidates = sorted(root.glob(f"{config['revision']}-{chain}-*.json.gz"), reverse=True)
            previous = None
            for candidate in candidates:
                with gzip.open(candidate, "rt") as f:
                    cached = json.load(f)
                if cached.get("status") == "available" and "savings_events" in cached:
                    previous = cached
                    break
            previous = previous or durable_capture(config["revision"], chain)
            history_end = (
                previous.get("pins", {}).get("closing", {}).get("block", -1) if previous else -1
            )
            savings = [
                e
                for e in (previous or {}).get("savings_events", [])
                if e["block"] <= pins["closing"]["block"]
            ]
            if history_end < pins["closing"]["block"]:
                savings.extend(
                    event(r, chain)
                    for r in hypersync.query_logs(
                        chain,
                        [{"address": vaults, "topics": [[TRANSFER, DEPOSIT, WITHDRAW]]}],
                        max(0, history_end + 1),
                        pins["closing"]["block"],
                        log_fields=[*_DEFAULT_LOG_FIELDS, "transaction_hash"],
                        max_num_logs=100000,
                    ).rows
                )
        if progress:
            progress(chain, "savings:" + str(len(savings)))
        pairs = []
        for token, meta in tokens.items():
            if not meta["cash"] and meta["symbol"] not in ("RLUSD", "PYUSD", "USDG"):
                continue
            for owner, role in config["wallets"][chain].items():
                if role["role"] in ("savings_idle", "intents") and meta["symbol"] not in (
                    "USDC",
                    "USDT",
                    "PYUSD",
                    "USDG",
                ):
                    continue
                pairs.append((token, owner))
        # Batch public historical getters to avoid one HTTP connection per
        # wallet/token. Keep an individual failed balance explicit; it must not
        # discard otherwise complete transfer evidence for the chain.
        requests_list = [
            (token, owner, key, pin) for token, owner in pairs for key, pin in pins.items()
        ]
        states = {(token, owner): {"token": token, "owner": owner} for token, owner in pairs}

        def batch(items):
            active = [
                (i, t, o, k, p) for i, (t, o, k, p) in enumerate(items) if not p["before_genesis"]
            ]
            raw = {}
            if active:
                payload = [
                    {
                        "jsonrpc": "2.0",
                        "id": i,
                        "method": "eth_call",
                        "params": [
                            {"to": t, "data": "0x70a08231" + o[2:].zfill(64)},
                            hex(p["block"]),
                        ],
                    }
                    for i, t, o, k, p in active
                ]
                try:
                    response = requests.post(os.environ[RPC_KEYS[chain]], json=payload, timeout=35)
                    data = response.json()
                    if isinstance(data, list):
                        raw = {r["id"]: r.get("result") for r in data if isinstance(r, dict)}
                except (requests.RequestException, ValueError):
                    pass
            out = []
            for i, (t, o, k, p) in enumerate(items):
                try:
                    v = raw.get(i)
                    value = (
                        0
                        if p["before_genesis"]
                        else int(v, 16)
                        if isinstance(v, str) and len(v) == 66
                        else balance(chain, t, o, p["block"])
                    )
                    out.append((t, o, k, str(value), None))
                except Exception as exc:
                    out.append((t, o, k, None, type(exc).__name__))
            return out

        chunks = [requests_list[i : i + 50] for i in range(0, len(requests_list), 50)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            for chunk in pool.map(batch, chunks):
                for token, owner, key, value, error in chunk:
                    states[token, owner][key] = value
                    if error:
                        states[token, owner][key + "_error"] = error
        balances = list(states.values())
        balances_complete = all(
            r.get("opening") is not None and r.get("closing") is not None for r in balances
        )
        result = {
            "chain": chain,
            "status": "available" if balances_complete else "partial",
            "events_complete": True,
            "balances_complete": balances_complete,
            "pins": pins,
            "events": sorted(all_events.values(), key=lambda r: (r["block"], r["log_index"])),
            "savings_events": savings,
            "balances": balances,
        }
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as f:
        json.dump(result, f, separators=(",", ":"))
    tmp.replace(path)
    durable_capture(config["revision"], chain, cutoff, document=result)
    if progress:
        progress(chain, len(result["events"]))
    return result


def collect(cutoff, *, cache_dir=None, config=None, workers=2, progress=None):
    config = config or load()
    cache_dir = cache_dir or os.environ.get("CASH_FLOW_CACHE_DIR", "/tmp/settle-cash-flow")
    result = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        jobs = {
            pool.submit(collect_chain, ch, cutoff, config, cache_dir, progress): ch
            for ch in config["wallets"]
        }
        for future in as_completed(jobs):
            ch = jobs[future]
            try:
                result[ch] = future.result()
            except Exception as exc:
                result[ch] = {
                    "chain": ch,
                    "status": "unavailable",
                    "reason": "collection_failed",
                    "error_type": type(exc).__name__,
                    "events": [],
                    "balances": [],
                }
                if progress:
                    progress(ch, "unavailable:" + type(exc).__name__)
    return {"cutoff": str(cutoff), "configuration_revision": config["revision"], "chains": result}
