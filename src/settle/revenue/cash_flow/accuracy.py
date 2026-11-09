"""Additional retained ERC4626 evidence; no assumption that a redemption is all principal."""

from collections import defaultdict
from decimal import ROUND_DOWN, Decimal, localcontext
from pathlib import Path

from ...domain.config import load_prime_by_id
from ...extract import hypersync
from ...extract._keccak import keccak256
from ...extract.hypersync import _DEFAULT_LOG_FIELDS
from ..spark_onchain import topic
from ..verification import digest
from . import fee_evidence
from .collector import (
    DEPOSIT,
    TRANSFER,
    WITHDRAW,
    durable_capture,
    event,
    read_capture,
    rpc,
    write_capture,
)
from .liability import ZERO, address

SCHEMA = "cash-flow-accuracy.v1"


def specs(config):
    return [
        {
            "venue": v.id,
            "chain": str(v.chain),
            "address": v.token.address.hex.lower(),
            "asset": v.underlying.address.hex.lower(),
            "decimals": v.underlying.decimals,
        }
        for v in load_prime_by_id(config["prime"]).venues
        if str(v.pricing_category) == "B"
        and v.underlying
        and config["tokens"]
        .get(str(v.chain), {})
        .get(v.underlying.address.hex.lower(), {})
        .get("cash")
    ] + config.get("reporting_vaults", [])


def collect(evidence, config, cache_dir, progress=None):
    revision = digest({"schema": SCHEMA, "configuration": config["revision"]})
    root = Path(cache_dir) / "accuracy"
    root.mkdir(parents=True, exist_ok=True)
    groups = defaultdict(list)
    for spec in specs(config):
        groups[spec["chain"]].append(spec)
    results = {}
    for chain, vaults in groups.items():
        if progress:
            progress(chain, "verifying-vaults")
        source = evidence["chains"][chain]
        pin = source.get("pins", {}).get("closing", {})
        if source["status"] == "unavailable" or pin.get("before_genesis"):
            results[chain] = {"status": "unavailable", "reason": "No deployed source at cutoff"}
            continue
        block = pin["block"]
        path = root / f"{revision}-{chain}-{evidence['cutoff']}.json.gz"
        saved = read_capture(path) if path.exists() else None
        saved = saved or durable_capture(revision, chain, evidence["cutoff"])
        if (
            saved
            and "fee_events" in saved
            and "share_balances" in saved
            and len(saved["share_balances"])
            == len(saved["verified"]) * len(config["wallets"][chain])
            and all(r["units"] is not None for r in saved["share_balances"])
            and not saved.get("failures")
            and saved.get("block") == block
            and saved.get("specs") == vaults
        ):
            results[chain] = saved
            continue
        verified = []
        failures = {}
        for spec in vaults:
            try:
                raw = rpc(
                    chain,
                    "eth_call",
                    [
                        {"to": spec["address"], "data": "0x" + keccak256(b"asset()")[:4].hex()},
                        hex(block),
                    ],
                )
                if len(raw) != 66 or address(raw) != spec["asset"]:
                    raise ValueError("ERC4626 asset mismatch")
                verified.append(spec)
            except Exception:
                failures[spec["venue"]] = (
                    "Asset getter unavailable or differs from configured cash asset"
                )
        prior = saved if saved and all(v in saved.get("verified", []) for v in verified) else None
        for candidate in (
            [] if prior else sorted(root.glob(f"{revision}-{chain}-*.json.gz"), reverse=True)
        ):
            p = read_capture(candidate)
            if p and p.get("status") == "available" and p.get("specs") == vaults:
                prior = p
                break
        prior = prior or durable_capture(revision, chain)
        history = [e for e in (prior or {}).get("events", []) if e["block"] <= block]
        start = min(block, (prior or {}).get("block", -1)) + 1
        # Only reuse history whose queried contract perimeter is unchanged.
        if prior and not all(v in prior.get("verified", []) for v in verified):
            history = []
            start = 0
        # A complete later history is valid for an earlier subset of deployed
        # vaults; filter the extra contracts instead of rereading genesis.
        history = [e for e in history if e["address"] in {v["address"] for v in verified}]
        if not prior:
            start = 0
        try:
            if progress:
                progress(chain, "history-from-" + str(start))
            if start <= block and verified:
                owners = [topic(a) for a in config["wallets"][chain]]
                contracts = [v["address"] for v in verified]
                queries = [
                    {"address": contracts, "topics": [[TRANSFER], owners]},
                    {"address": contracts, "topics": [[TRANSFER], [], owners]},
                    {"address": contracts, "topics": [[DEPOSIT], [], owners]},
                    {"address": contracts, "topics": [[WITHDRAW], [], [], owners]},
                ]
                history.extend(
                    event(r, chain)
                    for r in hypersync.query_logs(
                        chain,
                        queries,
                        start,
                        block,
                        log_fields=[*_DEFAULT_LOG_FIELDS, "transaction_hash"],
                        max_num_logs=100000,
                    ).rows
                )
            history = list({(e["block"], e["log_index"]): e for e in history}.values())
            fee_rows = fee_evidence.collect(chain, block, evidence["cutoff"], config, root)
            related = {
                (e["block"], e["tx_hash"], e["address"])
                for e in history
                if e["topic0"] == TRANSFER and address(e["topic1"]) == ZERO
            }
            fee_rows = [r for r in fee_rows if (r["block"], r["tx_hash"], r["address"]) in related]
            result = {
                "share_balances": share_balances(chain, verified, config["wallets"][chain], block),
                "fee_events": fee_rows,
                "status": "available",
                "chain": chain,
                "block": block,
                "specs": vaults,
                "verified": verified,
                "failures": failures,
                "events": sorted(history, key=lambda e: (e["block"], e["log_index"])),
            }
        except Exception:
            result = {
                "status": "unavailable",
                "reason": "Allocation history collection failed",
                "chain": chain,
            }
        if result["status"] == "available":
            write_capture(path, result)
            durable_capture(revision, chain, evidence["cutoff"], document=result)
        results[chain] = result
        if progress:
            progress(chain, len(result.get("events", [])))
    return {
        "schema_version": SCHEMA,
        "configuration_revision": config["revision"],
        "cutoff": evidence["cutoff"],
        "chains": results,
    }


