"""Explicit reporting accounting, independent of MSC settlement calculations.

Policy decisions are not inferred from chain balances. New publications retain
these decisions and their source evidence; old pinned stocks stay unchanged.
"""

from datetime import date
from decimal import Decimal as D
from decimal import localcontext

ATLAS = "https://github.com/sky-ecosystem/next-gen-atlas/blob/6daa01968a0fc3fe67fdedc45761cf33ba114515/content/A.2%20-%20The-Support-Scope.md"
WINDOWS = {
    "S24": (date(2025, 11, 13), date(2026, 6, 25)),
    "S62": (date(2026, 5, 26), date.max),
    "psm3_usdc": (date(2025, 11, 13), date.max),
}
SAFE_TX = "0x742a35dbd975d28770014edb1a55e52597555a6ca801c80980230651fde33ab3"
SAFE_FROM = "0x92e4629a4510af5819d7d1601464c233599ff5ec"
SAFE_TO = "0xd5ff8bdef23fc3c8ff6815c6b4051f9809c877a5"
USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"


def number(value):
    v = D(str(value))
    if not v.is_finite() or v < 0:
        raise ValueError("Expected a finite nonnegative amount")
    return v


def active(key, day):
    lo, hi = WINDOWS[key]
    return lo <= day <= hi


def fee_share(gross, sky_principal, fees):
    """Split fees by the value of Sky's leg / total LP principal, not fee token."""
    gross, sky_principal, fees = map(number, (gross, sky_principal, fees))
    with localcontext() as ctx:
        ctx.prec = 100
        principal = gross - fees
        if principal < 0 or sky_principal > principal or (not principal and fees):
            raise ValueError("LP fee ownership needs funded principal")
        sky_fees = fees * sky_principal / principal if principal else D(0)
        return dict(
            sky_fees=str(sky_fees),
            prime_fees=str(fees - sky_fees),
            sky_value=str(sky_principal + sky_fees),
            prime_value=str(gross - sky_principal - sky_fees),
        )


def collect(reader, prime, doc, cutoff):
    """Capture disjoint SDE legs and the provisional investment at closing."""
    from .balance_sheet_sources import rpc
    from .cash_flow.collector import TRANSFER

    result = dict(schema_version="spark-bs-accounting.v1", atlas=ATLAS, sde={}, investments={})
    for key in ("S24", "S62"):
        if not active(key, cutoff):
            result["sde"][key] = dict(status="available", value="0")
        else:
            pos = doc["positions"].get(key, {})
            ownership = doc["ownership"].get(key, {})
            if pos.get("status") == "available" and (ownership or D(pos["value"]) == 0):
                result["sde"][key] = dict(status="available", value=ownership.get("sky_value", "0"))
            else:
                result["sde"][key] = dict(status="unavailable", value=None)
    for chain, spec in prime.psm.items():
        ch = str(chain)
        if ch == "ethereum":
            continue
        key = "psm." + ch
        try:
            value = D(0)
            proof = dict(chain=ch, contract=spec.address.hex, holder=prime.alm[chain].hex)
            if active("psm3_usdc", cutoff) and reader.code(ch, spec.address.hex) != "0x":
                addr = spec.address.hex
                shares = reader.number(ch, addr, "shares(address)", int(prime.alm[chain].hex, 16))
                if shares:
                    supply = reader.number(ch, addr, "totalShares()")
                    token = "0x" + f"{reader.number(ch, addr, 'usdc()'):040x}"
                    decimals = reader.number(ch, token, "decimals()")
                    if decimals != 6 or not 0 < shares <= supply:
                        raise ValueError("Invalid PSM3 USDC share")
                    with localcontext() as ctx:
                        ctx.prec = 100
                        value = D(reader.balance(ch, token, addr)) * shares / supply / 10**6
            gross = doc["extra_assets"][key]
            if gross["status"] != "available" or value > D(gross["value"]):
                raise ValueError("PSM3 gross stock missing or below Sky leg")
            result["sde"][key] = dict(status="available", value=str(value), proof=proof)
        except Exception as e:
            result["sde"][key] = dict(status="unavailable", value=None, reason=type(e).__name__)
    try:
        receipt = rpc("ethereum", "eth_getTransactionReceipt", [SAFE_TX])
        if receipt["status"] != "0x1" or receipt["transactionHash"].lower() != SAFE_TX:
            raise ValueError("Arkis payment failed")
        matches = [
            log
            for log in receipt["logs"]
            if log["address"].lower() == USDS
            and len(log["topics"]) == 3
            and log["topics"][0] == TRANSFER
            and log["topics"][1][-40:] == SAFE_FROM[2:]
            and log["topics"][2][-40:] == SAFE_TO[2:]
            and int(log["data"], 16) == 4_000_000 * 10**18
        ]
        header = rpc("ethereum", "eth_getBlockByNumber", [receipt["blockNumber"], False])
        if (
            len(matches) != 1
            or header["hash"] != receipt["blockHash"]
            or int(receipt["blockNumber"], 16) > reader.pins["ethereum"]["block"]
        ):
            raise ValueError("Arkis investment not evidenced at cutoff")
        result["investments"]["arkis_safe"] = dict(
            status="available",
            value="4000000",
            quality="estimate",
            receipt=receipt,
            block=header,
            basis="Operator-approved provisional cost; SAFE entitlement and impairment unconfirmed",
        )
    except Exception as e:
        result["investments"]["arkis_safe"] = dict(
            status="unavailable", value=None, reason=type(e).__name__
        )
    return result


PSMS = ("psm.base", "psm.arbitrum", "psm.optimism", "psm.unichain")


