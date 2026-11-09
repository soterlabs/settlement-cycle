"""Receipt-basis USDS rewards from Spark's reviewed Lockstake urn.

No inferred accrual schedule. Authenticate RewardPaid, farm funding and delivery
into the owned cash perimeter; retain the exact recipient Transfer for cash flow.
"""

from datetime import UTC, datetime
from decimal import Decimal, localcontext

from ..extract._keccak import keccak256
from .cash_flow.collector import TRANSFER
from .cash_flow.config import load
from .cash_flow.liability import address
from .spark_onchain import topic

FARM = "0x38e4254bd82ed5ee97cd1c4278faae748d998865"
URN = "0x74bf5ab1c53e7b5ccec8d7107a76aea4e058cdf7"
USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
PAID = "0x" + keccak256(b"RewardPaid(address,uint256)").hex()


def collect(source):
    config = load()
    owners = list(config["wallets"]["ethereum"])
    rows = source.logs(
        "ethereum",
        [
            {"address": [FARM], "topics": [[PAID], [topic(URN)]]},
            {"address": [USDS], "topics": [[TRANSFER], [topic(FARM)], [topic(URN)]]},
            {"address": [USDS], "topics": [[TRANSFER], [topic(URN)], [topic(a) for a in owners]]},
        ],
    )
    return project(rows, source.period.end, owners)


def project(rows, cutoff, owners):
    receipts = []
    seen = set()
    with localcontext() as ctx:
        ctx.prec = 100
        for reward in rows:
            if (
                reward.address.lower() != FARM
                or reward.topic0 != PAID
                or address(reward.topic1) != URN
            ):
                continue
            day = datetime.fromtimestamp(reward.block_time, UTC).date()
            if not cutoff.replace(day=1) <= day <= cutoff:
                raise ValueError("SKY staking reward is outside the requested month")
            units = int(reward.data, 16)
            if not units:
                continue
            funding = [
                r
                for r in rows
                if r.transaction_hash == reward.transaction_hash
                and r.address.lower() == USDS
                and r.topic0 == TRANSFER
                and address(r.topic1) == FARM
                and address(r.topic2) == URN
                and int(r.data, 16) == units
            ]
            payments = [
                r
                for r in rows
                if r.transaction_hash == reward.transaction_hash
                and r.address.lower() == USDS
                and r.topic0 == TRANSFER
                and address(r.topic1) == URN
                and address(r.topic2) in owners
                and int(r.data, 16) == units
            ]
            if len(funding) != 1 or len(payments) != 1:
                raise ValueError("SKY staking reward lacks a unique farm-funded owned receipt")
            payment = payments[0]
            identity = payment.transaction_hash, payment.log_index
            if identity in seen:
                raise ValueError("Duplicate SKY staking reward receipt")
            seen.add(identity)
            receipts.append(
                dict(
                    chain="ethereum",
                    block=payment.block_number,
                    log_index=payment.log_index,
                    tx_hash=payment.transaction_hash,
                    timestamp=payment.block_time,
                    token=USDS,
                    **{"from": URN, "to": address(payment.topic2)},
                    amount_usd=str(Decimal(units) / 10**18),
                    reward_log_index=reward.log_index,
                    farm_log_index=funding[0].log_index,
                )
            )
        return dict(
            status="available",
            quality="supported",
            recognition="receipt",
            value=str(sum((Decimal(r["amount_usd"]) for r in receipts), Decimal(0))),
            basis="verified_Lockstake_USDS_rewards_receipt_basis",
            period_end=str(cutoff),
            scope=dict(farm=FARM, urn=URN, token=USDS),
            receipts=receipts,
        )


def validate_document(document):
    """Validate the retained accounting scope, cash partitions and period bounds."""
    raw = document.get("sky_staking")
    if not raw or raw.get("status") == "unavailable":
        return
    if raw.get("status") not in ("available", "partial") or raw.get("scope") != dict(
        farm=FARM, urn=URN, token=USDS
    ):
        raise ValueError("Invalid SKY staking scope")
    cutoff = document["cutoff"]
    end = raw.get("period_end", cutoff)
    if not document["period_start"] <= end <= cutoff:
        raise ValueError("Invalid SKY staking period")
    owners = load()["wallets"]["ethereum"]
    seen = set()
    with localcontext() as ctx:
        ctx.prec = 100
        total = Decimal(0)
        for receipt in raw.get("receipts", []):
            key = receipt["chain"], receipt["tx_hash"], receipt["log_index"]
            day = datetime.fromtimestamp(receipt["timestamp"], UTC).date().isoformat()
            value = Decimal(receipt["amount_usd"])
            indices = [receipt[k] for k in ("log_index", "reward_log_index", "farm_log_index")]
            if (
                key in seen
                or receipt["chain"] != "ethereum"
                or receipt["token"] != USDS
                or receipt["from"] != URN
                or receipt["to"] not in owners
                or not document["period_start"] <= day <= end
                or not value.is_finite()
                or value <= 0
                or len(set(indices)) != 3
                or any(type(i) is not int or i < 0 for i in indices)
            ):
                raise ValueError("Invalid SKY staking receipt")
            seen.add(key)
            total += value
        if Decimal(raw["value"]) != total:
            raise ValueError("SKY staking income differs from authenticated receipts")
