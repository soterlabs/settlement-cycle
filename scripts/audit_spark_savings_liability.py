#!/usr/bin/env python3
"""Reconcile Spark Savings accrued liabilities without assigning Sky funding.

Read-only, inception-to-pin audit. Uses integer contract units and checks
every emitted Drip against reconstructed supply and chi. No cash return is
arbitrarily divided into principal/interest and no settlement is changed.
Source: sparkdotfi/spark-vaults-v2 at
51c6d7a1da85944804ba87234f2eac13dba8330e, src/SparkVault.sol:153-167,384-399.
"""
import argparse
import gzip
import hashlib
import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from settle.extract._keccak import keccak256

RAY = 10**27
TOPICS = {"0x" + keccak256(signature.encode()).hex(): name for name, signature in (
    ("deposit", "Deposit(address,address,uint256,uint256)"),
    ("withdraw", "Withdraw(address,address,address,uint256,uint256)"),
    ("drip", "Drip(uint256,uint256)"),
    ("take", "Take(address,uint256)"),
)}


def audit_vault(group, cash):
    supply, chi, accrued, rounding, deposits, withdrawals = 0, RAY, 0, 0, 0, 0
    takes = defaultdict(int)
    unique = {}
    for row in group["rows"]:
        if row["address"] != group["vault"] or row["block_number"] > group["pin"]:
            raise ValueError("Savings liability evidence outside the requested vault/pin")
        key = row["block_number"], row["log_index"]
        if key in unique and unique[key] != row:
            raise ValueError("Conflicting Savings liability event")
        unique[key] = row
    for _, row in sorted(unique.items()):
        kind = TOPICS[row["topic0"]]
        data = row["data"][2:]
        if len(data) != (64 if kind == "take" else 128):
            raise ValueError("Invalid Savings liability event shape")
        values = [int(data[i:i+64], 16) for i in range(0, len(data), 64)]
        if kind == "drip":
            new_chi, diff = values
            if new_chi < chi or diff != supply * new_chi // RAY - supply * chi // RAY:
                raise ValueError("Savings Drip disagrees with reconstructed supply/chi")
            accrued += diff
            chi = new_chi
        elif kind == "take":
            takes["0x" + row["topic1"][-40:]] += values[0]
        else:
            assets, shares = values
            before = supply * chi // RAY
            if kind == "deposit":
                supply += shares
                deposits += assets
                rounding += supply * chi // RAY - before - assets
            else:
                supply -= shares
                withdrawals += assets
                rounding += supply * chi // RAY - before + assets
            if supply < 0:
                raise ValueError("Savings history misses opening shares")
    state = group["state"]
    if supply != state["totalSupply()"] or chi != state["chi()"]:
        raise ValueError("Savings event history differs from pinned supply/chi")
    tail = state["totalAssets()"] - supply * chi // RAY
    if tail < 0:
        raise ValueError("Savings accrued liability decreased after final Drip")
    accrued += tail
    if state["totalAssets()"] != deposits - withdrawals + accrued + rounding:
        raise ValueError("Savings share liability does not reconcile")
    scale = 10**group["decimals"]
    incoming = Decimal(cash["take_to_alm"]) * scale
    outgoing = Decimal(cash["return_to_vault"]) * scale
    if incoming != int(incoming) or outgoing != int(outgoing):
        raise ValueError("Savings cash evidence has fractional base units")
    if takes.get(group["alm"], 0) != int(incoming):
        raise ValueError("Savings liability and ALM cash inventories disagree")
    net_taken = sum(takes.values()) - int(outgoing)
    # Residual includes any direct non-ALM donation/return. We do not label
    # it revenue; full boundary payment evidence would be needed for that.
    other_cash = state["underlying_balance"] - (deposits - withdrawals - net_taken)
    signed_liability = net_taken + accrued + rounding - other_cash
    if max(signed_liability, 0) != state["assetsOutstanding()"]:
        raise ValueError("Savings outstanding liability does not reconcile")
    def human(value):
        return str(Decimal(value) / scale)
    return {
        "venue": group["venue"], "chain": group["chain"], "pin": group["pin"],
        "events": len(unique), "total_assets": human(state["totalAssets()"]),
        "idle_cash": human(state["underlying_balance"]),
        "assets_outstanding": human(state["assetsOutstanding()"]),
        "net_cash_taken": human(net_taken), "accrued_vsr": human(accrued),
        "share_rounding": human(rounding), "other_cash_residual": human(other_cash),
        "pending_accrual_after_last_drip": human(tail),
        "non_alm_takes": {a: human(v) for a, v in takes.items() if a != group["alm"]},
        "liability_less_net_cash": human(state["assetsOutstanding()"] - net_taken),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("events", "cash-inventory", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    raw = args.events.read_bytes()
    groups = json.loads(gzip.decompress(raw) if args.events.suffix == ".gz" else raw)
    cash = json.loads(args.cash_inventory.read_text())["by_venue"]
    result = {
        "scope": "Inception-to-August-pin liability audit, not monthly revenue or certified Sky funding attribution.",
        "vaults": [audit_vault(g, cash[g["venue"]]) for g in groups],
        "input_hashes": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (args.events, args.cash_inventory)},
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
