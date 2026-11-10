import gzip
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from settle.revenue.balance_sheet import LEGACY_POLICY, annual, decode
from settle.revenue.balance_sheet_sources import Reader
from settle.revenue.balance_sheet_store import validate
from settle.revenue.verification import digest

FIXTURE = Path(__file__).parents[1] / "fixtures" / "spark_closing_stocks_october.json.gz"


def test_rpc_snapshot_validates_and_populates_positions_without_legacy_revenue():
    document = json.load(gzip.open(FIXTURE))
    validate(document)
    stock = dict(
        cutoff=document["cutoff"],
        revision_id="a" * 64,
        publication_order=1,
        document_hash=digest(document),
        document=document,
    )
    inputs = dict(venues=[], cash=[], components=[], stocks=[stock])
    report = annual(2026, date(2026, 10, 31), inputs)
    rows = {r["id"]: r for r in report["lines"]}
    for target, family, key in [
        ("position.S70", "positions", "S70"),
        ("position.S72", "positions", "S72"),
        ("position.sparklend_weth", "positions", "sparklend_weth"),
        ("position.psm.base", "extra_assets", "psm.base"),
        ("savings.xlayer_usdc", "liabilities", "xlayer_usdc"),
    ]:
        assert rows[target]["values"]["2026-10"]["value"] == document[family][key]["value"]
    assert rows["sky_debt"]["values"]["2026-10"]["value"] == document["sky_debt"]["value"]
    assert (
        rows["position.S62"]["values"]["2026-10"]["value"]
        == document["ownership"]["S62"]["prime_value"]
    )
    assert rows["position.S23"]["values"]["2026-10"]["status"] == "unavailable"
    assert (
        rows["cash"]["values"]["2026-10"]["status"] == "unavailable"
    )  # not inferred from allocations
    assert rows["contributed_capital"]["values"]["2026-10"]["status"] == "unavailable"
    assert rows["idle.S56"]["values"]["2026-10"]["value"] == "0"  # already measured cash
    assert (
        annual(
            2026, date(2026, 10, 31), inputs, selection=decode(report["selection"], "spark", 2026)
        )
        == report
    )


def test_legacy_selection_remains_reproducible_without_new_stocks():
    selection = dict(
        policy_version=LEGACY_POLICY,
        prime="spark",
        year=2026,
        as_of="2026-10-31",
        months=[None] * 12,
    )
    before = annual(
        2026, date(2026, 10, 31), dict(venues=[], cash=[], components=[]), selection=selection
    )
    after = annual(
        2026,
        date(2026, 10, 31),
        dict(venues=[], cash=[], components=[], stocks=[dict(cutoff="2026-10-31")]),
        selection=selection,
    )
    assert before == after
    assert before["policy_version"] == LEGACY_POLICY
    assert not any(r["id"] == "position.S72" for r in before["lines"])


@pytest.mark.parametrize("mutation", ["hash", "boundary", "negative", "ownership", "debt"])
def test_corrupt_closing_evidence_fails_closed(mutation):
    document = json.load(gzip.open(FIXTURE))
    if mutation == "hash":
        document["cutoff"] = "2026-09-30"
    elif mutation == "boundary":
        document["pins"]["ethereum"]["successor_timestamp"] = document["pins"]["ethereum"][
            "timestamp"
        ]
    elif mutation == "negative":
        document["positions"]["S70"]["value"] = "-1"
    elif mutation == "ownership":
        document["ownership"]["S62"]["sky_value"] = "0"
    elif mutation == "debt":
        document["sky_debt"]["value"] = "NaN"
    if mutation != "hash":
        document["proof_hash"] = digest({k: v for k, v in document.items() if k != "proof_hash"})
    with pytest.raises(ValueError):
        validate(document)


def test_stable_par_and_stale_oracle_rules(monkeypatch):
    r = Reader({"ethereum": {"block": 1, "timestamp": 10000}})
    assert r.price("RLUSD") == Decimal(1)
    monkeypatch.setattr(
        r, "call", lambda *a: "0x" + "".join(f"{v:064x}" for v in [1, 2000 * 10**8, 1, 1, 1])
    )
    with pytest.raises(ValueError, match="Stale"):
        r.price("ETH")


def test_missing_chain_is_never_read_as_zero():
    with pytest.raises(KeyError):
        Reader({}).balance("xlayer", "0x" + "1" * 40, "0x" + "2" * 40)
