"""Reporting-only offsets must conserve net assets and retained publications."""

import gzip
import json
from copy import deepcopy
from datetime import date
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

import pytest

from settle.revenue.balance_sheet import annual, decode
from settle.revenue.balance_sheet_accounting import (
    ATLAS,
    PSMS,
    active,
    fee_share,
    project,
    validate,
)
from settle.revenue.verification import digest


def document():
    p = Path(__file__).parents[1] / "fixtures/spark_closing_stocks_october.json.gz"
    doc = json.load(gzip.open(p))
    doc["accounting"] = dict(
        schema_version="spark-bs-accounting.v1",
        atlas=ATLAS,
        sde={
            "S24": dict(status="available", value="0"),
            "S62": dict(status="available", value=doc["ownership"]["S62"]["sky_value"]),
            **{key: dict(status="available", value="1") for key in PSMS},
        },
        investments={
            "arkis_safe": dict(status="unavailable", value=None, reason="Payment evidence missing")
        },
    )
    from settle.extract._keccak import keccak256

    for key in PSMS:
        chain, addr, token, holder = (
            key.removeprefix("psm."),
            "0x" + "a" * 40,
            "0x" + "b" * 40,
            "0x" + "c" * 40,
        )
        doc["accounting"]["sde"][key]["proof"] = dict(chain=chain, contract=addr, holder=holder)
        doc["reads"].append(dict(chain=chain, contract=addr, method="eth_getCode", result="0x1234"))
        for contract, signature, args, value in [
            (addr, "shares(address)", [int(holder, 16)], 1),
            (addr, "totalShares()", [], 1),
            (addr, "usdc()", [], int(token, 16)),
            (token, "decimals()", [], 6),
            (token, "balanceOf(address)", [int(addr, 16)], 1000000),
        ]:
            data = (
                "0x" + keccak256(signature.encode()).hex()[:8] + "".join(f"{v:064x}" for v in args)
            )
            doc["reads"].append(
                dict(
                    chain=chain,
                    contract=contract,
                    method="eth_call",
                    data=data,
                    result=f"0x{value:064x}",
                )
            )
    return doc


def test_historical_boundaries_not_current_msc_config():
    for key, start, end in [
        ("S24", date(2025, 11, 13), date(2026, 6, 25)),
        ("S62", date(2026, 5, 26), date.max),
        ("psm3_usdc", date(2025, 11, 13), date.max),
    ]:
        from datetime import timedelta

        assert not active(key, start - timedelta(days=1))
        assert active(key, start) and active(key, end)
        if end != date.max:
            assert not active(key, end + timedelta(days=1))


def test_fee_split_conserves_value_and_uses_principal_not_fee_currency():
    result = fee_share("1010", "800", "10")
    assert D(result["sky_fees"]) == 8
    assert D(result["prime_fees"]) == 2
    assert D(result["sky_value"]) == 808
    assert D(result["prime_value"]) == 202
    assert fee_share("1010", "0", "10")["sky_fees"] == "0"
    for args in [("10", "0", "10"), ("1", "2", "0"), ("NaN", "0", "0")]:
        with pytest.raises(ValueError):
            fee_share(*args)


def test_exactly_once_sde_offset_and_missing_evidence_fail_closed():
    doc = document()
    result = project(doc)
    with localcontext() as ctx:
        ctx.prec = 100
        offset = sum(D(v["value"]) for v in doc["accounting"]["sde"].values())
        assert D(result["sky_debt"]["value"]) + offset == D(doc["sky_debt"]["value"])
        assert D(result["sky_debt_sde"]["value"]) == -offset
        for key in PSMS:
            assert D(result["position." + key]["value"]) + 1 == D(doc["extra_assets"][key]["value"])
    doc["accounting"]["sde"]["psm.base"] = dict(status="unavailable", value=None)
    result = project(doc)
    assert result["sky_debt"]["status"] == "unavailable"
    assert result["position.psm.base"]["status"] == "unavailable"
    assert result["sky_debt_gross"]["status"] == "available"


@pytest.mark.parametrize("mutation", ["missing", "negative", "excess", "lp", "safe"])
def test_corrupt_or_unsupported_offsets_rejected(mutation):
    doc = document()
    a = doc["accounting"]
    if mutation == "missing":
        del a["sde"]["psm.base"]
    elif mutation == "negative":
        a["sde"]["psm.base"]["value"] = "-1"
    elif mutation == "excess":
        a["sde"]["psm.base"]["value"] = "1e20"
    elif mutation == "lp":
        a["sde"]["S62"]["value"] = "0"
    else:
        a["investments"]["arkis_safe"] = dict(
            status="available", value="4000000", quality="supported", receipt={}, block={}
        )
    with pytest.raises((ValueError, KeyError)):
        validate(doc)


def test_retained_selection_does_not_pick_up_new_layout_or_offsets():
    doc = document()
    old = deepcopy(doc)
    del old["accounting"]

    def row(d, n):
        return dict(
            cutoff=d["cutoff"],
            revision_id=str(n) * 64,
            publication_order=n,
            document=d,
            document_hash=digest(d),
        )

    inputs = dict(venues=[], cash=[], components=[], stocks=[row(old, 1)])
    before = annual(2026, date(2026, 10, 31), inputs)
    inputs["stocks"].append(row(doc, 2))
    assert before == annual(
        2026, date(2026, 10, 31), inputs, selection=decode(before["selection"], "spark", 2026)
    )
    report = annual(2026, date(2026, 10, 31), inputs)
    assert report == annual(
        2026, date(2026, 10, 31), inputs, selection=decode(report["selection"], "spark", 2026)
    )
    rows = {r["id"]: r for r in report["lines"]}
    assert rows["investment.arkis_safe"]["values"]["2026-10"]["status"] == "unavailable"
    assert rows["sky_debt_gross"]["values"]["2026-10"]["value"] == old["sky_debt"]["value"]
    assert all(c["id"] != "sky_debt_memo" for c in rows["total_liabilities"]["components"])


def test_safe_requires_payment_and_stays_estimated():
    from settle.revenue.balance_sheet_accounting import SAFE_FROM, SAFE_TO, SAFE_TX, USDS
    from settle.revenue.cash_flow.collector import TRANSFER

    doc = document()
    receipt = dict(
        status="0x1",
        transactionHash=SAFE_TX,
        blockHash="0x" + "d" * 64,
        blockNumber="0x1",
        logs=[
            dict(
                address=USDS,
                topics=[TRANSFER, "0x" + SAFE_FROM[2:].zfill(64), "0x" + SAFE_TO[2:].zfill(64)],
                data=hex(4_000_000 * 10**18),
            )
        ],
    )
    doc["accounting"]["investments"]["arkis_safe"] = dict(
        status="available",
        value="4000000",
        quality="estimate",
        receipt=receipt,
        block=dict(hash=receipt["blockHash"], number=receipt["blockNumber"]),
    )
    assert project(doc)["investment.arkis_safe"]["quality"] == "estimate"
    receipt["logs"][0]["data"] = hex(3_000_000 * 10**18)
    with pytest.raises(ValueError, match="Arkis"):
        validate(doc)


def test_offset_cannot_be_changed_without_corresponding_getter_evidence():
    doc = document()
    doc["accounting"]["sde"]["psm.base"]["value"] = "1.1"
    with pytest.raises(ValueError, match="raw closing getters"):
        validate(doc)