def principal_splits(supplement, config, evidence):
    """Carry owned-share cost basis; unsupported external receipts fail closed per vault."""
    result = {}
    failures = {}
    owners_by_chain = config["wallets"]
    if not supplement:
        return result, {"source": "ERC4626 history supplement not collected"}
    if (
        supplement.get("schema_version") != SCHEMA
        or supplement.get("configuration_revision") != config["revision"]
        or supplement.get("cutoff") != evidence["cutoff"]
    ):
        raise ValueError("Accuracy supplement scope differs")
    with localcontext() as ctx:
        ctx.prec = 100
        for chain, source in supplement["chains"].items():
            if source["status"] != "available":
                failures[chain] = "Allocation history unavailable"
                continue
            if source["block"] != evidence["chains"][chain]["pins"]["closing"]["block"]:
                raise ValueError("Accuracy block differs")
            failures.update({chain + ":" + k: v for k, v in source["failures"].items()})
            fees_by_tx = defaultdict(list)
            for fee in source.get("fee_events", []):
                fees_by_tx[fee["tx_hash"]].append(fee)
            permitted = specs(config)
            if any(spec not in permitted for spec in source["verified"]) or len(
                {spec["address"] for spec in source["verified"]}
            ) != len(source["verified"]):
                raise ValueError("Unconfigured allocation vault evidence")
            for spec in source["verified"]:
                owner_set = owners_by_chain[chain]
                book = defaultdict(lambda: [Decimal(0), Decimal(0)])
                local = {}
                seen = set()
                try:
                    rows = sorted(
                        (e for e in source["events"] if e["address"] == spec["address"]),
                        key=lambda e: (e["block"], e["log_index"]),
                    )
                    closing = {owner: 0 for owner in owner_set}
                    for move in rows:
                        if move["topic0"] == TRANSFER:
                            units = int(move["data"], 16)
                            sender, receiver = address(move["topic1"]), address(move["topic2"])
                            if sender in closing:
                                closing[sender] -= units
                            if receiver in closing:
                                closing[receiver] += units
                    balances = [
                        r for r in source.get("share_balances", []) if r["vault"] == spec["address"]
                    ]
                    if (
                        len(balances) != len(owner_set)
                        or {r["owner"] for r in balances} != set(owner_set)
                        or any(
                            r["units"] is None or int(r["units"]) != closing[r["owner"]]
                            for r in balances
                        )
                    ):
                        failures[chain + ":" + spec["venue"]] = (
                            "Historical transfers do not reconcile to independent closing share balances"
                        )
                        continue
                    history_digest = digest(
                        {"owned_events": rows, "fee_proofs": source.get("fee_events", [])}
                    )
                    by_tx = defaultdict(list)
                    for row in rows:
                        by_tx[row["tx_hash"]].append(row)
                    fee_ids = {}
                    for e in rows:
                        if (
                            e["topic0"] == TRANSFER
                            and address(e["topic1"]) == ZERO
                            and int(e["data"], 16) > 0
                            and fee_evidence.verified_mint(chain, e, fees_by_tx[e["tx_hash"]])
                        ):
                            fee_ids[(e["tx_hash"], e["log_index"])] = e
                    for e in rows:
                        if e["chain"] != chain or not 0 <= e["block"] <= source["block"]:
                            raise ValueError("Allocation event outside certified scope")
                        identity = f"{chain}:{e['tx_hash']}:{e['log_index']}"
                        if identity in seen:
                            raise ValueError("Duplicate allocation event")
                        seen.add(identity)
                        if e["topic0"] == TRANSFER and not any(
                            address(e[t]) in owner_set for t in ["topic1", "topic2"]
                        ):
                            raise ValueError("Share transfer has no owned participant")
                        if (e["tx_hash"], e["log_index"]) in fee_ids:
                            # Authenticated fee shares were received without a
                            # cash subscription; their cash capital basis is zero.
                            book[address(e["topic2"])][0] += Decimal(int(e["data"], 16))
                            continue
                        kind = e["topic0"]
                        peers = by_tx[e["tx_hash"]]
                        if kind == TRANSFER:
                            sender, receiver = address(e["topic1"]), address(e["topic2"])
                            units = int(e["data"], 16)
                            if units and ZERO in (sender, receiver):
                                semantic = DEPOSIT if sender == ZERO else WITHDRAW
                                owner = receiver if sender == ZERO else sender
                                semantic_peers = [
                                    r
                                    for r in peers
                                    if r["topic0"] == semantic
                                    and address(r["topic2"] if semantic == DEPOSIT else r["topic3"])
                                    == owner
                                    and len(r["data"]) == 130
                                    and int(r["data"][66:], 16) == units
                                ]
                                mint_peers = [
                                    r
                                    for r in peers
                                    if r["topic0"] == TRANSFER
                                    and (r["tx_hash"], r["log_index"]) not in fee_ids
                                    and r["topic1"] == e["topic1"]
                                    and r["topic2"] == e["topic2"]
                                    and r["data"] == e["data"]
                                ]
                                if len(semantic_peers) != len(mint_peers):
                                    raise ValueError(
                                        "Unmatched share mint/burn; fee or missing semantic history requires attribution"
                                    )
                        elif kind in (DEPOSIT, WITHDRAW):
                            if len(e["data"]) != 130:
                                raise ValueError("Malformed allocation semantic event")
                            shares = int(e["data"][66:], 16)
                            owner = address(e["topic2"] if kind == DEPOSIT else e["topic3"])
                            if shares:
                                semantics = [
                                    r
                                    for r in peers
                                    if r["topic0"] == kind
                                    and len(r["data"]) == 130
                                    and int(r["data"][66:], 16) == shares
                                    and address(r["topic2"] if kind == DEPOSIT else r["topic3"])
                                    == owner
                                ]
                                mints = [
                                    r
                                    for r in peers
                                    if r["topic0"] == TRANSFER
                                    and (r["tx_hash"], r["log_index"]) not in fee_ids
                                    and len(r["data"]) == 66
                                    and int(r["data"], 16) == shares
                                    and address(r["topic1"] if kind == DEPOSIT else r["topic2"])
                                    == ZERO
                                    and address(r["topic2"] if kind == DEPOSIT else r["topic1"])
                                    == owner
                                ]
                                if len(semantics) != len(mints):
                                    raise ValueError(
                                        "Allocation semantic event lacks matching share mint/burn history"
                                    )
                        words = [
                            Decimal(int(e["data"][i : i + 64], 16))
                            for i in range(2, len(e["data"]), 64)
                        ]
                        if e["topic0"] == TRANSFER:
                            sender, receiver = address(e["topic1"]), address(e["topic2"])
                            if ZERO in (sender, receiver):
                                continue
                            if sender not in owner_set:
                                raise ValueError(
                                    "Incoming external shares lack a verified acquisition cost"
                                )
                            units = words[0]
                            shares, cost = book[sender]
                            if units > shares:
                                raise ValueError("Share transfer lacks subscription history")
                            carried = cost * units / shares if shares else Decimal(0)
                            book[sender] = [shares - units, cost - carried]
                            if receiver in owner_set:
                                book[receiver][0] += units
                                book[receiver][1] += carried
                        elif e["topic0"] == DEPOSIT:
                            if len(words) != 2 or address(e["topic2"]) not in owner_set:
                                raise ValueError("Invalid owned Deposit")
                            assets, shares = words
                            owner = address(e["topic2"])
                            book[owner][0] += shares
                            book[owner][1] += assets
                        elif e["topic0"] == WITHDRAW:
                            if len(words) != 2:
                                raise ValueError("Invalid Withdraw")
                            owner = address(e["topic3"])
                            receiver = address(e["topic2"])
                            assets, burned = words
                            if not burned and not assets:
                                continue
                            shares, cost = book[owner]
                            if not shares or burned > shares:
                                raise ValueError("Redemption lacks subscription history")
                            basis = cost * burned / shares
                            book[owner] = [shares - burned, cost - basis]
                            principal = min(assets, basis).to_integral_value(rounding=ROUND_DOWN)
                            if receiver in owner_set:
                                local[identity] = {
                                    **e,
                                    "vault": spec["address"],
                                    "asset": spec["asset"],
                                    "decimals": spec["decimals"],
                                    "receiver": receiver,
                                    "owner": owner,
                                    "assets_units": str(assets),
                                    "principal_units": str(principal),
                                    "interest_units": str(assets - principal),
                                    "basis_loss_units": str(max(basis - assets, Decimal(0))),
                                    "history_digest": history_digest,
                                }
                    result.update(local)
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    # Later unknown acquisitions cannot invalidate earlier,
                    # independently verified redemptions. Never promote beyond
                    # the first incomplete basis witness.
                    result.update(local)
                    failures[chain + ":" + spec["venue"]] = str(exc)
    return result, failures