def validate(doc):
    """Fail closed on incomplete offsets; retained legacy publications are untouched."""
    a = doc["accounting"]
    if a["schema_version"] != "spark-bs-accounting.v1" or a["atlas"] != ATLAS:
        raise ValueError("Unknown closing accounting policy")
    if set(a["sde"]) != {"S24", "S62", *PSMS} or set(a["investments"]) != {"arkis_safe"}:
        raise ValueError("Incomplete accounting perimeter")
    for family in ("sde", "investments"):
        for row in a[family].values():
            if row["status"] == "available":
                number(row["value"])
            elif row["status"] != "unavailable" or row["value"] is not None:
                raise ValueError("Invalid accounting availability")
    day = date.fromisoformat(doc["cutoff"])
    for key, row in a["sde"].items():
        if row["status"] != "available":
            continue
        value = number(row["value"])
        if not active("psm3_usdc" if key in PSMS else key, day) and value:
            raise ValueError("SDE outside historical Atlas window")
        if key in PSMS:
            gross = doc["extra_assets"][key]
            if gross["status"] != "available" or value > number(gross["value"]):
                raise ValueError("PSM offset exceeds available gross holding")
            proof = row["proof"]
            if proof["chain"] != key.removeprefix("psm."):
                raise ValueError("PSM chain mismatch")
            from ..extract._keccak import keccak256

            def read(method, contract, signature=None, *args, proof=proof):
                data = (
                    "0x"
                    + keccak256(signature.encode()).hex()[:8]
                    + "".join(f"{arg:064x}" for arg in args)
                    if signature
                    else None
                )
                matches = {
                    r["result"]
                    for r in doc["reads"]
                    if r["chain"] == proof["chain"]
                    and r["contract"].lower() == contract.lower()
                    and r["method"] == method
                    and (data is None or r.get("data") == data)
                }
                if len(matches) != 1:
                    raise ValueError("Missing or conflicting PSM getter evidence")
                result = matches.pop()
                return result if method == "eth_getCode" else int(result, 16)

            expected = D(0)
            addr = proof["contract"]
            if active("psm3_usdc", day) and read("eth_getCode", addr) != "0x":
                shares = read("eth_call", addr, "shares(address)", int(proof["holder"], 16))
                if shares:
                    supply = read("eth_call", addr, "totalShares()")
                    token = "0x" + f"{read('eth_call', addr, 'usdc()'):040x}"
                    decimals = read("eth_call", token, "decimals()")
                    if decimals != 6 or not 0 < shares <= supply:
                        raise ValueError("Invalid evidenced PSM3 share")
                    balance = read("eth_call", token, "balanceOf(address)", int(addr, 16))
                    with localcontext() as ctx:
                        ctx.prec = 100
                        expected = D(balance) * shares / supply / 10**6
            if value != expected:
                raise ValueError("PSM offset differs from raw closing getters")

        else:
            pos = doc["positions"].get(key, {})
            ownership = doc["ownership"].get(key, {})
            if active(key, day) and (
                pos.get("status") != "available"
                or value != number(ownership.get("sky_value", "0"))
                or (number(pos["value"]) and not ownership)
            ):
                raise ValueError("SDE differs from closing LP ownership")
    safe = a["investments"]["arkis_safe"]
    if safe["status"] == "available":
        from .cash_flow.collector import TRANSFER

        r, b = safe["receipt"], safe["block"]
        matches = [
            log
            for log in r["logs"]
            if log["address"].lower() == USDS
            and len(log["topics"]) == 3
            and log["topics"][0] == TRANSFER
            and log["topics"][1][-40:].lower() == SAFE_FROM[2:]
            and log["topics"][2][-40:].lower() == SAFE_TO[2:]
            and int(log["data"], 16) == 4_000_000 * 10**18
        ]
        if (
            safe.get("quality") != "estimate"
            or number(safe["value"]) != 4_000_000
            or r["transactionHash"].lower() != SAFE_TX
            or r["status"] != "0x1"
            or b["hash"] != r["blockHash"]
            or b["number"] != r["blockNumber"]
            or int(r["blockNumber"], 16) > doc["pins"]["ethereum"]["block"]
            or len(matches) != 1
        ):
            raise ValueError("Invalid provisional Arkis payment evidence")
    return a


def project(doc):
    a = validate(doc)

    def unavailable(reason):
        return dict(status="unavailable", value=None, reason=reason)

    def cell(value, **extra):
        return dict(status="available", value=str(value), **extra)

    out = {
        "investment.arkis_safe": a["investments"]["arkis_safe"],
        "sky_debt_gross": doc["sky_debt"],
    }
    with localcontext() as ctx:
        ctx.prec = 100
        for key in PSMS:
            gross, sky = doc["extra_assets"][key], a["sde"][key]
            out["position." + key] = (
                cell(number(gross["value"]) - number(sky["value"]))
                if gross["status"] == sky["status"] == "available"
                else unavailable("PSM3 ownership evidence incomplete")
            )
        if all(r["status"] == "available" for r in a["sde"].values()):
            offset = sum((number(r["value"]) for r in a["sde"].values()), D(0))
            out["sky_debt_sde"] = cell(-offset)
            debt = doc["sky_debt"]
            if debt["status"] == "available" and number(debt["value"]) >= offset:
                out["sky_debt"] = cell(number(debt["value"]) - offset)
            else:
                out["sky_debt"] = unavailable("Gross debt unavailable or below SDE offset")
        else:
            out["sky_debt_sde"] = unavailable("Historical SDE coverage incomplete")
            out["sky_debt"] = unavailable("Cannot offset debt with incomplete SDE coverage")
    return out
