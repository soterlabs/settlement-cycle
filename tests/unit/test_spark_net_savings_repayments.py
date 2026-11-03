"""Real receipt net amounts, replayed against an explicitly synthetic portfolio."""

import gzip
import json
from datetime import date
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.allocation_funding_bridge import funding_bridge
from settle.extract._keccak import keccak256
from settle.normalize.allocation_capital import (
    AssetMovement,
    CapitalBatch,
    CapitalHistory,
    ExternalFundingOperation,
)
from settle.normalize.sources.hypersync_debt import _FROB_T0, _decode_dart

ALM = "0x1601843c5e9bc251a3272907010afa41fa18347e"
SAVER = "0x28b3a8fb53b741a8fd78c0fb9a6b2393d896a43d"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
USDS = "0xdc035d45d973e3ec169d2276ddab16f1e407384f"
SUSDS = "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
VAULT = "0x691a6c29e9e96dd897718305427ad5d534db16ba"
VAT = "0x35d1b3f3d7966a1dfe207aa4514c12a259a0492b"
ILK = "0x414c4c4f4341544f522d535041524b2d41000000000000000000000000000000"
SOURCE = "ethereum:" + SAVER
FIXTURE = json.loads(
    gzip.decompress(
        (
            Path(__file__).parents[1] / "fixtures/spark_savings_net_repayments_2026_08.json.gz"
        ).read_bytes()
    )
)


def topic(signature):
    return "0x" + keccak256(signature.encode()).hex()


def receipt_amounts(row):
    receipt = row["receipt"]
    assert receipt["status"] == "0x1"
    logs = receipt["logs"]
    assert all(
        log["transactionHash"] == receipt["transactionHash"] and not log.get("removed")
        for log in logs
    )

    def transfer(token, sender, receiver):
        return sum(
            (
                D(int(log["data"], 16))
                for log in logs
                if log["address"] == token
                and log["topics"][0] == topic("Transfer(address,address,uint256)")
                and log["topics"][1][-40:] == sender[2:]
                and log["topics"][2][-40:] == receiver[2:]
            ),
            D(0),
        )

    taken = transfer(USDC, SAVER, ALM) / 10**6
    assert taken == transfer(USDC, ALM, "0x37305b1cd40574e4c5ce33f8e8306be057fd7341") / 10**6
    deposit = transfer(USDS, ALM, SUSDS) / 10**18
    draw, wipe = [
        sum(
            (
                D(int(log["data"], 16)) / 10**18
                for log in logs
                if log["address"] == VAULT and log["topics"][0] == topic(sig)
            ),
            D(0),
        )
        for sig in ["Draw(address,uint256)", "Wipe(address,uint256)"]
    ]
    assert wipe == taken and draw == deposit
    with localcontext() as ctx:
        ctx.prec = 60
        net = sum(
            (
                D(_decode_dart(log["data"])) / 10**18 * D(FIXTURE["ilk_rate"])
                for log in logs
                if log["address"] == VAT
                and log["topics"][0] == _FROB_T0
                and log["topics"][1] == ILK
            ),
            D(0),
        )
        assert abs(net - (draw - wipe)) < D("1e-12")
    return taken, deposit, net


