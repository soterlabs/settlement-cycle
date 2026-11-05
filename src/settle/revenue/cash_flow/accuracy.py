"""Additional retained ERC4626 evidence; no assumption that a redemption is all principal."""

import gzip
import json
from collections import defaultdict
from decimal import ROUND_DOWN, Decimal, localcontext
from pathlib import Path

from ...domain.config import load_prime_by_id
from ...extract import hypersync
from ...extract._keccak import keccak256
from ...extract.hypersync import _DEFAULT_LOG_FIELDS
from ..spark_onchain import topic
from ..verification import digest
from .collector import DEPOSIT, TRANSFER, WITHDRAW, durable_capture, event, rpc
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
    ]


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
        saved = durable_capture(revision, chain, evidence["cutoff"])
        if path.exists():
            with gzip.open(path, "rt") as f:
                saved = json.load(f)
        if (
            saved
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
        prior = None
        for candidate in sorted(root.glob(f"{revision}-{chain}-*.json.gz"), reverse=True):
            with gzip.open(candidate, "rt") as f:
                p = json.load(f)
            if p.get("status") == "available" and p.get("specs") == vaults:
                prior = p
                break
        prior = prior or durable_capture(revision, chain)
        history = [e for e in (prior or {}).get("events", []) if e["block"] <= block]
        start = min(block, (prior or {}).get("block", -1)) + 1
        # Only reuse history whose queried contract perimeter is unchanged.
        if prior and prior.get("verified") != verified:
            history = []
            start = 0
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
            result = {
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
            with gzip.open(path, "wt") as f:
                json.dump(result, f, separators=(",", ":"))
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
                    for e in source["events"]:
                        if e["address"] != spec["address"]:
                            continue
                        if e["chain"] != chain or e["block"] > source["block"]:
                            raise ValueError("Allocation event outside scope")
                        identity = f"{chain}:{e['tx_hash']}:{e['log_index']}"
                        if identity in seen:
                            raise ValueError("Duplicate allocation event")
                        seen.add(identity)
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
                                }
                    result.update(local)
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    failures[chain + ":" + spec["venue"]] = str(exc)
    return result, failures
