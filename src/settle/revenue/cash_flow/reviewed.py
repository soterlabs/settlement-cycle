"""Finite reviewed public cash events; never infer purpose from payer alone."""

import json
from decimal import Decimal, localcontext
from importlib.resources import files

from ..verification import digest
from .liability import address

LINES = {
    "allocation-changes",
    "external-receipts",
    "treasury-disposals",
    "internal-routing",
    "unclassified",
}


def reviewed_events():
    result = json.loads(
        files(__package__).joinpath("reviewed-receipts.spark-2026.v1.json").read_text()
    )
    seen = set()
    with localcontext() as ctx:
        ctx.prec = 100
        for record in result["records"]:
            key = (record["chain"], record["tx_hash"], record["log_index"])
            value = Decimal(record["amount_usd"])
            parts = [Decimal(p["amount_usd"]) for p in record["parts"]]
            if (
                key in seen
                or not value.is_finite()
                or not value
                or not parts
                or any(not p.is_finite() or p * value <= 0 for p in parts)
                or sum(parts) != value
                or any(
                    p["line_id"] not in LINES or p["quality"] not in ("supported", "estimate")
                    for p in record["parts"]
                )
            ):
                raise ValueError("Invalid reviewed cash event partition")
            seen.add(key)
    return result


def identity(event):
    return event["chain"], event["tx_hash"], event["log_index"]


def match(record, event, signed_value):
    """Match all retained cash facts, including endpoints, not hash/amount alone."""
    return (
        identity(record) == identity(event)
        and record["block"] == event["block"]
        and record["timestamp"] == event["timestamp"]
        and record["token"] == event["address"]
        and record["from"] == address(event["topic1"])
        and record["to"] == address(event["topic2"])
        and Decimal(record["amount_usd"]) == signed_value
    )


def index(config):
    source = config.get("reviewed_receipts")
    if not source:
        return {}, None
    return {identity(r): r for r in source["records"]}, digest(source)


def conversion_complete(record, rows, records, config):
    """A reviewed conversion is internal only with both exact cash legs present."""
    if not any(p["line_id"] == "internal-routing" for p in record["parts"]):
        return True
    legs = [
        r
        for r in records.values()
        if r["chain"] == record["chain"]
        and r["tx_hash"] == record["tx_hash"]
        and any(p["line_id"] == "internal-routing" for p in r["parts"])
    ]
    with localcontext() as ctx:
        ctx.prec = 100
        if (
            len(legs) != 2
            or sum(
                Decimal(p["amount_usd"])
                for r in legs
                for p in r["parts"]
                if p["line_id"] == "internal-routing"
            )
            != 0
        ):
            return False
        for leg in legs:
            meta = config["tokens"][leg["chain"]].get(leg["token"], {})
            owners = config["wallets"][leg["chain"]]
            if not meta.get("cash") or ((leg["from"] in owners) == (leg["to"] in owners)):
                return False
            sign = 1 if leg["to"] in owners else -1
            matches = [
                r
                for r in rows
                if r["topic0"]
                == "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
                and r["address"] == leg["token"]
                and match(leg, r, Decimal(int(r["data"], 16)) / 10 ** meta["decimals"] * sign)
            ]
            if len(matches) != 1:
                return False
    return True
