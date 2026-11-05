"""Classify observed cash movements; unknown counterparties remain explicit.

No balancing plugs and no assumption that every unrecognised payment is an
allocation. All amounts originate in indexed Transfer/Deposit/Withdraw logs.
"""

import json
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from importlib.resources import files

from ...domain.config import load_prime_by_id
from .accuracy import principal_splits
from .collector import TRANSFER
from .config import load
from .liability import ZERO, address
from .liability import split as savings_split
from .model import metric, total

CASH_LEAVES = (
    "sky-allocation-receipts",
    "sky-demand-receipts",
    "external-receipts",
    "expenses-paid",
    "allocation-changes",
    "investments",
    "usds-drawn",
    "deposits",
    "sky-contributions",
    "buybacks",
)


def is_cash(config, chain, token, owner):
    meta = config["tokens"][chain].get(token)
    role = config["wallets"][chain].get(owner)
    return bool(
        meta
        and role
        and (
            meta["cash"]
            or (meta["symbol"] in ("PYUSD", "USDG") and role["role"] in ("savings_idle", "intents"))
        )
    )


def collect_ledger(evidence, *, config=None):
    config = config or load()
    entries = []
    seen = set()
    errors = []
    settled = set()
    savings = savings_split(
        [r for c in evidence["chains"].values() for r in c.get("savings_events", [])],
        config["savings"],
    )
    savings_index = defaultdict(list)
    for e in savings["events"]:
        savings_index[(e["chain"], e["tx_hash"], e["vault"])].append(e)
    redemptions, allocation_failures = principal_splits(evidence.get("accuracy"), config, evidence)
    redemption_index = defaultdict(list)
    for item in redemptions.values():
        redemption_index[(item["chain"], item["tx_hash"], item["vault"])].append(item)
    used_redemptions = set()
    settlements = json.loads(files(__package__).joinpath("settlements.json").read_text())["records"]
    by_date = {r["execution_date"]: r for r in settlements}
    prime = load_prime_by_id("spark")
    loan_rules = []
    for _chain, payers in prime.principal_return_overrides.items():
        for payer, rules in payers.items():
            if payer.hex.lower() == "0x49506c3aa028693458d6ee816b2ec28522946872":
                loan_rules = list(rules)

    # One explicit underlying cash leg can be partitioned, but never consumed
    # twice. IDs retain the original log and a component suffix.
    def append(e, line, value, *, quality="supported", reason=None, suffix="cash"):
        identity = f"{e['chain']}:{e['tx_hash']}:{e['log_index']}:{suffix}"
        if identity in seen:
            raise ValueError("Duplicate cash-flow evidence")
        seen.add(identity)
        entries.append(
            {
                "id": identity,
                "source_event_id": f"{e['chain']}:{e['tx_hash']}:{e['log_index']}",
                "chain": e["chain"],
                "block": e["block"],
                "log_index": e["log_index"],
                "tx_hash": e["tx_hash"],
                "timestamp": e["timestamp"],
                "date": datetime.fromtimestamp(e["timestamp"], UTC).date().isoformat(),
                "token": e["address"],
                "from": address(e["topic1"]),
                "to": address(e["topic2"]),
                "line_id": line,
                "amount_usd": str(value),
                "quality": quality,
                "reason": reason,
            }
        )

    with localcontext() as ctx:
        ctx.prec = 100
        for chain, source in sorted(evidence["chains"].items()):
            wallets = config["wallets"][chain]
            tokens = config["tokens"][chain]
            counter = config["counterparties"][chain]
            rows = source.get("events", [])
            by_tx = defaultdict(list)
            for e in rows:
                by_tx[e["tx_hash"]].append(e)
            loan_daily = defaultdict(Decimal)
            for e in rows:
                if (
                    e["topic0"] == TRANSFER
                    and address(e["topic1"]) == "0x49506c3aa028693458d6ee816b2ec28522946872"
                    and address(e["topic2"]) in wallets
                    and e["address"] in tokens
                    and tokens[e["address"]]["symbol"] == "USDC"
                ):
                    loan_daily[datetime.fromtimestamp(e["timestamp"], UTC).date()] += (
                        Decimal(int(e["data"], 16)) / 10 ** tokens[e["address"]]["decimals"]
                    )
            loan_remaining = {}
            loan_verified = {}
            for day, received in loan_daily.items():
                matches = [r for r in loan_rules if r.date == day and abs(r.amount - received) <= 1]
                if len(matches) > 1:
                    raise ValueError("Ambiguous Anchorage split")
                loan_verified[day] = bool(
                    matches
                    and matches[0].capital_amount is not None
                    and matches[0].amount == received
                )
                loan_remaining[day] = (
                    (
                        matches[0].capital_amount
                        if matches[0].capital_amount is not None
                        else received
                    )
                    if matches
                    else Decimal(0)
                )
            for e in sorted(rows, key=lambda r: (r["block"], r["log_index"])):
                if e["topic0"] != TRANSFER or len(e["data"]) != 66 or e["address"] not in tokens:
                    continue
                src, dst = address(e["topic1"]), address(e["topic2"])
                meta = tokens[e["address"]]
                if src == dst:
                    continue
                units = Decimal(int(e["data"], 16))
                value = units / 10 ** meta["decimals"]
                if value == 0:
                    continue
                # Direct issuer rewards are cash under the agreed operational
                # reward-balance policy; principal remains an allocation.
                if (
                    src in config["reward_payers"].get(chain, {})
                    and dst in wallets
                    and meta["symbol"] == config["reward_payers"][chain][src]
                ):
                    append(
                        e,
                        "external-receipts",
                        value,
                        reason="Verified issuer reward payer; reward cash balance tracked separately",
                    )
                    continue
                cash_from, cash_to = (
                    is_cash(config, chain, e["address"], src),
                    is_cash(config, chain, e["address"], dst),
                )
                if cash_from == cash_to:
                    continue
                # A relay between two owned cash accounts is internal only when
                # both exact token/amount legs share the same intermediary and tx.
                relay = dst if cash_from else src
                pair = [
                    other
                    for other in by_tx[e["tx_hash"]]
                    if other["log_index"] != e["log_index"]
                    and other["topic0"] == TRANSFER
                    and other["address"] == e["address"]
                    and other["data"] == e["data"]
                    and (
                        (
                            cash_from
                            and address(other["topic1"]) == relay
                            and is_cash(config, chain, e["address"], address(other["topic2"]))
                        )
                        or (
                            cash_to
                            and address(other["topic2"]) == relay
                            and is_cash(config, chain, e["address"], address(other["topic1"]))
                        )
                    )
                ]
                if relay != ZERO and len(pair) == 1:
                    # Require uniqueness in both directions, not a many-to-one
                    # amount match that could reuse one receipt twice.
                    same = [
                        other
                        for other in by_tx[e["tx_hash"]]
                        if other["topic0"] == TRANSFER
                        and other["address"] == e["address"]
                        and other["data"] == e["data"]
                        and (
                            address(other["topic2"]) == relay
                            if cash_from
                            else address(other["topic1"]) == relay
                        )
                        and is_cash(
                            config,
                            chain,
                            e["address"],
                            address(other["topic1"]) if cash_from else address(other["topic2"]),
                        )
                    ]
                    if len(same) == 1:
                        append(
                            e,
                            "internal-routing",
                            value * (-1 if cash_from else 1),
                            reason="Exact same-token cash relay between owned accounts in one transaction",
                        )
                        continue
                sign = 1 if cash_to else -1
                other = src if sign == 1 else dst
                owner = dst if sign == 1 else src
                day = datetime.fromtimestamp(e["timestamp"], UTC).date()
                rule = counter.get(other, {})
                if other == ZERO and meta["cash"]:
                    opposite = []
                    for sibling in by_tx[e["tx_hash"]]:
                        if (
                            sibling["address"] not in tokens
                            or sibling["log_index"] == e["log_index"]
                            or sibling["topic0"] != TRANSFER
                        ):
                            continue
                        sf, st = address(sibling["topic1"]), address(sibling["topic2"])
                        sm = tokens[sibling["address"]]
                        sc_from, sc_to = (
                            is_cash(config, chain, sibling["address"], sf),
                            is_cash(config, chain, sibling["address"], st),
                        )
                        if sc_from == sc_to:
                            continue
                        ss = 1 if sc_to else -1
                        cp = sf if ss == 1 else st
                        if (
                            ss == -sign
                            and counter.get(cp, {}).get("kind") == "conversion"
                            and Decimal(int(sibling["data"], 16)) / 10 ** sm["decimals"] == value
                        ):
                            opposite.append(sibling)
                    if len(opposite) == 1:
                        append(
                            e,
                            "internal-routing",
                            value * sign,
                            reason="Cash-to-cash converter mint/burn matches the opposite cash leg in the same transaction",
                        )
                        continue

                # Match Savings semantic events to the actual underlying cash
                # transfer and its external beneficiary, not just a share burn.
                vault = owner if wallets[owner]["role"] == "savings_idle" else None
                matches = (
                    [
                        v
                        for v in savings_index.get((chain, e["tx_hash"], vault), [])
                        if v["direction"] == sign
                        and Decimal(v["assets_units"]) == units
                        and (v["receiver"] == dst if sign < 0 else v["owner"] == dst)
                    ]
                    if vault
                    else []
                )
                if other in wallets:
                    append(
                        e,
                        "allocation-changes",
                        value * sign,
                        quality="estimate",
                        reason="Cash-boundary transfer between Savings idle assets and allocated issuer-stable principal; reward attribution may be mixed",
                    )
                    continue
                if wallets[owner]["role"] == "intents":
                    append(
                        e,
                        "unclassified",
                        value * sign,
                        reason="Queued Savings payout requires its request principal/interest attribution",
                    )
                    continue
                if vault:
                    # For deposits owner is the share beneficiary, not the vault;
                    # match by asset amount and consume each semantic event once.
                    matches = [
                        v
                        for v in savings_index.get((chain, e["tx_hash"], vault), [])
                        if v["direction"] == sign
                        and Decimal(v["assets_units"]) == units
                        and not v.get("_used")
                        and (sign > 0 or v["receiver"] == dst)
                    ]
                    if len(matches) == 1 and matches[0]["owner"] not in wallets:
                        item = matches[0]
                        item["_used"] = True
                        append(
                            e,
                            "deposits",
                            Decimal(item["principal_units"]) / 10 ** meta["decimals"] * sign,
                            reason="Weighted-average deposited principal follows transferred shares",
                            suffix="savings-principal",
                        )
                        if Decimal(item["interest_units"]):
                            append(
                                e,
                                "expenses-paid",
                                -Decimal(item["interest_units"]) / 10 ** meta["decimals"],
                                reason="Savings interest paid; excluded from financing principal",
                                suffix="savings-interest",
                            )
                    else:
                        append(
                            e,
                            "unclassified",
                            value * sign,
                            reason="Savings payment lacks a complete principal/interest match",
                        )
                    continue
                if (
                    chain == "ethereum"
                    and owner == "0x3300f198988e4c9c63f75df86de36421f06af8c4"
                    and src == ZERO
                    and meta["symbol"] == "USDS"
                ):
                    record = by_date.get(str(day))
                    if (
                        record
                        and str(day) not in settled
                        and record["expected_send_usds"] is not None
                        and value == Decimal(record["expected_send_usds"])
                    ):
                        settled.add(str(day))
                        demand = Decimal(record["demand_usds"])
                        append(
                            e,
                            "sky-demand-receipts",
                            demand,
                            reason=record["source_url"],
                            suffix="msc-demand",
                        )
                        append(
                            e,
                            "sky-allocation-receipts",
                            value - demand,
                            reason=record["source_url"],
                            suffix="msc-allocation",
                        )
                    else:
                        append(
                            e,
                            "unclassified",
                            value,
                            reason="USDS mint to SubProxy is not matched to a reviewed executed MSC split",
                        )
                elif rule.get("kind") == "borrowing":
                    append(
                        e,
                        "usds-drawn",
                        value * sign,
                        reason="Actual AllocatorBuffer cash movement; Sky-direct attribution separately disclosed",
                    )
                elif rule.get("kind") == "loan" and meta["symbol"] == "USDC":
                    if sign < 0:
                        append(e, "allocation-changes", -value, reason="Loan principal advanced")
                    else:
                        capital = min(value, loan_remaining.get(day, Decimal(0)))
                        loan_remaining[day] -= capital
                        if capital:
                            append(
                                e,
                                "allocation-changes",
                                capital,
                                quality="supported" if loan_verified.get(day) else "estimate",
                                reason="Reviewed explicit principal split preserves separately identified interest"
                                if loan_verified.get(day)
                                else "Registered principal override; embedded interest may be unresolved",
                                suffix="loan-principal",
                            )
                        if value - capital:
                            append(
                                e,
                                "external-receipts",
                                value - capital,
                                reason="Loan interest receipt after registered principal exclusions",
                                suffix="loan-interest",
                            )
                elif rule.get("kind") == "investment":
                    if str(day) == "2026-04-10" and sign < 0:
                        append(
                            e,
                            "excluded-immaterial",
                            -value,
                            reason="Approved immaterial Arkis follow-on exclusion; retained in reconciliation evidence",
                        )
                    else:
                        append(
                            e,
                            "investments",
                            value * sign,
                            reason="Observed investment cash payment",
                        )
                elif rule.get("kind") == "expense":
                    append(e, "expenses-paid", value * sign, reason=rule["label"])
                elif rule.get("kind") == "unclassified":
                    append(
                        e,
                        "unclassified",
                        value * sign,
                        reason="Unidentified counterparty explicitly acknowledged as unresolved",
                    )
                elif (
                    owner == "0x797b010e0babb493b8dedd6f6ce5cc72778c2bf3"
                    and sign < 0
                    and any(
                        r["address"] == "0xc20059e0317de91738d13af027dfc4a50781b066"
                        and address(r["topic2"]) in wallets
                        for r in by_tx[e["tx_hash"]]
                    )
                ):
                    append(
                        e,
                        "buybacks",
                        -value,
                        reason="Stablecoin spent in transaction receiving SPK into the owned cluster",
                    )
                elif (
                    sign > 0
                    and len(
                        matches := [
                            r
                            for r in redemption_index.get((chain, e["tx_hash"], src), [])
                            if r["asset"] == e["address"]
                            and r["receiver"] == dst
                            and Decimal(r["assets_units"]) == units
                            and (chain, r["tx_hash"], r["log_index"]) not in used_redemptions
                        ]
                    )
                    == 1
                ):
                    item = matches[0]
                    used_redemptions.add((chain, item["tx_hash"], item["log_index"]))
                    append(
                        e,
                        "allocation-changes",
                        Decimal(item["principal_units"]) / 10 ** meta["decimals"],
                        reason="Verified ERC4626 redemption; owned-share subscription cost follows internal transfers",
                        suffix="vault-principal",
                    )
                    witness = {
                        "method": "owned_share_weighted_subscription_cost",
                        "withdrawal_id": f"{chain}:{item['tx_hash']}:{item['log_index']}",
                        "history_digest": item["history_digest"],
                        "principal_units": item["principal_units"],
                        "interest_units": item["interest_units"],
                        "asset": item["asset"],
                    }
                    entries[-1]["principal_evidence"] = witness
                    if Decimal(item["interest_units"]):
                        append(
                            e,
                            "external-receipts",
                            Decimal(item["interest_units"]) / 10 ** meta["decimals"],
                            reason="ERC4626 cash yield above carried subscription principal",
                            suffix="vault-interest",
                        )
                        entries[-1]["principal_evidence"] = witness
                elif rule.get("kind") in (
                    "allocation",
                    "amm",
                    "sky_direct",
                    "allocation_conversion",
                ):
                    # A redemption includes principal and possibly income. Until
                    # its cost-basis witness is reconciled, preserve gross cash
                    # evidence without asserting that it is all principal.
                    if sign < 0:
                        append(
                            e,
                            "allocation-changes",
                            -value,
                            quality="estimate"
                            if rule["kind"] in ("amm", "sky_direct")
                            else "supported",
                            reason="Observed cash deployed; AMM/Sky-direct funding attribution pending"
                            if rule["kind"] in ("amm", "sky_direct")
                            else "Observed allocation cash subscription",
                        )
                    else:
                        append(
                            e,
                            "allocation-changes",
                            value,
                            quality="estimate",
                            reason="Observed allocation cash redemption; principal/income attribution remains provisional",
                        )
                elif rule.get("kind") in ("bridge", "conversion"):
                    append(
                        e,
                        "internal-routing",
                        value * sign,
                        quality="estimate",
                        reason="Known routing contract; cross-leg/message reconciliation pending",
                    )
                elif sign < 0 and wallets[owner]["label"] in (
                    "Spark Foundation",
                    "Spark Assets Foundation",
                    "Spark DevCo (Phoenix Labs)",
                    "Spark SPK Mid-term Treasury",
                ):
                    append(
                        e,
                        "expenses-paid",
                        -value,
                        quality="estimate",
                        reason="External operating-wallet payment under the supplied policy; payee/category unverified",
                    )
                else:
                    append(
                        e,
                        "unclassified",
                        value * sign,
                        reason="Unclassified cash movement; never inferred to be revenue or allocation capital",
                    )
    return {
        "entries": entries,
        "savings": savings,
        "errors": errors,
        "allocation_failures": allocation_failures,
        "verified_redemptions": len(used_redemptions),
    }