@pytest.mark.parametrize(
    "row", FIXTURE["transactions"], ids=lambda r: r["receipt"]["transactionHash"][:12]
)
def test_observed_net_repayment_reduces_sky_only_and_preserves_other_funding(row):
    taken, deposit, net = receipt_amounts(row)
    day = date.fromisoformat(row["day"])
    # Synthetic opening allocation weights, not reconstructed historical holdings.
    opening = CapitalBatch(
        "opening",
        day,
        0,
        "ethereum",
        0,
        (AssetMovement("a", D(0), D("60000000")), AssetMovement("b", D(0), D("40000000"))),
        D("100000000"),
        minted_by_ilk={ILK: D("100000000")},
    )
    gains = CapitalBatch(
        "earned",
        day,
        1,
        "ethereum",
        1,
        (AssetMovement("a", D("60000000"), D("12000000"), D("12000000")),),
    )
    unrelated = CapitalBatch(
        "other-ilk",
        day,
        2,
        "ethereum",
        2,
        (AssetMovement("other", D(0), D("20000000")),),
        D("20000000"),
        minted_by_ilk={"other": D("20000000")},
    )
    saver_only = CapitalBatch(
        "saver-only",
        day,
        3,
        "ethereum",
        3,
        (AssetMovement("saver-only", D(0), D("10000000")),),
        external_funding=(ExternalFundingOperation("draw", "other-saver", D("10000000")),),
    )
    batch = CapitalBatch(
        "ethereum:" + row["receipt"]["transactionHash"],
        day,
        4,
        "ethereum",
        int(row["receipt"]["blockNumber"], 16),
        (AssetMovement("new-susds", D(0), deposit),) if deposit else (),
        net,
        minted_by_ilk={ILK: net},
        external_funding=(ExternalFundingOperation("draw", SOURCE, taken),),
        funding_assumption="Accepted proportional repayment-only replacement",
    )
    history = CapitalHistory(
        (opening, gains, unrelated, saver_only, batch), {"A": "a", "B": "b", "S32": "new-susds"}, {}
    )
    result = replay_history(
        history, day, day, funding_accounts={"a", "b", "new-susds", "saver-only", "other"}
    )
    for name, weight, value in [("a", D(".6"), D("72000000")), ("b", D(".4"), D("40000000"))]:
        account = result.ledger.account(name)
        assert abs(account.borrowed - (D("100000000") + net) * weight) < D("1e-12")
        assert abs(account.external_by_source[SOURCE] - (-net) * weight) < D("1e-12")
        assert account.value == value
    assert result.ledger.account("other").borrowed == D("20000000")
    assert result.ledger.account("saver-only").external_by_source == {"other-saver": D("10000000")}
    assert result.ledger.account("new-susds").borrowed == 0
    assert abs(
        result.ledger.account("new-susds").external_by_source.get(SOURCE, D(0)) - deposit
    ) < D("1e-12")
    # The temporary gross draw is not carried into the daily borrowing-cost base.
    assert abs(sum(result.daily[day].values()) - (D("120000000") + net)) < D("1e-12")
    assert all(
        abs(r["principal_not_in_accounts"]) < D("1e-12")
        for r in result.daily_funding_bridge[day]["sources"]
    )


def test_bridge_separates_expenses_unselected_custody_and_retired_losses():
    from settle.compute.allocation_capital import CapitalLedger
    from tests.unit.test_allocation_external_funding import apply

    ledger = CapitalLedger()
    apply(ledger, "external_draw", 100, destination="venue", external="saver")
    apply(ledger, "transfer", 10, "venue", "financing:saver-interest:saver")
    apply(ledger, "mark", 0, destination="financing:saver-interest:saver")
    apply(ledger, "transfer", 20, "venue", "custody")
    row = next(r for r in funding_bridge(ledger, {"venue"})["sources"] if r["kind"] == "savings")
    assert (row["selected_principal"], row["financing_principal"], row["other_principal"]) == (
        70,
        10,
        20,
    )
    assert row["principal_not_in_accounts"] == 0
    # Cumulative losses must not be deducted twice after the lender is refinanced.
    ledger = CapitalLedger()
    apply(ledger, "external_draw", 100, destination="venue", external="saver")
    apply(ledger, "mark", 50, destination="venue")
    apply(ledger, "transfer", 50, "venue", "cash")
    apply(ledger, "draw", 100, destination="replacement", ilk="spark")
    apply(ledger, "external_repay", 100, "replacement", external="saver")
    row = next(
        r for r in funding_bridge(ledger, {"venue", "cash"})["sources"] if r["kind"] == "savings"
    )
    assert row["historical_realised_principal_loss"] == 50
    assert row["outstanding_principal"] == row["principal_not_in_accounts"] == 0


@pytest.mark.parametrize("mutation", ["missing", "amount", "duplicate", "nonfinite"])
def test_saved_bridge_rejects_incomplete_or_inconsistent_controls(tmp_path, monkeypatch, mutation):
    monkeypatch.syspath_prepend(str(Path("scripts").resolve()))
    import audit_spark_mixed_funding_yields as module

    from tests.unit.test_spark_mixed_funding_yields import history

    day = date(2026, 8, 2)
    replay = replay_history(history(), day, day, funding_accounts={"venue", "other"})
    identity = {"schema": 3, "start": str(day), "end": str(day), "accounts": ["other", "venue"]}
    path = tmp_path / "snapshot.json.gz"
    module.save_snapshot(path, identity, replay)
    loaded = module.load_snapshot(path, identity)
    assert loaded.daily_funding == replay.daily_funding
    assert (
        loaded.daily_funding_bridge[day]["policy"]
        == "proportional_remaining_principal_of_repaid_lender"
    )
    saved = json.loads(gzip.decompress(path.read_bytes()))
    rows = saved["daily_funding_bridge"][str(day)]["sources"]
    if mutation == "missing":
        del saved["daily_funding_bridge"]
    if mutation == "amount":
        rows[0]["selected_principal"] = "999999"
    if mutation == "duplicate":
        rows.append(rows[0])
    if mutation == "nonfinite":
        rows[0]["financing_principal"] = "NaN"
    path.write_bytes(gzip.compress(json.dumps(saved).encode()))
    with pytest.raises(ValueError):
        module.load_snapshot(path, identity)
