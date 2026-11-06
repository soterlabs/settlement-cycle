"""Additional reporting-only Spark sources for the complete statement perimeter."""

from dataclasses import replace
from decimal import localcontext
from functools import cache

from ..domain.primes import Address
from .spark_onchain import SPWETH
from .spark_reconciliation import aave_supply, bridged_susds, vault_supply
from .spark_savings import VAULTS, collect_vault
from .spark_v4_fees import collect as collect_fees

SUSDS = ("S32", "S37", "S43", "S47", "S51")
SENTORA = "0xfc8c624b6080a0a780583799f2a862de936f6e22"
RLUSD = "0x8292bb45bf1ee4d140127049757c2e0ff06317ed"


def weth_supply(source):
    """Supply interest in WETH translated daily; price revaluation is separate."""
    base = next(v for v in source.prime.venues if v.id == "S70")
    venue = replace(
        base,
        id="sparklend.weth",
        token=replace(base.token, address=Address.from_str(SPWETH), decimals=18),
        underlying=replace(
            base.underlying,
            address=Address.from_str("0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"),
            decimals=18,
            symbol="WETH",
        ),
    )
    return aave_supply(source, venue)


def sentora(source):
    """Reuse native net-vault NAV return, with an explicit reporting identity."""
    base = next(v for v in source.prime.venues if v.id == "S13")
    token = replace(base.token, address=Address.from_str(SENTORA), decimals=18)
    underlying = replace(
        base.underlying, address=Address.from_str(RLUSD), decimals=18, symbol="RLUSD"
    )
    venue = replace(base, id="reporting.sentora", token=token, underlying=underlying)
    block = source.block("ethereum", source.period.end)
    if hasattr(source, "code") and source.code("ethereum", SENTORA, block) == "0x":
        return vault_supply(source, venue)
    if source.number("ethereum", SENTORA, "asset()", block) != int(RLUSD, 16):
        raise ValueError("Unexpected Sentora underlying")
    return vault_supply(source, venue)


def arbitrum_susds(source, venue):
    return bridged_susds(source, venue)


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
                lambda v=v: arbitrum_susds(source, v) if v.id != "S32" else vault_supply(source, v),
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
    from .spark_v4_fees import reconcile_capital

    try:
        with localcontext() as ctx:
            ctx.prec = 60
            reconcile_capital(source, result["amm_fees"])
    except Exception as exc:
        result["completion_errors"]["amm_capital"] = type(exc).__name__
    return result
