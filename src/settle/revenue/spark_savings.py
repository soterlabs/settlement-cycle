"""Exact depositor interest from SparkVault events, isolated from MSC.

Drip.diff includes prior un-dripped interest. Subtract the opening tail and
add the closing tail. Deposits and withdrawals affect supply, never income.
ETH is translated using each accrual day's closing USD quote; token-unit
interest is exact; USD values follow an explicit daily closing quote convention.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

from ..extract._keccak import keccak256
from .verification import digest

RAY = 10**27
VAULTS = ("S56", "S57", "S58", "S59", "S60", "S63")
EVENTS = {
    "0x" + keccak256(signature.encode()).hex(): name
    for name, signature in (
        ("deposit", "Deposit(address,address,uint256,uint256)"),
        ("withdraw", "Withdraw(address,address,address,uint256,uint256)"),
        ("drip", "Drip(uint256,uint256)"),
    )
}


def reconcile(opening, closing, rows, *, start, end):
    """Integer contract-unit accrual; reject incomplete or ambiguous evidence."""
    supply, chi = opening["supply"], opening["chi"]
    accrued = deposits = withdrawals = rounding = 0
    daily = defaultdict(int)
    daily_capital = defaultdict(int)
    seen = set()
    for row in sorted(rows, key=lambda r: (r.block_number, r.log_index)):
        key = row.block_number, row.log_index
        if key in seen or not start < row.block_number <= end:
            raise ValueError("Savings event is duplicated or outside pinned boundaries")
        seen.add(key)
        if row.topic0 not in EVENTS or len(row.data) != 130:
            raise ValueError("Unexpected Savings event")
        first, second = int(row.data[2:66], 16), int(row.data[66:], 16)
        kind = EVENTS[row.topic0]
        if kind == "drip":
            if first < chi or second != supply * first // RAY - supply * chi // RAY:
                raise ValueError("Savings Drip does not reproduce supply/chi accrual")
            accrued += second
            daily[datetime.fromtimestamp(row.block_time, UTC).date()] += second
            chi = first
        else:
            before = supply * chi // RAY
            if kind == "deposit":
                supply += second
                deposits += first
                rounding += supply * chi // RAY - before - first
            else:
                supply -= second
                withdrawals += first
                rounding += supply * chi // RAY - before + first
            daily_capital[datetime.fromtimestamp(row.block_time, UTC).date()] += (
                supply * chi // RAY - before
            )
            if supply < 0:
                raise ValueError("Savings history misses shares")
    if supply != closing["supply"] or chi != closing["chi"]:
        raise ValueError("Savings closing supply/chi mismatch")
    tails = [s["assets"] - s["supply"] * s["chi"] // RAY for s in (opening, closing)]
    if min(tails) < 0:
        raise ValueError("Savings pending accrual is negative")
    accrued += tails[1] - tails[0]
    if closing["assets"] - opening["assets"] - deposits + withdrawals != accrued + rounding:
        raise ValueError("Savings capital/interest bridge mismatch")
    return {
        "interest": accrued,
        "rounding": rounding,
        "deposits": deposits,
        "withdrawals": withdrawals,
        "opening_tail": tails[0],
        "closing_tail": tails[1],
        "daily_drips": daily,
        "daily_capital": daily_capital,
        "event_count": len(seen),
    }


def collect_vault(source, venue):
    chain, vault = str(venue.chain), venue.token.address.hex
    first, last = source.period.start, source.period.end
    before = first - timedelta(days=1)
    start, end = source.block(chain, before), source.block(chain, last)
    rows = source.logs(chain, [{"address": [vault], "topics": [list(EVENTS)]}])
    from .spark_reconciliation import absent_contract

    empty = absent_contract(source, chain, vault, rows)
    if empty is not None:
        return {
            **empty,
            "venue": venue.id,
            "chain": chain,
            "vault": vault,
            "asset": venue.underlying.symbol,
            **{
                key: "0"
                for key in (
                    "interest_units",
                    "opening_claim_units",
                    "closing_claim_units",
                    "deposits_units",
                    "withdrawals_units",
                    "share_rounding_units",
                    "opening_tail_units",
                    "closing_tail_units",
                )
            },
            "opening_pin": start,
            "closing_pin": end,
            "event_count": 0,
            "event_digest": digest([]),
            "daily_translation": [],
            **(
                {
                    "daily_liability": [
                        {
                            "date": str(first + timedelta(days=i)),
                            "opening_claim_units": "0",
                            "closing_claim_units": "0",
                            "net_capital_units": "0",
                            "interest_units": "0",
                        }
                        for i in range(source.period.n_days)
                    ]
                }
                if venue.underlying.symbol == "WETH"
                else {}
            ),
        }
    if any(row.address.lower() != vault.lower() for row in rows):
        raise ValueError("Savings evidence includes another vault")

    def state(day):
        block = source.block(chain, day)
        if hasattr(source, "code") and source.code(chain, vault, block) == "0x":
            return {"supply": 0, "chi": RAY, "assets": 0}
        values = {
            key: source.number(chain, vault, getter, block)
            for key, getter in (
                ("supply", "totalSupply()"),
                ("chi", "chi()"),
                ("assets", "totalAssets()"),
            )
        }
        return values

    opening, closing = state(before), state(last)
    result = reconcile(opening, closing, rows, start=start, end=end)
    with localcontext() as ctx:
        ctx.prec = 60
        scale = Decimal(10) ** venue.underlying.decimals
        interest = Decimal(result["interest"]) / scale
        usd = interest
        quality = "supported"
        prices = []
        liability_days = []
        if venue.underlying.symbol == "WETH":
            quality = "supported"
            usd, tail = Decimal(0), result["opening_tail"]
            from ..extract.transfer_logs import TRANSFER_TOPIC0
            from .spark_onchain import topic
            from .spark_reconciliation import unique

            vault_topic = topic(vault)
            cash_rows = unique(
                source.logs(
                    chain,
                    [
                        {
                            "address": [venue.underlying.address.hex],
                            "topics": [[TRANSFER_TOPIC0], [vault_topic]],
                        },
                        {
                            "address": [venue.underlying.address.hex],
                            "topics": [[TRANSFER_TOPIC0], [], [vault_topic]],
                        },
                    ],
                )
            )
            daily_cash = defaultdict(int)
            for row in cash_rows:
                delta = int(row.data, 16) * (
                    (row.topic2 == vault_topic) - (row.topic1 == vault_topic)
                )
                daily_cash[datetime.fromtimestamp(row.block_time, UTC).date()] += delta

            def liability(day, assets):
                block = source.block(chain, day)
                if hasattr(source, "code") and source.code(chain, vault, block) == "0x":
                    return 0, 0
                idle = source.number(
                    chain, venue.underlying.address.hex, "balanceOf(address)", block, int(vault, 16)
                )
                owed = source.number(chain, vault, "assetsOutstanding()", block)
                if assets - idle != owed or owed < 0:
                    raise ValueError("Savings ETH outstanding claim/idle balance mismatch")
                return idle, owed

            previous_idle, previous_owed = liability(before, opening["assets"])
            for i in range(source.period.n_days):
                day = first + timedelta(days=i)
                current = closing if day == last else state(day)
                new_tail = current["assets"] - current["supply"] * current["chi"] // RAY
                if new_tail < 0:
                    raise ValueError("Negative daily Savings tail")
                quantity = result["daily_drips"].get(day, 0) + new_tail - tail
                idle, owed = liability(day, current["assets"])
                capital = result["daily_capital"].get(day, 0) - daily_cash[day]
                if (
                    idle - previous_idle != daily_cash[day]
                    or owed - previous_owed - capital != quantity
                ):
                    raise ValueError("Savings ETH daily capital/interest/liability bridge failed")
                liability_days.append(
                    {
                        "date": str(day),
                        "opening_claim_units": str(Decimal(previous_owed) / scale),
                        "closing_claim_units": str(Decimal(owed) / scale),
                        "net_capital_units": str(Decimal(capital) / scale),
                        "interest_units": str(Decimal(quantity) / scale),
                    }
                )
                previous_idle, previous_owed = idle, owed
                price = source.price("coingecko:ethereum", day)
                usd += Decimal(quantity) / scale * price
                prices.append(
                    {
                        "date": str(day),
                        "interest_units": str(Decimal(quantity) / scale),
                        "usd_price": str(price),
                    }
                )
                tail = new_tail
            if sum((Decimal(row["interest_units"]) for row in prices), Decimal(0)) != interest:
                raise ValueError("Daily ETH interest does not bridge to monthly token accrual")
        return {
            "venue": venue.id,
            "chain": chain,
            "vault": vault,
            "asset": venue.underlying.symbol,
            "status": "available",
            "recognition": "accrual",
            "quality": quality,
            "value": str(usd),
            "interest_units": str(interest),
            "opening_claim_units": str(Decimal(opening["assets"]) / scale),
            "closing_claim_units": str(Decimal(closing["assets"]) / scale),
            "deposits_units": str(Decimal(result["deposits"]) / scale),
            "withdrawals_units": str(Decimal(result["withdrawals"]) / scale),
            "share_rounding_units": str(Decimal(result["rounding"]) / scale),
            "opening_tail_units": str(Decimal(result["opening_tail"]) / scale),
            "closing_tail_units": str(Decimal(result["closing_tail"]) / scale),
            "opening_vsr_ray": (
                str(source.number(chain, vault, "vsr()", start))
                if not hasattr(source, "code") or source.code(chain, vault, start) != "0x"
                else None
            ),
            "closing_vsr_ray": str(source.number(chain, vault, "vsr()", end)),
            "opening_pin": start,
            "closing_pin": end,
            "event_count": result["event_count"],
            "event_digest": digest([(r.block_number, r.log_index, r.data) for r in rows]),
            "daily_translation": prices,
            **(
                {
                    "daily_liability": liability_days,
                    "idle_cash_event_count": len(cash_rows),
                    "idle_cash_event_digest": digest(
                        [
                            (r.block_number, r.log_index, r.topic1, r.topic2, r.data)
                            for r in cash_rows
                        ]
                    ),
                }
                if liability_days
                else {}
            ),
            **(
                {
                    "translation_policy": "daily_closing_ETH_USD_nonforward_quote_max_age_2h_confidence_gte_0.9"
                }
                if prices
                else {}
            ),
            "basis": "exact_Drip_interest_plus_closing_tail_minus_opening_tail"
            + ("_translated_at_daily_closing_ETH_USD" if prices else "_stablecoin_par"),
        }
