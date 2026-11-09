"""Reporting-only treasury vault accruals and independently funded cash splits."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext

from ..extract.cache import cached
from ..extract.hypersync import _DEFAULT_LOG_FIELDS
from ..extract.hypersync_store import fetch_logs
from .cash_flow.accuracy import SCHEMA, principal_splits
from .cash_flow.collector import DEPOSIT, TRANSFER, WITHDRAW, event, rpc
from .cash_flow.config import load
from .cash_flow.liability import ZERO, address
from .spark_onchain import topic
from .verification import digest

VAULT = "0xbeef3f3a04e28895f3d5163d910474901981183d"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
VENUE = "TREASURY_STEAKHOUSE_USDC"


@cached(source_id="spark-reporting.steakhouse-receipt.v1")
def receipt(tx_hash):
    return rpc("ethereum", "eth_getTransactionReceipt", [tx_hash])


def collect(source):
    config = load()
    owners = sorted(config["wallets"]["ethereum"])
    opening, closing = source.opening_pins["ethereum"], source.closing_pins["ethereum"]
    queries = [
        {"address": [VAULT], "topics": [[TRANSFER], [topic(a) for a in owners]]},
        {"address": [VAULT], "topics": [[TRANSFER], [], [topic(a) for a in owners]]},
        {"address": [VAULT], "topics": [[DEPOSIT], [], [topic(a) for a in owners]]},
        {"address": [VAULT], "topics": [[WITHDRAW], [], [], [topic(a) for a in owners]]},
    ]
    rows = [
        event(r, "ethereum")
        for r in fetch_logs(
            "ethereum",
            queries,
            0,
            closing,
            log_fields=[*_DEFAULT_LOG_FIELDS, "transaction_hash"],
            max_num_logs=100000,
        )
    ]
    unique = {}
    for row in rows:
        key = row["block"], row["log_index"]
        if key in unique and unique[key] != row:
            raise ValueError("Conflicting Steakhouse share history")
        unique[key] = row
    history = sorted(unique.values(), key=lambda r: (r["block"], r["log_index"]))

    def boundary(block):
        deployed = source.code("ethereum", VAULT, block) != "0x"
        if deployed and address(source.call("ethereum", VAULT, "asset()", block)) != USDC:
            raise ValueError("Steakhouse asset getter differs")

        def position(owner):
            units = (
                source.number("ethereum", VAULT, "balanceOf(address)", block, int(owner, 16))
                if deployed
                else 0
            )
            assets = (
                source.number("ethereum", VAULT, "previewRedeem(uint256)", block, units)
                if units
                else 0
            )
            return dict(owner=owner, shares_units=str(units), assets_units=str(assets))

        with ThreadPoolExecutor(max_workers=6) as pool:
            positions = list(pool.map(position, owners))
        return dict(
            block=block,
            deployed=deployed,
            asset=USDC if deployed else None,
            quote_method="previewRedeem(uint256)",
            positions=positions,
        )

    # Authenticate actual cash legs, including deposits routed through a bundler.
    cash = []
    for semantic in history:
        if semantic["topic0"] not in (DEPOSIT, WITHDRAW):
            continue
        tx = receipt(semantic["tx_hash"])
        logs = tx.get("logs", [])
        witness = [
            r
            for r in logs
            if int(r["logIndex"], 16) == semantic["log_index"]
            and r["address"].lower() == VAULT
            and r["data"] == semantic["data"]
            and r["topics"]
            == [semantic[k] for k in ("topic0", "topic1", "topic2", "topic3") if semantic.get(k)]
        ]
        if (
            tx.get("status") != "0x1"
            or tx.get("transactionHash") != semantic["tx_hash"]
            or int(tx["blockNumber"], 16) != semantic["block"]
            or len(witness) != 1
        ):
            raise ValueError("Steakhouse semantic receipt differs")
        units = int(semantic["data"][2:66], 16)
        deposit = semantic["topic0"] == DEPOSIT
        matches = [
            r
            for r in logs
            if r["address"].lower() == USDC
            and r["topics"]
            and r["topics"][0] == TRANSFER
            and int(r["data"], 16) == units
            and (
                address(r["topics"][1]) in owners and address(r["topics"][2]) not in owners
                if deposit
                else address(r["topics"][1]) == VAULT
                and address(r["topics"][2]) == address(semantic["topic2"]) in owners
            )
        ]
        if len(matches) != 1:
            raise ValueError("Steakhouse capital movement lacks one owned cash leg")
        raw = matches[0]
        cash.append(
            dict(
                chain="ethereum",
                block=semantic["block"],
                timestamp=semantic["timestamp"],
                tx_hash=semantic["tx_hash"],
                log_index=int(raw["logIndex"], 16),
                token=USDC,
                **{"from": address(raw["topics"][1]), "to": address(raw["topics"][2])},
                amount_usd=str(Decimal(units) / 10**6 * (-1 if deposit else 1)),
                semantic_log_index=semantic["log_index"],
                direction="subscription" if deposit else "redemption",
            )
        )
    proof = dict(
        schema_version="spark-steakhouse-accrual.v1",
        period_start=str(source.period.start),
        period_end=str(source.period.end),
        configuration_revision=config["revision"],
        ownership_scope=owners,
        history=history,
        opening=boundary(opening),
        closing=boundary(closing),
        cash_events=cash,
    )
    return project(proof, config)


def project(proof, config=None):
    config = config or load()
    owners = proof["ownership_scope"]
    if (
        proof["schema_version"] != "spark-steakhouse-accrual.v1"
        or not owners
        or len(set(owners)) != len(owners)
        or not set(owners) <= set(config["wallets"]["ethereum"])
    ):
        raise ValueError("Invalid Steakhouse ownership scope")
    config = deepcopy(config)
    config["revision"] = proof["configuration_revision"]
    config["wallets"]["ethereum"] = {a: config["wallets"]["ethereum"][a] for a in owners}
    spec = next(r for r in config["reporting_vaults"] if r["venue"] == VENUE)
    first, last = date.fromisoformat(proof["period_start"]), date.fromisoformat(proof["period_end"])
    if first != last.replace(day=1):
        raise ValueError("Invalid Steakhouse month scope")
    opening, closing = proof["opening"], proof["closing"]
    if not 0 <= opening["block"] < closing["block"]:
        raise ValueError("Invalid Steakhouse boundary pins")
    history = proof["history"]
    identities = {(e["tx_hash"], e["log_index"]) for e in history}
    if len(identities) != len(history) or any(
        e["chain"] != "ethereum"
        or e["address"] != VAULT
        or e["topic0"] not in (TRANSFER, DEPOSIT, WITHDRAW)
        or not 0 <= e["block"] <= closing["block"]
        for e in history
    ):
        raise ValueError("Steakhouse history is duplicated or outside its vault/pin scope")
    with localcontext() as ctx:
        ctx.prec = 100
        marked = {}
        for name, boundary in (("opening", opening), ("closing", closing)):
            states = boundary["positions"]
            if (
                len(states) != len(owners)
                or {r["owner"] for r in states} != set(owners)
                or boundary["quote_method"] != "previewRedeem(uint256)"
                or (
                    boundary["asset"] != USDC
                    if boundary["deployed"]
                    else any(int(r["shares_units"]) for r in states)
                )
            ):
                raise ValueError("Incomplete Steakhouse position marks")
            balances = defaultdict(int)
            for e in history:
                if e["topic0"] == TRANSFER and e["block"] <= boundary["block"]:
                    balances[address(e["topic1"])] -= int(e["data"], 16)
                    balances[address(e["topic2"])] += int(e["data"], 16)
            if any(
                int(r["shares_units"]) != balances[r["owner"]]
                or int(r["shares_units"]) < 0
                or int(r["assets_units"]) < 0
                or (not int(r["shares_units"]) and int(r["assets_units"]))
                for r in states
            ):
                raise ValueError(
                    "Steakhouse shares do not reconcile to independent boundary balances"
                )
            marked[name] = sum(Decimal(r["assets_units"]) for r in states)
        extra = dict(
            schema_version=SCHEMA,
            configuration_revision=config["revision"],
            cutoff=str(last),
            chains={
                "ethereum": dict(
                    status="available",
                    block=closing["block"],
                    verified=[spec],
                    failures={},
                    events=history,
                    share_balances=[
                        dict(vault=VAULT, owner=r["owner"], units=r["shares_units"])
                        for r in closing["positions"]
                    ],
                    fee_events=[],
                )
            },
        )
        evidence = dict(
            cutoff=str(last),
            chains={"ethereum": dict(pins={"closing": dict(block=closing["block"])})},
        )
        splits, failures = principal_splits(extra, config, evidence)
        if failures:
            raise ValueError("Steakhouse subscription basis history is incomplete")
        net_capital = Decimal(0)
        semantics = {}
        for e in history:
            if (
                e["topic0"] == TRANSFER
                and ZERO not in (address(e["topic1"]), address(e["topic2"]))
                and ((address(e["topic1"]) in owners) != (address(e["topic2"]) in owners))
            ):
                raise ValueError(
                    "Steakhouse external share transfer needs acquisition/disposal valuation"
                )
            if e["topic0"] in (DEPOSIT, WITHDRAW):
                semantics[(e["tx_hash"], e["log_index"])] = e
                if opening["block"] < e["block"] <= closing["block"]:
                    day = datetime.fromtimestamp(e["timestamp"], UTC).date()
                    if not first <= day <= last:
                        raise ValueError("Steakhouse semantic timestamp differs from pinned month")
                    assets = Decimal(int(e["data"][2:66], 16))
                    net_capital += assets * (1 if e["topic0"] == DEPOSIT else -1)
        cash_events, used, used_semantics = [], set(), set()
        for cash in proof["cash_events"]:
            semantic = semantics.get((cash["tx_hash"], cash["semantic_log_index"]))
            key = (cash["tx_hash"], cash["log_index"])
            deposit = cash["direction"] == "subscription"
            if (
                not semantic
                or key in used
                or (cash["tx_hash"], cash["semantic_log_index"]) in used_semantics
                or cash["chain"] != "ethereum"
                or cash["token"] != USDC
                or cash["direction"] not in ("subscription", "redemption")
                or cash["block"] != semantic["block"]
                or cash["timestamp"] != semantic["timestamp"]
                or Decimal(cash["amount_usd"])
                != Decimal(int(semantic["data"][2:66], 16)) / 10**6 * (-1 if deposit else 1)
                or (
                    deposit
                    and (
                        semantic["topic0"] != DEPOSIT
                        or cash["from"] not in owners
                        or cash["to"] in owners
                    )
                )
                or (
                    not deposit
                    and (
                        semantic["topic0"] != WITHDRAW
                        or cash["from"] != VAULT
                        or cash["to"] != address(semantic["topic2"])
                        or cash["to"] not in owners
                    )
                )
            ):
                raise ValueError("Invalid Steakhouse cash funding witness")
            used.add(key)
            used_semantics.add((cash["tx_hash"], cash["semantic_log_index"]))
            if not opening["block"] < cash["block"] <= closing["block"]:
                continue
            parts = [
                dict(
                    line_id="allocation-changes",
                    amount_usd=cash["amount_usd"],
                    suffix="subscription",
                )
            ]
            if not deposit:
                split = splits.get(f"ethereum:{cash['tx_hash']}:{cash['semantic_log_index']}")
                if split is None:
                    raise ValueError("Steakhouse redemption has no verified subscription basis")
                parts = [
                    dict(
                        line_id="allocation-changes",
                        amount_usd=str(Decimal(split["principal_units"]) / 10**6),
                        suffix="vault-principal",
                    )
                ]
                if Decimal(split["interest_units"]):
                    parts.append(
                        dict(
                            line_id="external-receipts",
                            amount_usd=str(Decimal(split["interest_units"]) / 10**6),
                            suffix="vault-interest",
                        )
                    )
            cash_events.append(dict(cash, parts=parts, history_digest=digest(history)))
        if {(r["tx_hash"], r["semantic_log_index"]) for r in proof["cash_events"]} != set(
            semantics
        ):
            raise ValueError("Steakhouse capital events lack cash subscription/redemption proof")
        earned = (marked["closing"] - marked["opening"] - net_capital) / 10**6
        return dict(
            status="available",
            quality="supported",
            recognition="accrual",
            value=str(earned),
            basis="ERC4626_net_claim_change_less_verified_capital_flows",
            period_end=str(last),
            opening_value=str(marked["opening"] / 10**6),
            closing_value=str(marked["closing"] / 10**6),
            net_capital=str(net_capital / 10**6),
            cash_events=cash_events,
            proof=proof,
            proof_hash=digest(proof),
        )


def validate_document(document):
    raw = document.get("steakhouse")
    if not raw or raw.get("status") == "unavailable":
        return
    if raw.get("status") not in ("available", "partial") or raw.get("proof_hash") != digest(
        raw["proof"]
    ):
        raise ValueError("Invalid Steakhouse source proof")
    if (
        raw["proof"]["period_start"] != document["period_start"]
        or not document["period_start"] <= raw["period_end"] <= document["cutoff"]
    ):
        raise ValueError("Steakhouse retained period differs")
    pins = document.get("opening_pins", {}), document.get("closing_pins", {})
    if (
        raw["proof"]["period_end"] != raw["period_end"]
        or raw["proof"]["opening"]["block"] != pins[0].get("ethereum")
        or raw["proof"]["closing"]["block"] > pins[1].get("ethereum", -1)
        or (
            raw["status"] == "available"
            and raw["proof"]["closing"]["block"] != pins[1].get("ethereum")
        )
    ):
        raise ValueError("Steakhouse source pins differ from publication")
    expected = project(raw["proof"])
    if any(
        raw.get(key) != expected[key]
        for key in ("value", "opening_value", "closing_value", "net_capital", "cash_events")
    ):
        raise ValueError("Steakhouse income or cash split differs from its evidence")
