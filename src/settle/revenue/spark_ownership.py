"""Reporting-only historical SDE attribution, independent of MSC configuration."""

from datetime import date, timedelta
from decimal import Decimal as D
from decimal import localcontext

from ..compute.monthly_pnl import _univ4_position_legs
from ..compute.sky_revenue import base_rate_spread_at
from ..domain.period import Period
from ..domain.primes import Chain
from ..normalize.positions import _venue_v4_pool_key
from ..normalize.sources.curve_pool import CurvePoolSource
from ..normalize.sources.uniswap_v4 import RPCUniswapV4PositionSource
from .spark_onchain import Source, topic
from .spark_v4_fees import collect as v4_fees
from .spark_v4_fees import ownership_history, reconcile_capital

USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
SUSDS = "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
WINDOWS = {"S24": (date(2025, 11, 13), date(2026, 6, 25)), "S62": (date(2026, 5, 26), date.max)}


def collect(source, retained=None):
    venues = {v.id: v for v in source.prime.venues}
    result = {"venues": {}, "funding_adjustment": {}, "errors": {}}
    owner = source.prime.alm[Chain("ethereum")]
    curve = CurvePoolSource()
    v4 = RPCUniswapV4PositionSource()
    retained = retained or {}

    def positions(venue, block):
        ids = set()
        for row in ownership_history(owner.hex, source.closing_pins["ethereum"]):
            if row.block_number > block:
                break
            tid = int(row.topic3, 16)
            if row.topic1 == topic(owner.hex):
                ids.discard(tid)
            if row.topic2 == topic(owner.hex):
                ids.add(tid)
        return (
            v4.positions_in_pool(
                "ethereum", owner.value, sorted(ids), _venue_v4_pool_key(venue), block
            )
            if ids
            else []
        )

    from functools import cache

    @cache
    def curve_state(block, need_nav=False):
        venue = venues["S24"]
        if source.code("ethereum", venue.token.address.hex, block) == "0x":
            return D(0), D(0), D(0), D(0)
        q = source.number(
            "ethereum", venue.token.address.hex, "balanceOf(address)", block, int(owner.hex, 16)
        )
        if not q and not need_nav:
            return D(0), D(0), D(0), D(0)
        state = curve.read_pool("ethereum", venue.token.address.value, block)
        if not state.total_supply:
            if q:
                raise ValueError("Curve supply is zero with owned shares")
            return D(0), D(0), D(0), D(0)
        weight = D(q) / state.total_supply
        usdt = susds = D(0)
        for token, units in zip(state.coins, state.balances, strict=True):
            address = token.hex.lower()
            if address == USDT:
                usdt += weight * D(units) / 10**6
            elif address == SUSDS:
                susds += weight * D(units) / 10**18
            elif units:
                raise ValueError("Unexpected Curve asset")
        rate = (
            D(source.number("ethereum", SUSDS, "convertToAssets(uint256)", block, 10**18)) / 10**18
        )
        value = usdt + susds * rate
        nav = (
            value / D(q)
            if q
            else (
                sum(
                    D(units)
                    / (10**6 if token.hex.lower() == USDT else 10**18)
                    * (D(1) if token.hex.lower() == USDT else rate)
                    for token, units in zip(state.coins, state.balances, strict=True)
                )
                / state.total_supply
            )
        )
        return value, usdt, susds, nav

    def segment(start, end):
        opening = dict(source.opening_pins)
        opening["ethereum"] = source.block("ethereum", start - timedelta(days=1))
        closing = {Chain(chain): block for chain, block in source.closing_pins.items()}
        closing[Chain("ethereum")] = source.block("ethereum", end)
        return Source(
            source.prime, Period(start, end, closing), opening, daily_pins=source.daily_pins
        )

    empty_curve = False
    # Curve fees/value growth excluding capital, with sUSDS appreciation kept
    # outside Sky's USDT designation. Capital at block NAV is an explicit estimate.
    try:
        venue = venues["S24"]
        value = D(0)
        sky = D(0)
        days = []
        previous = curve_state(source.block("ethereum", source.period.start - timedelta(days=1)))
        lp_rows = source.logs(
            "ethereum",
            [
                {
                    "address": [venue.token.address.hex],
                    "topics": [
                        ["0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"],
                        [topic(owner.hex)],
                    ],
                },
                {
                    "address": [venue.token.address.hex],
                    "topics": [
                        ["0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"],
                        [],
                        [topic(owner.hex)],
                    ],
                },
            ],
        )
        capital = {}
        from datetime import UTC, datetime

        for row in {(r.block_number, r.log_index): r for r in lp_rows}.values():
            day = datetime.fromtimestamp(row.block_time, UTC).date()
            sign = (row.topic2 == topic(owner.hex)) - (row.topic1 == topic(owner.hex))
            capital[day] = (
                capital.get(day, D(0))
                + D(int(row.data, 16)) * sign * curve_state(row.block_number, True)[3]
            )
        empty_curve = (
            not any(int(r.data, 16) for r in lp_rows)
            and previous[0] == 0
            and curve_state(source.closing_pins["ethereum"])[0] == 0
        )
        for i in range(0 if empty_curve else source.period.n_days):
            day = source.period.start + timedelta(days=i)
            current = curve_state(source.block("ethereum", day))
            gross = current[0] - previous[0] - capital.get(day, D(0))
            block0 = source.block("ethereum", day - timedelta(days=1))
            embedded = D(0)
            if previous[2] or current[2]:
                rate0 = (
                    D(source.number("ethereum", SUSDS, "convertToAssets(uint256)", block0, 10**18))
                    / 10**18
                )
                rate1 = (
                    D(
                        source.number(
                            "ethereum",
                            SUSDS,
                            "convertToAssets(uint256)",
                            source.block("ethereum", day),
                            10**18,
                        )
                    )
                    / 10**18
                )
                embedded = (previous[2] + current[2]) / 2 * (rate1 - rate0)
            active = WINDOWS["S24"][0] <= day <= WINDOWS["S24"][1]
            due = gross - embedded if active else D(0)
            sky += due
            value += gross
            days.append(
                dict(
                    date=str(day),
                    gross=str(gross),
                    sky=str(due),
                    prime=str(gross - due),
                    usdt_value=str(current[1]),
                )
            )
            previous = current
        result["venues"]["S24"] = dict(
            status="available",
            quality="estimate",
            recognition="investment_return",
            value=str(value - sky),
            gross_income_usd=str(value),
            sky_income_usd=str(sky),
            daily=days,
            basis="daily_Curve_NAV_capital_bridge_less_historically_designated_USDT_income_excluding_sUSDS_appreciation",
        )
    except Exception as exc:
        result["errors"]["S24"] = type(exc).__name__
        result["venues"]["S24"] = dict(
            status="unavailable", reason="historical_Curve_ownership_not_verified"
        )
    try:
        gross = sky = D(0)
        start = source.period.start
        activation = WINDOWS["S62"][0]
        windows = [
            (start, min(source.period.end, activation - timedelta(days=1))),
            (max(start, activation), source.period.end),
        ]
        for lo, hi in windows:
            if lo > hi:
                continue
            existing = retained.get("amm_fees", {}).get("S62", {})
            if (
                lo == source.period.start
                and hi == source.period.end
                and existing.get("status") == "available"
                and existing.get("capital_reconciliation", {}).get("status") == "available"
            ):
                fees = {"S62": existing}
            else:
                part = segment(lo, hi)
                fees = {v: v4_fees(part, venues[v]) for v in ("S61", "S62", "S66")}
                reconcile_capital(part, fees)
            earned = D(fees["S62"]["capital_reconciliation"]["gross_return_usd"])
            gross += earned
            if lo >= activation:
                sky += earned
        result["venues"]["S62"] = dict(
            status="available",
            quality="supported",
            recognition="investment_return",
            value=str(gross - sky),
            gross_income_usd=str(gross),
            sky_income_usd=str(sky),
            basis="event_reconciled_V4_return_split_at_Atlas_designation_date",
        )
    except Exception as exc:
        result["errors"]["S62"] = type(exc).__name__
        result["venues"]["S62"] = dict(
            status="unavailable", reason="historical_V4_ownership_not_verified"
        )
    try:
        adjustment = D(0)
        days = []
        with localcontext() as ctx:
            ctx.prec = 100
            for i in range(source.period.n_days):
                day = source.period.start + timedelta(days=i)
                correction = D(0)
                for key, old_start in (("S24", date(2025, 11, 13)), ("S62", date(2026, 2, 1))):
                    lo, hi = WINDOWS[key]
                    old = day >= old_start
                    new = lo <= day <= hi
                    if old == new or (key == "S24" and empty_curve):
                        continue
                    block = source.block("ethereum", day)
                    exposure = (
                        curve_state(block)[1]
                        if key == "S24"
                        else _univ4_position_legs(venues[key], positions(venues[key], block))[1]
                    )
                    if not exposure:
                        continue
                    ssr = D(source.number("ethereum", SUSDS, "ssr()", block)) / 10**27
                    apr = 365 * (ssr**86400 - 1) + base_rate_spread_at(day)
                    correction += exposure * apr / 365 * (int(old) - int(new))
                adjustment += correction
                days.append(dict(date=str(day), funding_adjustment=str(correction)))
        result["funding_adjustment"] = dict(
            status="available",
            quality="supported",
            recognition="accrual",
            value=str(adjustment),
            daily=days,
            basis="daily_EOD_Atlas_SDE_scope_correction_to_retained_charge",
        )
    except Exception as exc:
        result["errors"]["funding"] = type(exc).__name__
        result["funding_adjustment"] = dict(
            status="unavailable", reason="historical_SDE_funding_not_verified"
        )
    return result