def share_balances(chain, vaults, owners, block):
    """Independent closing share reads, batched without exposing provider URLs."""
    import os
    from concurrent.futures import ThreadPoolExecutor

    import requests

    from .collector import RPC_KEYS

    pairs = [(v["address"], owner) for v in vaults for owner in owners]

    def batch(items):
        payload = [
            {
                "jsonrpc": "2.0",
                "id": i,
                "method": "eth_call",
                "params": [{"to": v, "data": "0x70a08231" + o[2:].zfill(64)}, hex(block)],
            }
            for i, (v, o) in enumerate(items)
        ]
        values = {}
        try:
            response = requests.post(os.environ[RPC_KEYS[chain]], json=payload, timeout=30)
            raw = response.json()
            if isinstance(raw, list):
                values = {
                    r["id"]: r.get("result") for r in raw if isinstance(r, dict) and "id" in r
                }
        except Exception:
            pass
        result = []
        for i, (v, o) in enumerate(items):
            try:
                raw = values.get(i)
                if not isinstance(raw, str) or len(raw) != 66:
                    raw = rpc(
                        chain,
                        "eth_call",
                        [{"to": v, "data": "0x70a08231" + o[2:].zfill(64)}, hex(block)],
                    )
                units = str(int(raw, 16)) if isinstance(raw, str) and len(raw) == 66 else None
            except Exception:
                units = None
            result.append({"vault": v, "owner": o, "units": units})
        return result

    chunks = [pairs[i : i + 40] for i in range(0, len(pairs), 40)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        return [r for chunk in pool.map(batch, chunks) for r in chunk]
