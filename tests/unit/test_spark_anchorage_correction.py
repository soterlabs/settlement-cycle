import gzip
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_anchorage_correction import (
    ACCOUNT,
    AMOUNT,
    CASH,
    correct_spark_anchorage_round_trip,
)
from settle.domain.config import load_prime_by_id
from settle.domain.pricing import PricingCategory
from settle.domain.primes import Address, Chain, Token, Venue
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory
from settle.normalize.allocation_custody import USDC, link_facility

EVIDENCE = json.loads(
    gzip.decompress(
        (
            Path(__file__).parents[1] / "fixtures/spark_anchorage_july_correction.json.gz"
        ).read_bytes()
    )
)


def batches():
    result = []
    for e in EVIDENCE:
        b = dict(e["batch"])
        b["day"] = date.fromisoformat(b["day"])
        b["minted"] = D(b["minted"])
        b["minted_by_ilk"] = {k: D(v) for k, v in b["minted_by_ilk"].items()}
        b["external_funding"] = ()
        b["movements"] = tuple(
            M(
                m["account"],
                D(m["value_before"]),
                D(m["change"]),
                D(m["external_income"]),
                m["preserve_basis"],
            )
            for m in b["movements"]
        )
        result.append(B(**b))
    return result


def history():
    bs = batches()
    amount = next(m.value_before for m in bs[0].movements if m.account == ACCOUNT)
    ilk = next(iter(bs[0].minted_by_ilk))
    seed = B(
        "funding",
        bs[0].day,
        bs[0].timestamp - 1,
        "ethereum",
        bs[0].block - 1,
        (M(ACCOUNT, D(0), amount, preserve_basis=True),),
        amount,
        minted_by_ilk={ilk: amount},
    )
    return CapitalHistory(
        tuple([seed, *bs]), {"S23": ACCOUNT}, {"S23": "Unconfirmed other returns"}
    )


def test_receipts_prove_exact_ten_million_net_deployment():
    holder = CASH.split(":")[1]
    escrow = ACCOUNT.split(":")[-1]
    flows = []
    for e in EVIDENCE:
        r = e["receipt"]
        assert r["status"] == "0x1" and int(r["blockNumber"], 16) == e["batch"]["block"]
        rows = [
            log
            for log in r["logs"]
            if log["address"] == USDC
            and log["topics"][0] == TRANSFER_TOPIC0
            and {log["topics"][1][-40:], log["topics"][2][-40:]} == {holder[2:], escrow[2:]}
        ]
        assert len(rows) == 1
        log = rows[0]
        amount = D(int(log["data"], 16)) / 10**6
        flows.append(amount if log["topics"][1][-40:] == holder[2:] else -amount)
    assert flows == [D("10000008.643597"), D("10000008.643597"), -AMOUNT]
    assert sum(flows) == D("10000000")


def test_old_snapshot_releases_funded_claim_and_is_idempotent():
    old = history()
    fixed = correct_spark_anchorage_round_trip(old)
    assert correct_spark_anchorage_round_trip(fixed) == fixed
    assert fixed.unsupported == old.unsupported
    assert [b.minted_by_ilk for b in fixed.batches] == [b.minted_by_ilk for b in old.batches]
    last = fixed.batches[-1]
    assert next(m for m in last.movements if m.account == CASH).external_income == 0
    replay = replay_history(fixed, last.day, last.day)
    assert replay.ledger.account(CASH).borrowed.quantize(D(".000001")) == AMOUNT
    assert replay.ledger.account(ACCOUNT).value == D("270133608.694857")
    assert not replay.unmatched_receipts and not replay.unmatched_outflows
    broken = replace(old.batches[-1], block=0)
    with pytest.raises(ValueError, match="metadata"):
        correct_spark_anchorage_round_trip(replace(old, batches=(*old.batches[:-1], broken)))


def test_fresh_normalizer_identifies_return_even_on_net_outflow_day():
    prime = load_prime_by_id("spark")
    bs = [
        replace(b, movements=tuple(m for m in b.movements if m.account != ACCOUNT))
        for b in batches()
    ]
    logs = []
    for e in EVIDENCE:
        for r in e["receipt"]["logs"]:
            if r["address"] == USDC and r["topics"][0] == TRANSFER_TOPIC0:
                logs.append(
                    LogRow(
                        int(r["blockNumber"], 16),
                        int(r["logIndex"], 16),
                        e["batch"]["timestamp"],
                        USDC,
                        *r["topics"],
                        None,
                        r["data"],
                        r["transactionHash"],
                    )
                )
    token = Token(Chain.ETHEREUM, Address.from_str(USDC), "USDC", 6)
    mapping = {
        (USDC, prime.alm[Chain.ETHEREUM].hex): Venue(
            "cash", Chain.ETHEREUM, token, PricingCategory.PAR_STABLE
        )
    }
    accounts, unsupported = {}, {}
    linked = link_facility(prime, Chain.ETHEREUM, bs, logs, accounts, unsupported, mapping)
    fixed = CapitalHistory(tuple(linked), accounts, unsupported)
    assert correct_spark_anchorage_round_trip(fixed) == fixed
    result = replay_history(fixed, linked[0].day, linked[-1].day)
    assert result.ledger.account(ACCOUNT).value == D("10000000")
    assert result.ledger.account(CASH).borrowed.quantize(D(".000001")) == AMOUNT


def test_later_facility_marks_are_rebased_and_inconsistent_input_rejected():
    h = history()
    last = h.batches[-1]
    old_close = D("280133625.982051")
    future = B(
        "later-deposit",
        last.day,
        last.timestamp + 1,
        "ethereum",
        last.block + 1,
        (M(ACCOUNT, old_close, D("10"), preserve_basis=True),),
        D(10),
        minted_by_ilk={"A": D(10)},
    )
    h = replace(h, batches=(*h.batches, future))
    fixed = correct_spark_anchorage_round_trip(h)
    assert fixed.batches[-1].movements[0].value_before == old_close - AMOUNT
    bad = replace(future, movements=(replace(future.movements[0], value_before=old_close + D(1)),))
    with pytest.raises(ValueError, match="balance inconsistent"):
        correct_spark_anchorage_round_trip(replace(h, batches=(*h.batches[:-1], bad)))
