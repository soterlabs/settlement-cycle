"""Reporting-only loan principal, with explicit reviewed cash-return splits.

The ALM -> escrow boundary includes duplicate advances, cash refunds and payments
outside loan routes. Use the two evidenced loan wallets instead. Never infer a
new return's principal/interest split, or import MSC's conservative cash policy.
"""

import json
from decimal import Decimal
from pathlib import Path

from .verification import digest

TOKEN = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
ESCROW = "0x49506c3aa028693458d6ee816b2ec28522946872"
ROUTES = {
    "0x8149c53ea54de2a62c9e4caef29478f1af4c7bd3",
    "0x8bcd4b0a1ad17521aa12057cabd40c1b32d23181",
}


def policy():
    return json.loads(Path(__file__).with_name("spark_anchorage_principal.v1.json").read_text())


def reconstruct(events, block):
    """Require the exact reviewed positive loan-route transfers up to the pin."""
    rules = policy()
    expected = [r for r in rules["events"] if r["block"] <= block]
    observed = sorted(events, key=lambda r: (r["block"], r["log_index"]))
    cash_fields = ("block", "log_index", "tx_hash", "sender", "recipient", "units")
    if observed != [{k: r[k] for k in cash_fields} for r in expected]:
        raise ValueError("Missing, changed or unreviewed Anchorage loan-route transfer")
    balances = dict.fromkeys(ROUTES, 0)
    for r in expected:
        principal = int(r["principal_units"])
        if not 0 <= principal <= int(r["units"]):
            raise ValueError("Invalid Anchorage split")
        route = r["recipient"] if r["sender"] == ESCROW else r["sender"]
        balances[route] += principal if r["sender"] == ESCROW else -principal
        if balances[route] < 0:
            raise ValueError("Unfunded Anchorage principal return")
    return {
        "policy_hash": digest(rules),
        "policy_version": rules["version"],
        "assumptions": rules["assumptions"],
        "block": block,
        "events": observed,
        "route_principal_units": {k: str(v) for k, v in sorted(balances.items())},
        "value": str(Decimal(sum(balances.values())) / 10**6),
        "quality": "estimate",
        "basis": "reviewed_loan_route_cash_principal_not_escrow_balance",
    }


def collect(block):
    from ..extract import hypersync
    from .cash_flow.collector import TRANSFER
    from .spark_onchain import topic

    result = hypersync.query_logs(
        "ethereum",
        [
            {
                "address": [TOKEN],
                "topics": [[TRANSFER], [topic(ESCROW)], [topic(a) for a in sorted(ROUTES)]],
            },
            {
                "address": [TOKEN],
                "topics": [[TRANSFER], [topic(a) for a in sorted(ROUTES)], [topic(ESCROW)]],
            },
        ],
        0,
        block,
        log_fields=[*hypersync._DEFAULT_LOG_FIELDS, "transaction_hash"],
    )
    events = []
    for r in result.rows:
        if r.address.lower() != TOKEN or r.topic0 != TRANSFER:
            raise ValueError("Unexpected Anchorage token/event")
        units = int(r.data, 16)
        if not units:  # Zero-value address-poisoning logs do not fund a loan.
            continue
        events.append(
            dict(
                block=r.block_number,
                log_index=r.log_index,
                tx_hash=r.transaction_hash,
                sender="0x" + r.topic1[-40:],
                recipient="0x" + r.topic2[-40:],
                units=str(units),
            )
        )
    return reconstruct(events, block)


def validate(document):
    proof = document["anchorage_principal"]
    block = document["pins"]["ethereum"]["block"]
    if proof != reconstruct(proof["events"], block):
        raise ValueError("Anchorage principal proof differs")
    row = document["positions"]["S23"]
    if (
        row["status"] != "available"
        or row["value"] != proof["value"]
        or row["quality"] != "estimate"
    ):
        raise ValueError("Anchorage position differs from reviewed loan principal")