def cash_metrics(evidence, ledger, config=None):
    with localcontext() as ctx:
        ctx.prec = 100
        config = config or load()
        metrics = {}
        rows = ledger["entries"]
        missing = [
            c
            for c, s in evidence["chains"].items()
            if not s.get("events_complete", s.get("status") == "available")
        ]
        for key in CASH_LEAVES:
            matching = [r for r in rows if r["line_id"] == key]
            required = (
                ["ethereum"]
                if key
                in (
                    "sky-allocation-receipts",
                    "sky-demand-receipts",
                    "usds-drawn",
                    "buybacks",
                    "investments",
                )
                else sorted({v["chain"] for v in config["savings"]})
                if key == "deposits"
                else list(config["wallets"])
            )
            complete = all(ch not in missing for ch in required)
            reason = []
            if not complete:
                reason.append(
                    "Missing event coverage: " + ",".join(ch for ch in required if ch in missing)
                )
            if key in ("allocation-changes", "usds-drawn"):
                complete = False
                reason.append(
                    "Sky-direct funding attribution and redemption cost basis not fully reconciled"
                )
            if any(r["quality"] == "estimate" for r in matching):
                complete = False
                reason.append("Some transaction classifications remain provisional")
            if key == "deposits" and (
                ledger["savings"]["failures"]
                or any(
                    r["line_id"] == "unclassified" and ("Savings" in (r.get("reason") or ""))
                    for r in rows
                )
            ):
                complete = False
                reason.append("Some Savings principal/interest histories are incomplete")
            observed = any(ch not in missing for ch in required)
            if key == "sky-contributions":
                observed = False
                complete = False
                reason.append("Sky-funded direct seed attribution is not reconciled")
            metrics[key] = metric(
                sum((Decimal(r["amount_usd"]) for r in matching), Decimal(0)) if observed else None,
                complete=complete,
                quality="estimate"
                if any(r["quality"] == "estimate" for r in matching)
                else "supported",
                reason="; ".join(reason) or None,
                refs=["event_ledger." + key],
            )
            metrics[key]["event_count"] = len(matching)
            estimates = defaultdict(list)
            for row in matching:
                if row["quality"] == "estimate":
                    estimates[row["reason"]].append(row)
            metrics[key]["estimate_components"] = [
                {
                    "reason": reason,
                    "amount_usd": str(sum((Decimal(r["amount_usd"]) for r in items), Decimal(0))),
                    "event_count": len(items),
                }
                for reason, items in sorted(estimates.items())
            ]
        metrics["unclassified-net"] = metric(
            sum(
                (Decimal(r["amount_usd"]) for r in rows if r["line_id"] == "unclassified"),
                Decimal(0),
            )
            if len(missing) < len(config["wallets"])
            else None,
            complete=not missing,
            reason="Unclassified movements are disclosed outside statement totals",
            refs=["event_ledger.unclassified"],
        )
        metrics["cash-in-transit"] = metric(
            reason="Authenticated bridge message matching is incomplete; no zero assumed"
        )
        for side, key in [("opening", "opening-cash"), ("closing", "closing-cash")]:
            known = Decimal(0)
            observed = False
            incomplete = []
            for chain, source in evidence["chains"].items():
                if not source.get("balances") and not source.get("absence_proof"):
                    incomplete.append(chain)
                    continue
                if source.get("absence_proof"):
                    observed = True
                for r in source.get("balances", []):
                    if not is_cash(config, chain, r["token"], r["owner"]):
                        continue
                    if r.get(side) is None:
                        incomplete.append(chain)
                        continue
                    observed = True
                    known += (
                        Decimal(r[side]) / 10 ** config["tokens"][chain][r["token"]]["decimals"]
                    )
            # Issuer reward cash in raw ALMs requires a separate principal/reward
            # sub-ledger. The independently observed par wallet balance is known.
            metrics[key] = metric(
                known if observed else None,
                complete=False,
                reason="Known par wallet/vault cash; issuer reward cash and bridge transit reconciliation pending"
                + (
                    "; missing balances: " + ",".join(sorted(set(incomplete))) if incomplete else ""
                ),
                refs=["pinned_balanceOf:" + side],
            )
        metrics["measured-cash"] = metrics["closing-cash"].copy()
        metrics["measured-change"] = total(
            [
                ("closing-cash", 1, metrics["closing-cash"]),
                ("opening-cash", -1, metrics["opening-cash"]),
            ]
        )
        return metrics
