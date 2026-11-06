#!/usr/bin/env python3
"""Repair frozen diagnostic sUSDS deposits using complete share-transfer logs.

The original positive-change override dropped peer receipts whenever an
ERC-4626 deposit occurred in the same transaction. No debt, other allocation,
published settlement or API revenue is altered by this diagnostic repair.
"""

import argparse
import gzip
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path
from tempfile import NamedTemporaryFile

from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import DEPOSIT
from settle.normalize.allocation_vault_deposits import deposit_transaction_value

VAULT = "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
ALM = "0x1601843c5e9bc251a3272907010afa41fa18347e"
ACCOUNT = "ethereum:" + ALM + ":" + VAULT
WHO = "0x" + ALM[2:].rjust(64, "0")
ZERO_TOPIC = "0x" + "0" * 64


def repair(batch, witness):
    if (
        batch["identity"] != "ethereum:" + witness["transaction_hash"]
        or batch["block"] != witness["block"]
        or batch["chain"] != "ethereum"
    ):
        raise ValueError("Mixed deposit witness disagrees with normalized transaction")
    seen = set()
    deposited, incoming, minted, cash = 0, 0, 0, 0
    for row in witness["logs"]:
        if (
            row["transaction_hash"] != witness["transaction_hash"]
            or row["block_number"] != witness["block"]
            or row["block_time"] != batch["timestamp"]
            or row["address"] != VAULT
        ):
            raise ValueError("Mixed deposit evidence contains a different event")
        if row["log_index"] in seen:
            raise ValueError("Duplicate mixed deposit evidence")
        seen.add(row["log_index"])
        if row["topic0"] == TRANSFER_TOPIC0:
            if row["topic1"] == WHO or row["topic2"] != WHO or len(row["data"]) != 66:
                raise ValueError("Mixed deposit repair requires incoming-only shares")
            amount = int(row["data"], 16)
            incoming += amount
            if row["topic1"] == ZERO_TOPIC:
                minted += amount
        elif row["topic0"] == DEPOSIT:
            if row["topic2"] != WHO or len(row["data"]) != 130:
                raise ValueError("Invalid mixed deposit receiver or layout")
            cash += int(row["data"][2:66], 16)
            deposited += int(row["data"][66:], 16)
        else:
            raise ValueError("Unexpected mixed deposit event")
    if not deposited or minted != deposited or incoming <= deposited:
        raise ValueError("Deposit mints and peer receipts do not match")
    matches = [m for m in batch["movements"] if m["account"] == ACCOUNT]
    if (
        len(matches) != 1
        or D(matches[0]["change"]) != D(cash) / 10**18
        or D(matches[0]["external_income"])
    ):
        raise ValueError("Snapshot is not the witnessed deposit-only overwrite")
    if not isinstance(witness["unit_assets"], int) or witness["unit_assets"] <= 0:
        raise ValueError("Invalid sUSDS execution price")
    old = matches[0]
    value = deposit_transaction_value(
        cash=D(cash) / 10**18,
        deposited_shares=deposited,
        fee_shares=0,
        net_shares=incoming,
        price=D(witness["unit_assets"]) / 10**18,
        scale=D(10**18),
    )
    fixed = {**old, "change": str(value)}
    result = {**batch, "movements": [fixed if m is old else m for m in batch["movements"]]}
    return result, {
        "identity": batch["identity"],
        "day": batch["day"],
        "block": batch["block"],
        "old_change": old["change"],
        "new_change": str(value),
        "restored_share_value": str(value - D(old["change"])),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("history", "evidence", "output", "audit"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if len({p.resolve() for p in (args.history, args.evidence, args.output, args.audit)}) != 4:
        raise ValueError("Repair inputs and outputs must be distinct")
    evidence = json.loads(gzip.decompress(args.evidence.read_bytes()))
    witnesses = {w["batch"]["identity"]: w for w in evidence}
    if len(witnesses) != len(evidence):
        raise ValueError("Duplicate mixed deposit witness")
    hashes = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (args.history, args.evidence, Path(__file__))
    }
    changes = []
    with NamedTemporaryFile(dir=args.output.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with gzip.open(args.history, "rt") as src, gzip.open(temporary, "wt") as dest:
            header = json.loads(next(src))
            if "diagnostic_mixed_deposit_patch" in header:
                raise ValueError("Mixed deposit repair already applied")
            header["diagnostic_mixed_deposit_patch"] = {"input_hashes": hashes}
            dest.write(json.dumps(header) + "\n")
            for line in src:
                batch = json.loads(line)
                witness = witnesses.get(batch["identity"])
                if witness:
                    if batch != witness["batch"]:
                        raise ValueError("Mixed deposit snapshot changed after witness capture")
                    batch, change = repair(batch, witness)
                    changes.append(change)
                dest.write(json.dumps(batch) + "\n")
        if len(changes) != len(witnesses):
            raise ValueError("Mixed deposit evidence missing from snapshot")
        temporary.replace(args.output)
    finally:
        temporary.unlink(missing_ok=True)
    args.audit.write_text(
        json.dumps(
            {
                "input_hashes": hashes,
                "repaired": len(changes),
                "restored_share_value": str(
                    sum((D(c["restored_share_value"]) for c in changes), D(0))
                ),
                "output_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                "changes": changes,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
