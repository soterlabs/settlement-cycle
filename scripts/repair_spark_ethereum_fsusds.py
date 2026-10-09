#!/usr/bin/env python3
"""Repair Ethereum fsUSDS tracing using verified sUSDS assets and cash.

Historical asset() calls prove S17 uses sUSDS despite the legacy report config.
Only the seven witnessed wrapper movements are repriced; published revenue,
all other asset movements and Sky debt stay unchanged.
"""

import argparse
import gzip
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path
from tempfile import NamedTemporaryFile

from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import DEPOSIT, WITHDRAW

VAULT = "0x2bbe31d63e6813e3ac858c04dae43fb2a72b0d11"
SUSDS = "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
RESERVE = "0x52aa899454998be5b000ad077a46bbe360f4e497"
ALM = "0x1601843c5e9bc251a3272907010afa41fa18347e"
ACCOUNT = "ethereum:" + ALM + ":" + VAULT
WHO = "0x" + ALM[2:].rjust(64, "0")
ZERO_TOPIC = "0x" + "0" * 64


def repair(batch, witness):
    receipt = witness["receipt"]
    if (
        batch["chain"] != "ethereum"
        or batch["identity"] != "ethereum:" + receipt["transactionHash"]
        or batch["block"] != int(receipt["blockNumber"], 16)
        or receipt["status"] != "0x1"
        or witness["asset"] != SUSDS
    ):
        raise ValueError("Ethereum fsUSDS evidence metadata or actual asset changed")
    events = []
    delta = 0
    seen = set()
    for row in receipt["logs"]:
        index = int(row["logIndex"], 16)
        if index in seen or row["transactionHash"] != receipt["transactionHash"]:
            raise ValueError("Duplicate or inconsistent Ethereum fsUSDS receipt")
        seen.add(index)
        if row["address"] != VAULT:
            continue
        topics = row["topics"]
        if topics[0] == TRANSFER_TOPIC0:
            amount = int(row["data"], 16)
            delta += (amount if topics[2] == WHO else 0) - (amount if topics[1] == WHO else 0)
        elif topics[0] in (DEPOSIT, WITHDRAW):
            if len(row["data"]) != 130 or any(t != WHO for t in topics[1:]):
                raise ValueError("Unexpected Ethereum fsUSDS vault event")
            sign = 1 if topics[0] == DEPOSIT else -1
            events.append((sign * int(row["data"][2:66], 16), sign * int(row["data"][66:], 16)))
    if len(events) != 1 or events[0][1] != delta:
        raise ValueError("Ethereum fsUSDS share transfers disagree with vault event")
    assets, _shares = events[0]
    matches = [m for m in batch["movements"] if m["account"] == ACCOUNT]
    if (
        len(matches) != 1
        or D(matches[0]["change"]) != D(assets) / 10**18
        or D(matches[0]["external_income"])
    ):
        raise ValueError("Ethereum fsUSDS snapshot is not the old underlying-at-par input")
    # Actual sUSDS cash goes to/from Fluid's liquidity reserve, while the
    # outer shares are minted/burned by fsUSDS. Check that exact payment leg.
    expected = (
        (WHO, "0x" + RESERVE[2:].rjust(64, "0"))
        if assets > 0
        else ("0x" + RESERVE[2:].rjust(64, "0"), WHO)
    )
    payments = [
        r
        for r in receipt["logs"]
        if r["address"] == SUSDS
        and r["topics"][0] == TRANSFER_TOPIC0
        and tuple(r["topics"][1:]) == expected
    ]
    if sum(int(r["data"], 16) for r in payments) != abs(assets):
        raise ValueError("Ethereum fsUSDS underlying cash disagrees with vault event")
    price = D(witness["underlying_unit_assets"]) / 10**18
    if not price.is_finite() or price <= 0:
        raise ValueError("Invalid Ethereum sUSDS historical price")
    old = matches[0]
    fixed = {
        **old,
        "value_before": str(D(old["value_before"]) * price),
        "change": str(D(old["change"]) * price),
    }
    result = {**batch, "movements": [fixed if m is old else m for m in batch["movements"]]}
    old_gap = D(batch["minted"]) - sum(
        (D(m["change"]) - D(m["external_income"]) for m in batch["movements"]), D(0)
    )
    new_gap = D(batch["minted"]) - sum(
        (D(m["change"]) - D(m["external_income"]) for m in result["movements"]), D(0)
    )
    return result, {
        "identity": batch["identity"],
        "day": batch["day"],
        "block": batch["block"],
        "old_change": old["change"],
        "new_change": fixed["change"],
        "old_signed_residual": str(old_gap),
        "new_signed_residual": str(new_gap),
        "restored_share_value": str(D(fixed["change"]) - D(old["change"])),
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
        raise ValueError("Duplicate Ethereum fsUSDS witness")
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
            if "diagnostic_ethereum_fsusds_patch" in header:
                raise ValueError("Ethereum fsUSDS repair already applied")
            header["diagnostic_ethereum_fsusds_patch"] = {"input_hashes": hashes}
            dest.write(json.dumps(header) + "\n")
            for line in src:
                batch = json.loads(line)
                witness = witnesses.get(batch["identity"])
                if witness:
                    if batch != witness["batch"]:
                        raise ValueError("Ethereum fsUSDS snapshot changed after witness capture")
                    batch, change = repair(batch, witness)
                    changes.append(change)
                dest.write(json.dumps(batch) + "\n")
        if len(changes) != len(witnesses):
            raise ValueError("Ethereum fsUSDS evidence missing from snapshot")
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
