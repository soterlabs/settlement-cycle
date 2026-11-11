"""Attach verified September receipt positions to the saved Spark funding history.

This is a research-input migration, not a settlement restatement. Future captures
use the ordinary venue normalizer once the MSC coverage configuration is enabled.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

from audit_saved_funding_gap import read
from audit_spark_rlusd_positions import analyze
from audit_spark_rlusd_routes import ALM, SENTORA, SPRLUSD, TRANSFER, ZERO, address

from settle.extract.aave_reconstruct import BT_T0, BURN_T0, MINT_T0, ray_div
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch
from settle.normalize.allocation_history_cache import load_history, save_history

TREASURY = "0xb137e7d16564c81ae2b0c8ee6b55de81dd46ece5"
DEPOSIT = "0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7"
WITHDRAW = "0xfbde797d201c681b91056529119e0b02407c7bb96a4a2c75c01fc9667232c8db"


def words(row):
    h = row["data"].removeprefix("0x")
    return [int(h[i : i + 64], 16) for i in range(0, len(h), 64)]


def receipt_movements(logs):
    unique = {}
    for r in logs:
        key = r["block_number"], r["log_index"]
        if key in unique and unique[key] != r:
            raise ValueError("Conflicting duplicate receipt evidence")
        unique[key] = r
    txs = defaultdict(list)
    for r in sorted(unique.values(), key=lambda r: (r["block_number"], r["log_index"])):
        txs[r["transaction_hash"]].append(r)
    balances = {SENTORA: 0, SPRLUSD: 0}
    result = {}
    with localcontext() as ctx:
        ctx.prec = 60
        for tx, rows in txs.items():
            movements = []
            for token in balances:
                change = gift = 0
                price = None
                cash = shares = 0
                for r in rows:
                    if r["address"] != token:
                        continue
                    w = words(r)
                    if token == SENTORA:
                        if r["topic0"] == TRANSFER:
                            sender, receiver = address(r["topic1"]), address(r["topic2"])
                            if (
                                ALM in (sender, receiver)
                                and ZERO not in (sender, receiver)
                                and w[0]
                            ):
                                raise ValueError("Unexpected Sentora receipt transfer")
                            change += w[0] * ((receiver == ALM) - (sender == ALM))
                        elif r["topic0"] == DEPOSIT and address(r["topic2"]) == ALM:
                            cash += w[0]
                            shares += w[1]
                        elif r["topic0"] == WITHDRAW and address(r["topic3"]) == ALM:
                            cash -= w[0]
                            shares -= w[1]
                    elif r["topic0"] in (MINT_T0, BURN_T0, BT_T0):
                        sender, receiver = address(r["topic1"]), address(r["topic2"])
                        if r["topic0"] == MINT_T0 and receiver == ALM:
                            amount = w[0] - w[1]
                            change += (1 if amount >= 0 else -1) * ray_div(abs(amount), w[2])
                        elif r["topic0"] == BURN_T0 and sender == ALM:
                            change -= ray_div(w[0] + w[1], w[2])
                        elif r["topic0"] == BT_T0:
                            change += w[0] * ((receiver == ALM) - (sender == ALM))
                            if receiver == ALM and w[0]:
                                if sender != TREASURY:
                                    raise ValueError("Unreviewed spRLUSD receipt payer")
                                gift += w[0]
                        if ALM in (sender, receiver):
                            price = D(w[-1]) / D(10**27)
                if not change and not gift:
                    continue
                if token == SENTORA:
                    if not shares or shares != change or cash * shares <= 0:
                        raise ValueError("Sentora shares do not match cash events")
                    # Use actual exchanged cash/share ratio, as the normalizer
                    # does for withdrawals. Issuance rounding is sub-wei per
                    # share; do not invent an independent historical NAV.
                    price = D(cash) / D(shares)
                if price is None or price <= 0:
                    raise ValueError("Missing execution price")
                before = D(balances[token]) * price / D(10**18)
                delta = D(change) * price / D(10**18)
                income = D(gift) * price / D(10**18)
                balances[token] += change
                if balances[token] < 0:
                    raise ValueError("Negative receipt balance")
                movements.append(AssetMovement(f"ethereum:{ALM}:{token}", before, delta, income))
            if movements:
                result["ethereum:" + tx] = (rows[0], tuple(movements))
    return result, balances


def attach(history, patches):
    destinations = {m.account for _, ms in patches.values() for m in ms}
    if any(m.account in destinations for b in history.batches for m in b.movements):
        raise ValueError("Receipt positions already present; refuse duplicate attachment")
    batches = []
    remaining = dict(patches)
    for b in history.batches:
        found = remaining.pop(b.identity, None)
        if found:
            row, movements = found
            if b.block != row["block_number"] or b.timestamp != row["block_time"]:
                raise ValueError("Receipt evidence/history block mismatch")
            b = replace(b, movements=b.movements + movements)
        batches.append(b)
    for identity, (row, movements) in remaining.items():
        # Only standalone gifts may have been omitted by the old capture.
        if any(m.change != m.external_income for m in movements):
            raise ValueError("Cash-bearing receipt transaction missing from history")
        batches.append(
            CapitalBatch(
                identity,
                datetime.fromtimestamp(row["block_time"], UTC).date(),
                row["block_time"],
                "ethereum",
                row["block_number"],
                movements,
                log_index=row["log_index"],
            )
        )
    venues = {
        **history.venue_accounts,
        "S70": f"ethereum:{ALM}:{SPRLUSD}",
        "S72": f"ethereum:{ALM}:{SENTORA}",
    }
    return replace(
        history,
        batches=tuple(sorted(batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index))),
        venue_accounts=venues,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--history", type=Path, required=True)
    p.add_argument("--evidence-dir", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    paths = [
        a.history,
        *[
            a.evidence_dir / n
            for n in (
                "receipt-token-joined-logs.json.gz",
                "boundary-calls.json",
                "position-calls.json",
            )
        ],
    ]
    protected = {path.resolve() for path in paths}
    if (
        a.output.resolve() in protected
        or a.output.with_suffix(".audit.json").resolve() in protected
    ):
        raise ValueError("Output cannot overwrite source evidence")
    logs, calls, positions = [read(path) for path in paths[1:]]
    controls = analyze(logs, calls, positions)
    patches, balances = receipt_movements(logs)
    if (
        balances[SPRLUSD] != controls["sprlusd_closing_scaled_raw"]
        or D(balances[SENTORA]) / D(10**18) != controls["sentora_closing_shares"]
    ):
        raise ValueError("Patched receipts fail closing contract balance controls")
    import gzip

    with gzip.open(a.history, "rt") as f:
        key = json.loads(next(f))["fingerprint"]
    history = attach(load_history(a.history, key), patches)
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    newkey = hashlib.sha256(
        json.dumps(hashes, sort_keys=True).encode() + Path(__file__).read_bytes()
    ).hexdigest()
    save_history(a.output, history, newkey)
    proof = {
        "publishable": False,
        "input_sha256": hashes,
        "fingerprint": newkey,
        "patched_transactions": len(patches),
        "closing_controls": controls,
        "batches": len(history.batches),
    }
    a.output.with_suffix(".audit.json").write_text(json.dumps(proof, default=str, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in proof.items() if k not in ("closing_controls", "input_sha256")}
        )
    )


if __name__ == "__main__":
    main()
