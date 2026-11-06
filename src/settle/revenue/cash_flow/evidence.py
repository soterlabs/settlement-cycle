"""Reject mismatched or truncated evidence before economic classification."""

from datetime import UTC, date, datetime, time

from .collector import DEPOSIT, TRANSFER, WITHDRAW


def validate_evidence(evidence, config):
    cutoff = date.fromisoformat(evidence["cutoff"])
    if evidence.get("configuration_revision") != config["revision"] or set(
        evidence["chains"]
    ) != set(config["wallets"]):
        raise ValueError("Cash-flow evidence configuration differs")
    start = int(datetime.combine(cutoff.replace(day=1), time.min, UTC).timestamp())
    end = int(datetime.combine(cutoff, time.max, UTC).timestamp())
    for chain, source in evidence["chains"].items():
        if source.get("chain") != chain or source.get("status") not in (
            "available",
            "partial",
            "unavailable",
        ):
            raise ValueError("Cash-flow source identity differs")
        if source["status"] == "unavailable":
            if source.get("events") or source.get("balances") or source.get("savings_events"):
                raise ValueError("Unavailable source contains usable evidence")
            continue
        pins = source["pins"]
        lo, hi = pins["opening"]["block"], pins["closing"]["block"]
        if not isinstance(lo, int) or not isinstance(hi, int) or not 0 <= lo <= hi:
            raise ValueError("Invalid cash-flow block interval")
        if pins["closing"]["before_genesis"]:
            if (
                source.get("events")
                or source.get("balances")
                or source.get("absence_proof") != "cutoff_precedes_chain_genesis"
            ):
                raise ValueError("Invalid pre-genesis absence proof")
            continue
        owners = config["wallets"][chain]
        vaults = {v["address"] for v in config["savings"] if v["chain"] == chain}
        for key in ("events", "savings_events"):
            seen = set()
            for event in source.get(key, []):
                identity = (event["block"], event["log_index"])
                if (
                    identity in seen
                    or event["chain"] != chain
                    or event["block"] > hi
                    or event["timestamp"] > end
                ):
                    raise ValueError("Duplicate or out-of-scope cash-flow event")
                seen.add(identity)
                if key == "events":
                    if (
                        not lo < event["block"]
                        or event["timestamp"] < start
                        or event["topic0"] != TRANSFER
                        or event["address"] not in config["tokens"][chain]
                    ):
                        raise ValueError("Cash event differs from configured interval or token")
                    addresses = [
                        "0x" + t[-40:]
                        for t in (event["topic1"], event["topic2"])
                        if isinstance(t, str) and len(t) == 66
                    ]
                    if not any(a in owners for a in addresses):
                        raise ValueError("Cash event has no owned participant")
                elif event["address"] not in vaults or event["topic0"] not in (
                    TRANSFER,
                    DEPOSIT,
                    WITHDRAW,
                ):
                    raise ValueError("Savings history scope differs")
        expected = set()
        for token, meta in config["tokens"][chain].items():
            if not meta["cash"] and meta["symbol"] not in ("RLUSD", "PYUSD", "USDG"):
                continue
            for owner, role in owners.items():
                if role["role"] in ("savings_idle", "intents") and meta["symbol"] not in (
                    "USDC",
                    "USDT",
                    "PYUSD",
                    "USDG",
                ):
                    continue
                expected.add((token, owner))
        actual = [(r["token"], r["owner"]) for r in source.get("balances", [])]
        if len(actual) != len(set(actual)) or set(actual) != expected:
            raise ValueError("Historical cash balance scope is incomplete")
    return evidence
