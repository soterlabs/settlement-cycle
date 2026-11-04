"""Monthly reporting income from existing issuer rules and attributed cash.

Only the requested month's earnings are calculated. No cumulative entitlement
or historical receivable is inferred from these monthly flows.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from types import SimpleNamespace

from ..compute.incentives import daily_accrual
from ..domain.primes import Address
from ..extract.transfer_logs import TRANSFER_TOPIC0
from ..normalize.incentives import IncentiveInputs
from .spark_onchain import topic
from .spark_reconciliation import unique
from .spark_v4_fees import ownership_history
from .verification import digest


def anchorage(source):
    venue = next(v for v in source.prime.venues if v.id == "S23")
    cfg = venue.settlement_view
    if cfg.method != "alm_receipts" or cfg.source_venue != "S26":
        raise ValueError("Anchorage cash attribution is not configured")
    cash = next(v for v in source.prime.venues if v.id == cfg.source_venue)
    holder, payer = source.prime.alm[cash.chain].hex, cfg.payer.lower()
    rows = unique(
        source.logs(
            str(cash.chain),
            [
                {
                    "address": [cash.token.address.hex],
                    "topics": [[TRANSFER_TOPIC0], [topic(payer)], [topic(holder)]],
                },
                {
                    "address": [cash.token.address.hex],
                    "topics": [[TRANSFER_TOPIC0], [topic(holder)], [topic(payer)]],
                },
            ],
        )
    )
    daily = defaultdict(Decimal)
    for row in rows:
        day = datetime.fromtimestamp(row.block_time, UTC).date()
        daily[day] += (
            Decimal(int(row.data, 16))
            / 10**cash.token.decimals
            * (1 if row.topic2 == topic(holder) else -1)
        )
    rules = source.prime.principal_return_overrides.get(cash.chain, {}).get(
        Address.from_str(payer), ()
    )
    earned = excluded = Decimal(0)
    details = []
    for day, amount in sorted(daily.items()):
        if amount <= 0:
            continue  # Directional capital rule, identical to the existing cash source.
        matches = [
            r
            for r in rules
            if r.date == day
            and (not r.token or r.token == cash.token.symbol)
            and abs(r.amount - amount) <= 1
        ]
        if len(matches) > 1:
            raise ValueError("Ambiguous Anchorage principal exclusion")
        capital = (
            (matches[0].capital_amount if matches[0].capital_amount is not None else amount)
            if matches
            else Decimal(0)
        )
        if not 0 <= capital <= amount:
            raise ValueError("Anchorage principal exceeds the received amount")
        earned += amount - capital
        excluded += capital
        details.append(
            {
                "date": str(day),
                "day_net_received_usd": str(amount),
                "principal_excluded_usd": str(capital),
                "income_usd": str(amount - capital),
            }
        )
    return {
        "status": "available",
        "value": str(earned),
        "recognition": "receipt",
        "quality": "supported",
        "basis": "configured_Anchorage_day_net_cash_receipts_after_registered_principal_exclusions",
        "payer": payer,
        "source_venue": cfg.source_venue,
        "principal_excluded_usd": str(excluded),
        "daily_receipts": details,
        "event_digest": digest(
            [(r.block_number, r.log_index, r.topic1, r.topic2, r.data) for r in rows]
        ),
    }


def issuer_income(source, venue, amm_fees):
    cfg = venue.incentive
    program = cfg["program"]
    if str(source.period.end) < cfg["accrual_start"]:
        return {
            "status": "available",
            "value": "0",
            "recognition": "receipt" if program == "usdg" else "accrual",
            "quality": "supported",
            "basis": "issuer_program_not_started",
            "program": program,
        }
    provider = IncentiveInputs(
        source.prime,
        SimpleNamespace(block_resolver=object(), v4_position=None, curve_pool=None),
        source.period,
    )
    provider.block = lambda chain, day: source.block(str(chain), day)

    def owned(source_venue, holder, block):
        fee = amm_fees.get(source_venue.id, {})
        if fee.get("status") != "available":
            raise ValueError("Issuer eligibility requires a complete owned NFT inventory")
        ids = set()
        for r in ownership_history(holder.hex, source.closing_pins[str(source_venue.chain)]):
            if r.block_number > block:
                break
            tid = int(r.topic3, 16)
            if r.topic1 == topic(holder.hex):
                ids.discard(tid)
            if r.topic2 == topic(holder.hex):
                ids.add(tid)
        return sorted(ids & set(fee["position_ids"]))

    provider.owned_v4_ids = owned
    rates = provider.rates(source.period.start, source.period.end) if program == "pyusd" else {}
    total = Decimal(0)
    days = []
    if program != "usdg":
        for i in range(source.period.n_days):
            day = source.period.start + timedelta(days=i)
            earned, components = daily_accrual(
                program, day, provider.eligible(venue, day), rates.get(day)
            )
            total += earned
            days.append({"date": str(day), "accrual": str(earned), "components": components})
    receipts = Decimal(0)
    witnesses = []
    for entry in cfg["receipt_sources"]:
        chain = entry.get("chain", str(venue.chain))
        rows = unique(
            source.logs(
                chain,
                [
                    {
                        "address": [entry["token"]],
                        "topics": [
                            [TRANSFER_TOPIC0],
                            [topic(entry["payer"])],
                            [topic(entry["recipient"])],
                        ],
                    }
                ],
            )
        )
        receipts += sum(
            (Decimal(int(r.data, 16)) / 10**venue.token.decimals for r in rows), Decimal(0)
        )
        witnesses.extend((chain, r.block_number, r.log_index, r.data) for r in rows)
    return {
        "status": "available",
        "value": str(receipts if program == "usdg" else total),
        "recognition": "receipt" if program == "usdg" else "accrual",
        "quality": "supported",
        "program": program,
        "basis": "issuer_eight_hour_receipts"
        if program == "usdg"
        else "issuer_ACT_365_daily_EOD_eligible_balances",
        "period_receipts_usd": str(receipts),
        "daily": days,
        "receipt_event_digest": digest(witnesses),
        "receivable_inferred": False,
    }


def collect(source, amm_fees, *, progress=None):
    result = {"issuer_income": {}, "monthly_income_errors": {}}
    for venue in source.prime.venues:
        if venue.id not in ("S67", "S68", "S69"):
            continue
        if progress:
            progress("issuer." + venue.id)
        try:
            with localcontext() as ctx:
                ctx.prec = 60
                result["issuer_income"][venue.incentive["program"]] = issuer_income(
                    source, venue, amm_fees
                )
        except Exception as exc:
            result["monthly_income_errors"][venue.id] = type(exc).__name__
            result["issuer_income"][venue.incentive["program"]] = {
                "status": "unavailable",
                "reason": "monthly_issuer_source_unavailable",
            }
    if progress:
        progress("anchorage.receipts")
    try:
        result["anchorage.receipts"] = anchorage(source)
    except Exception as exc:
        result["monthly_income_errors"]["anchorage.receipts"] = type(exc).__name__
    return result
