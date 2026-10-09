#!/usr/bin/env python3
"""Diagnostic proportional Savings repayment policy; never settlement revenue.

Contract arithmetic: sparkdotfi/spark-vaults-v2 at
51c6d7a1da85944804ba87234f2eac13dba8330e, SparkVault.sol
setVsr, drip, nowChi and _rpow. Cash transfers need not call drip.
The proportional repayment policy is an explicit working assumption, not a
contract instruction: repayments retire principal and accrued interest pro rata.
Excess returns remain a prepaid claim; a later take refunds that claim first.
"""

import argparse
import gzip
import hashlib
import json
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from audit_spark_savings_liability import RAY, TOPICS, audit_vault

from settle.extract._keccak import keccak256

VSR = "0x" + keccak256(b"VsrSet(address,uint256,uint256)").hex()
TRANSFER = "0x" + keccak256(b"Transfer(address,address,uint256)").hex()
ZERO = D(0)


def rpow(x, n):
    """Solidity ray exponentiation, including half-up intermediate rounding."""
    if x < 0 or n < 0:
        raise ValueError("Negative ray exponentiation input")
    z = x if n % 2 else RAY
    n //= 2
    while n:
        x = (x * x + RAY // 2) // RAY
        if n % 2:
            z = (z * x + RAY // 2) // RAY
        n //= 2
    return z


def split_vault(group, rates, cash_groups):
    with localcontext() as ctx:
        ctx.prec = 60
        return _split_vault(group, rates, cash_groups)


def _split_vault(g, rates, cash_groups):
    if any(rates[k] != g[k] for k in ("venue", "chain", "vault", "pin")):
        raise ValueError("Savings rate evidence uses a different vault/pin")
    rows = {}

    def add(r, kind):
        if r["block_number"] > g["pin"]:
            raise ValueError("Savings evidence exceeds pin")
        key = (r["block_number"], r["log_index"])
        if key in rows and rows[key] != (r, kind):
            raise ValueError("Conflicting Savings event")
        rows[key] = (r, kind)

    for r in g["rows"]:
        if r["address"] != g["vault"]:
            raise ValueError("Savings liability evidence uses another vault")
        add(r, TOPICS[r["topic0"]])
    for r in rates["rows"]:
        if r["address"] != g["vault"] or r["topic0"] != VSR:
            raise ValueError("Invalid Savings rate event")
        add(r, "rate")
    for cg in cash_groups:
        if cg["chain"] != g["chain"]:
            continue
        if cg["pin"] != g["pin"]:
            raise ValueError("Savings cash evidence uses another pin")
        for r in cg["rows"]:
            if r["topic0"] != TRANSFER or r["address"] != g["underlying"]:
                continue
            sender, receiver = "0x" + r["topic1"][-40:], "0x" + r["topic2"][-40:]
            if (sender, receiver) == (g["vault"], g["alm"]):
                add(r, "cash_take")
            elif (sender, receiver) == (g["alm"], g["vault"]):
                add(r, "cash_return")
    supply, chi, rho, vsr, emitted, accrued = 0, RAY, None, RAY, 0, 0
    principal, interest, prepaid = ZERO, ZERO, ZERO
    borrowed, returned_principal, paid_interest, net_cash = ZERO, ZERO, ZERO, ZERO
    operations = []
    total_taken, total_returned = ZERO, ZERO

    def accrue(timestamp, row=None):
        nonlocal accrued, interest, prepaid, paid_interest
        now_chi = chi if rho is None else rpow(vsr, timestamp - rho) * chi // RAY
        total = emitted + supply * now_chi // RAY - supply * chi // RAY
        delta = D(total - accrued)
        if delta < 0:
            raise ValueError("Savings cumulative accrual decreased")
        accrued = total
        consumed = min(prepaid, delta)
        prepaid -= consumed
        interest += delta - consumed
        paid_interest += consumed
        if consumed:
            operations.append(operation("prepaid_interest", consumed, row, timestamp))
        return now_chi

    def operation(kind, amount, row, timestamp):
        return {
            "kind": kind,
            "amount": str(amount / 10 ** g["decimals"]),
            "source": g["chain"] + ":" + g["vault"],
            "venue": g["venue"],
            "transaction_hash": row["transaction_hash"] if row else None,
            "block": row["block_number"] if row else g["pin"],
            "log_index": row["log_index"] if row else 2**31,
            "timestamp": timestamp,
        }

    for (_, _), (r, kind) in sorted(rows.items()):
        timestamp = r["block_time"]
        now_chi = accrue(timestamp, r)
        data = r["data"][2:]
        values = [int(data[i : i + 64], 16) for i in range(0, len(data), 64)]
        if kind == "rate":
            old, new = values
            if old != vsr or rho != timestamp:
                raise ValueError("Savings rate update missing preceding Drip")
            vsr = new
        elif kind == "drip":
            new, diff = values
            if new != now_chi or diff != supply * new // RAY - supply * chi // RAY:
                raise ValueError("Savings rate schedule does not reproduce Drip")
            emitted += diff
            chi, rho = new, timestamp
        elif kind in ("deposit", "withdraw"):
            if rho != timestamp:
                raise ValueError("Savings share change missing preceding Drip")
            supply += values[1] if kind == "deposit" else -values[1]
            if supply < 0:
                raise ValueError("Savings supply is negative")
        elif kind == "cash_take":
            amount = D(values[0])
            net_cash += amount
            total_taken += amount
            refund = min(prepaid, amount)
            prepaid -= refund
            if refund:
                operations.append(operation("refund", refund, r, timestamp))
            amount -= refund
            principal += amount
            borrowed += amount
            if amount:
                operations.append(operation("draw", amount, r, timestamp))
        elif kind == "cash_return":
            amount = D(values[0])
            net_cash -= amount
            total_returned += amount
            liability = principal + interest
            retired = min(amount, liability)
            p = principal if retired == liability else retired * principal / liability
            i = retired - p
            principal -= p
            interest -= i
            returned_principal += p
            paid_interest += i
            prepaid += amount - retired
            for name, value in (("repay", p), ("interest", i), ("prepay", amount - retired)):
                if value:
                    operations.append(operation(name, value, r, timestamp))
    state = g["state"]
    now_chi = accrue(state["block_timestamp"])
    if (
        supply != state["totalSupply()"]
        or chi != state["chi()"]
        or rho != state["rho()"]
        or vsr != state["vsr()"]
        or supply * now_chi // RAY != state["totalAssets()"]
    ):
        raise ValueError("Savings rate reconstruction differs from pinned state")
    residual = principal + interest - prepaid - net_cash - D(accrued)
    if abs(residual) > D("1e-35"):
        raise ValueError("Savings principal/interest cash conservation failed")

    def human(x):
        return str(D(x) / 10 ** g["decimals"])

    liability_audit = audit_vault(
        g, {"take_to_alm": human(total_taken), "return_to_vault": human(total_returned)}
    )
    return {
        "venue": g["venue"],
        "principal": human(principal),
        "interest": human(interest),
        "prepaid": human(prepaid),
        "borrowed_principal": human(borrowed),
        "returned_principal": human(returned_principal),
        "paid_interest": human(paid_interest),
        "net_cash_taken": human(net_cash),
        "accrued_interest": human(accrued),
        "conservation_residual": human(residual),
        "rate_events": len(rates["rows"]),
        "contract_liability_audit": liability_audit,
        "operations": operations,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = [
        Path("tests/fixtures/" + name)
        for name in (
            "spark_savings_v2_liability.json.gz",
            "spark_savings_v2_rates.json",
            "spark_savings_v2_funding.json.gz",
        )
    ]

    def read(p):
        raw = p.read_bytes()
        return json.loads(gzip.decompress(raw) if p.suffix == ".gz" else raw)

    groups, rates, cash = map(read, paths)
    result = {
        "policy": "PROVISIONAL: proportional repayment of principal and accrued VSR; excess return is prepaid funding.",
        "scope": "Diagnostic only. No Sky debt, settlement or API amounts are changed.",
        "vaults": [
            split_vault(g, next(r for r in rates if r["venue"] == g["venue"]), cash) for g in groups
        ],
        "input_hashes": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
