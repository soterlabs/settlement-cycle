"""Savings principal/interest attribution with transferable-share cost basis.

A Deposit creates principal at assets paid. Ordinary share transfers carry the
sender's proportional principal; mints/burns are counted by Deposit/Withdraw,
not again by ERC20 Transfer. Unsupported/missing histories fail closed.
"""

from collections import defaultdict
from decimal import ROUND_DOWN, Decimal, localcontext

from .collector import DEPOSIT, TRANSFER, WITHDRAW

ZERO = "0x" + "0" * 40


def address(topic):
    return "0x" + topic[-40:] if isinstance(topic, str) and len(topic) == 66 else None


def split(events, vaults):
    result = {}
    state = {}
    failures = {}
    with localcontext() as ctx:
        ctx.prec = 100
        for vault in vaults:
            chain, contract = vault["chain"], vault["address"]
            book = defaultdict(lambda: [Decimal(0), Decimal(0)])
            rows = sorted(
                (e for e in events if e["chain"] == chain and e["address"] == contract),
                key=lambda e: (e["block"], e["log_index"]),
            )
            seen = set()
            try:
                for e in rows:
                    identity = f"{chain}:{e['tx_hash']}:{e['log_index']}"
                    if identity in seen:
                        raise ValueError("Duplicate Savings event")
                    seen.add(identity)
                    words = [int(e["data"][i : i + 64], 16) for i in range(2, len(e["data"]), 64)]
                    if e["topic0"] == TRANSFER:
                        sender, receiver = address(e["topic1"]), address(e["topic2"])
                        if ZERO in (sender, receiver):
                            continue
                        units = Decimal(words[0])
                        shares, principal = book[sender]
                        if units > shares:
                            raise ValueError("Savings share transfer lacks its principal history")
                        carried = principal * units / shares if shares else Decimal(0)
                        book[sender] = [shares - units, principal - carried]
                        book[receiver][0] += units
                        book[receiver][1] += carried
                    elif e["topic0"] == DEPOSIT:
                        if len(words) != 2:
                            raise ValueError("Malformed Deposit")
                        owner = address(e["topic2"])
                        assets, shares = map(Decimal, words)
                        book[owner][0] += shares
                        book[owner][1] += assets
                        result[identity] = {
                            "chain": chain,
                            "vault": contract,
                            "tx_hash": e["tx_hash"],
                            "log_index": e["log_index"],
                            "direction": 1,
                            "assets_units": str(assets),
                            "principal_units": str(assets),
                            "interest_units": "0",
                            "owner": owner,
                            "receiver": owner,
                            "timestamp": e["timestamp"],
                        }
                    elif e["topic0"] == WITHDRAW:
                        if len(words) != 2:
                            raise ValueError("Malformed Withdraw")
                        owner = address(e["topic3"])
                        receiver = address(e["topic2"])
                        assets, burned = map(Decimal, words)
                        if assets == 0 and burned == 0:
                            continue
                        shares, principal = book[owner]
                        if burned > shares or not shares:
                            raise ValueError("Savings withdrawal lacks its principal history")
                        paid_principal = min(
                            assets, max(Decimal(0), principal * burned / shares)
                        ).to_integral_value(rounding=ROUND_DOWN)
                        book[owner] = [shares - burned, principal - principal * burned / shares]
                        result[identity] = {
                            "chain": chain,
                            "vault": contract,
                            "tx_hash": e["tx_hash"],
                            "log_index": e["log_index"],
                            "direction": -1,
                            "assets_units": str(assets),
                            "principal_units": str(paid_principal),
                            "interest_units": str(assets - paid_principal),
                            "owner": owner,
                            "receiver": receiver,
                            "timestamp": e["timestamp"],
                        }
                state[chain + ":" + contract] = {
                    "shares_units": str(sum(v[0] for v in book.values())),
                    "principal_units": str(sum(v[1] for v in book.values())),
                    "events": len(rows),
                }
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                failures[chain + ":" + contract] = {
                    "reason": str(exc),
                    "event_id": identity,
                    "date": __import__("datetime")
                    .datetime.fromtimestamp(e["timestamp"], __import__("datetime").timezone.utc)
                    .isoformat(),
                }
                # Earlier entries cannot certify an interval after the failure.
                result = {
                    k: v
                    for k, v in result.items()
                    if not (v["chain"] == chain and v["vault"] == contract)
                }
    return {"events": list(result.values()), "states": state, "failures": failures}
