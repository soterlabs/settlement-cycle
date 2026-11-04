"""Additional reporting-only Spark sources for the complete statement perimeter."""

from copy import copy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal, localcontext
from functools import cache
from itertools import pairwise

from ..domain.primes import Address
from .financial_allocations import Allocation
from .spark_onchain import SPWETH, WAD, native_supply
from .spark_savings import VAULTS, collect_vault
from .spark_v4_fees import collect as collect_fees

SUSDS = ("S32", "S37", "S43", "S47", "S51")
SENTORA = "0xfc8c624b6080a0a780583799f2a862de936f6e22"
RLUSD = "0x8292bb45bf1ee4d140127049757c2e0ff06317ed"


def weth_supply(source):
    """Supply interest in WETH translated daily; price revaluation is separate."""
    chain, holder = (
        "ethereum",
        source.prime.alm[next(c for c in source.prime.alm if str(c) == "ethereum")].hex,
    )
    before = source.period.start - timedelta(days=1)
    days = [before + timedelta(days=i) for i in range(source.period.n_days + 1)]
    samples = []
    for day in days:
        block = source.block(chain, day)
        quantity = source.number(chain, SPWETH, "scaledBalanceOf(address)", block, int(holder, 16))
        pool = source.number(chain, SPWETH, "POOL()", block)
        index = source.number(
            chain,
            f"0x{pool:040x}",
            "getReserveNormalizedIncome(address)",
            block,
            int("c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2", 16),
        )
        samples.append((Decimal(quantity) / WAD, Decimal(index) / 10**27))
    income = Decimal(0)
    for day, ((q0, i0), (q1, i1)) in zip(days[1:], pairwise(samples), strict=True):
        if i1 < i0:
            raise ValueError("SparkLend WETH index decreased")
        earned = (q0 + q1) / 2 * (i1 - i0)
        if earned:
            income += earned * source.price("coingecko:ethereum", day)
    return {
        "status": "available",
        "value": str(income),
        "quality": "estimate",
        "recognition": "accrual",
        "basis": "daily_scaled_spWETH_balance_times_liquidity_index_growth_translated_at_daily_ETH_USD",
    }


def sentora(source):
    """Reuse native net-vault NAV return, with an explicit reporting identity."""
    base = next(v for v in source.prime.venues if v.id == "S13")
    token = replace(base.token, address=Address.from_str(SENTORA), decimals=18)
    underlying = replace(
        base.underlying, address=Address.from_str(RLUSD), decimals=18, symbol="RLUSD"
    )
    venue = replace(base, id="reporting.sentora", token=token, underlying=underlying)
    block = source.block("ethereum", source.period.end)
    if source.number("ethereum", SENTORA, "asset()", block) != int(RLUSD, 16):
        raise ValueError("Unexpected Sentora underlying")
    proxy = copy(source)
    proxy.prime = replace(source.prime, venues=(*source.prime.venues, venue))
    return native_supply(
        proxy, Allocation(venue.id, "morpho.sentora_rlusd", "Sentora RLUSD", "B", "Morpho vaults")
    )


def arbitrum_susds(source, venue):
    # The Arbitrum bridged share token has no ERC4626 conversion getter.
    # Use the same RAY rate provider as Spark PSM3 after verifying its assets.
    # sparkdotfi/spark-psm/src/PSM3.sol::_getSUsdsValue
    psm = "0x2b05f8e1cacc6974fd79a673a341fe1f58d27266"
    chain = "arbitrum"
    token = venue.token.address.hex
    holder = source.prime.alm[venue.chain].hex
    start = source.period.start - timedelta(days=1)
    days = [start + timedelta(days=i) for i in range(source.period.n_days + 1)]
    samples = []
    providers = set()
    for day in days:
        block = source.block(chain, day)
        if source.number(chain, psm, "susds()", block) != int(token, 16) or source.number(
            chain, psm, "usds()", block
        ) != int(venue.underlying.address.hex, 16):
            raise ValueError("Arbitrum PSM assets differ from the reviewed sUSDS identity")
        provider = source.number(chain, psm, "rateProvider()", block)
        rate = source.number(chain, f"0x{provider:040x}", "getConversionRate()", block)
        if not rate:
            raise ValueError("Arbitrum sUSDS conversion rate is zero")
        balance = source.number(chain, token, "balanceOf(address)", block, int(holder, 16))
        samples.append((Decimal(balance) / 10**venue.token.decimals, Decimal(rate) / 10**27))
        providers.add(f"0x{provider:040x}")
    value = sum(((q0 + q1) / 2 * (p1 - p0) for (q0, p0), (q1, p1) in pairwise(samples)), Decimal(0))
    return {
        "status": "available",
        "value": str(value),
        "recognition": "accrual",
        "quality": "estimate",
        "basis": "average_daily_bridged_sUSDS_shares_times_PSM3_RAY_conversion_rate_change",
        "rate_providers": sorted(providers),
        "psm": psm,
    }


def collect(source, *, progress=None):
    # Bound shared daily prices and block resolutions once per collection.
    source.price = cache(source.price)
    result = {
        "savings_interest": {},
        "statement_allocations": {},
        "amm_fees": {},
        "completion_errors": {},
    }

    def attempt(key, fn):
        if progress:
            progress(key)
        try:
            with localcontext() as ctx:
                ctx.prec = 60
                return fn()
        except Exception as exc:
            result["completion_errors"][key] = type(exc).__name__
            return {"status": "unavailable", "reason": "reporting_source_unavailable"}

    for v in source.prime.venues:
        if v.id in VAULTS:
            result["savings_interest"][v.id] = attempt(
                "savings." + v.id, lambda v=v: collect_vault(source, v)
            )
        if v.id in SUSDS:
            result["statement_allocations"][v.id] = attempt(
                "susds." + v.id,
                lambda v=v: (
                    arbitrum_susds(source, v)
                    if v.id == "S43"
                    else native_supply(source, Allocation(v.id, "susds", "sUSDS", "B", "sUSDS"))
                ),
            )
        if v.id in ("S61", "S62", "S66"):
            result["amm_fees"][v.id] = attempt(
                "amm_fees." + v.id, lambda v=v: collect_fees(source, v)
            )
    result["statement_allocations"]["sparklend.weth"] = attempt(
        "supply.weth", lambda: weth_supply(source)
    )
    result["statement_allocations"]["morpho.sentora_rlusd"] = attempt(
        "supply.sentora", lambda: sentora(source)
    )
    return result
