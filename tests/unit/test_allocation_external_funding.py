from datetime import date
from decimal import Decimal as D

import pytest

from settle.compute.allocation_capital import CapitalEvent, CapitalLedger


def apply(
    ledger, kind, amount, source=None, destination=None, ilk=None, external=None, preserve=False
):
    ledger.apply(
        CapitalEvent(
            "test",
            date(2026, 5, 18),
            (0,),
            kind,
            D(amount),
            source,
            destination,
            preserve_basis=preserve,
            ilk=ilk,
            external_source=external,
        )
    )


def test_sky_refinances_saver_investments_without_creating_extra_debt_or_income():
    ledger = CapitalLedger()
    apply(ledger, "external_draw", 100, destination="cash", external="saver")
    apply(ledger, "transfer", 60, "cash", "venue-a")
    apply(ledger, "transfer", 40, "cash", "venue-b")
    apply(ledger, "mark", 72, destination="venue-a")  # gains are never borrowed
    apply(ledger, "draw", 50, destination="cash", ilk="spark")
    apply(ledger, "external_repay", 50, "cash", external="saver")
    assert ledger.account("venue-a").borrowed == 30
    assert ledger.account("venue-b").borrowed == 20
    assert ledger.account("venue-a").external_by_source["saver"] == 30
    assert ledger.account("venue-a").value == 72
    assert ledger.drawn == 50 and ledger.repaid == 0
    assert ledger.external_drawn["saver"] == 100 and ledger.external_repaid["saver"] == 50
    assert sum(a.borrowed for a in ledger.accounts.values()) == 50


def test_saver_cash_can_refinance_sky_and_preserves_earned_fraction():
    ledger = CapitalLedger()
    apply(ledger, "draw", 100, destination="venue", ilk="spark")
    apply(ledger, "external_draw", 40, destination="cash", external="saver")
    apply(ledger, "income", 10, destination="cash")
    apply(ledger, "repay", 50, "cash", ilk="spark")
    assert ledger.account("venue").borrowed == 50
    assert ledger.account("venue").external_by_source["saver"] == 40
    assert ledger.account("venue").value == 100
    assert ledger.repaid == 50 and ledger.equity_funded_repayment == 10
    assert sum(a.borrowed for a in ledger.accounts.values()) == ledger.drawn - ledger.repaid


def test_joint_loss_cap_never_assigns_more_principal_than_realized_cash():
    ledger = CapitalLedger()
    apply(ledger, "draw", 60, destination="venue", ilk="spark")
    apply(ledger, "external_draw", 40, destination="venue", external="saver")
    apply(ledger, "mark", 50, destination="venue")
    apply(ledger, "transfer", 50, "venue", "cash")
    assert ledger.account("cash").borrowed == 30
    assert ledger.account("cash").external_by_source["saver"] == 20
    assert ledger.realised_principal_loss == 30
    assert ledger.external_realised_loss["saver"] == 20
    assert ledger.account("venue").borrowed == 0


def test_custody_transfer_retains_both_origins_despite_temporary_nav_loss():
    ledger = CapitalLedger()
    apply(ledger, "draw", 60, destination="venue", ilk="spark")
    apply(ledger, "external_draw", 40, destination="venue", external="saver")
    apply(ledger, "mark", 50, destination="venue")
    apply(ledger, "transfer", 50, "venue", "pending", preserve=True)
    assert ledger.account("pending").borrowed == 60
    assert ledger.account("pending").external_by_source["saver"] == 40
    assert ledger.realised_principal_loss == 0


def test_refinancing_lost_principal_does_not_fabricate_investment_assets():
    ledger = CapitalLedger()
    apply(ledger, "external_draw", 100, destination="venue", external="saver")
    apply(ledger, "mark", 50, destination="venue")
    apply(ledger, "transfer", 50, "venue", "cash")  # crystallizes 50 of saver principal loss
    apply(ledger, "draw", 100, destination="sky-cash", ilk="spark")
    apply(ledger, "external_repay", 100, "sky-cash", external="saver")
    assert ledger.account("cash").borrowed == 50
    expense = ledger.account("financing:retired-basis:external:saver")
    assert expense.borrowed == 50 and expense.value == 0
    assert sum(a.borrowed for a in ledger.accounts.values()) == 100
    assert sum(a.external_by_source.get("saver", D(0)) for a in ledger.accounts.values()) == 0


def test_interest_payment_keeps_financing_outside_investments():
    ledger = CapitalLedger()
    apply(ledger, "external_draw", 100, destination="venue", external="saver")
    apply(ledger, "draw", 55, destination="cash", ilk="spark")
    apply(ledger, "external_repay", 50, "cash", external="saver")
    apply(ledger, "transfer", 5, "cash", "financing:saver-interest:saver")
    apply(ledger, "mark", 0, destination="financing:saver-interest:saver")
    assert ledger.account("venue").borrowed == 50
    assert ledger.account("financing:saver-interest:saver").borrowed == 5
    assert sum(a.borrowed for a in ledger.accounts.values()) == 55


def test_no_external_lender_can_be_inferred_from_unidentified_cash():
    ledger = CapitalLedger()
    with pytest.raises(ValueError, match="identified funding source"):
        apply(ledger, "external_draw", 10, destination="cash")


def test_batch_replay_refinances_saver_principal_and_separates_interest(tmp_path):
    from settle.compute.allocation_capital import replay_history
    from settle.normalize.allocation_capital import (
        AssetMovement,
        CapitalBatch,
        CapitalHistory,
        ExternalFundingOperation,
    )
    from settle.normalize.allocation_history_cache import load_history, save_history

    day = date(2026, 5, 18)
    history = CapitalHistory(
        (
            CapitalBatch(
                "saver-deposit",
                day,
                1,
                "ethereum",
                1,
                (AssetMovement("venue", D(0), D(100)),),
                external_funding=(ExternalFundingOperation("draw", "saver", D(100)),),
            ),
            CapitalBatch(
                "refinance",
                day,
                2,
                "ethereum",
                2,
                (),
                D(55),
                minted_by_ilk={"spark": D(55)},
                external_funding=(
                    ExternalFundingOperation("repay", "saver", D(50)),
                    ExternalFundingOperation("interest", "saver", D(5)),
                ),
                funding_assumption="proportional policy",
            ),
        ),
        {"venue": "venue"},
        {},
    )
    path = tmp_path / "history.gz"
    save_history(path, history, "test")
    assert load_history(path, "test") == history
    replay = replay_history(history, day, day)
    assert replay.daily[day]["venue"] == 50
    assert replay.ledger.account("venue").external_by_source["saver"] == 50
    assert replay.ledger.account("financing:saver-interest:saver").borrowed == 5
    assert replay.ledger.drawn_by_ilk == {"spark": D(55)}
    assert not replay.unmatched_receipts and not replay.unmatched_outflows
    assert "venue" in replay.uncertain_accounts
    with pytest.raises(ValueError, match="do not yet support"):
        replay_history(history, day, day, quantify_uncertainty=True)


def test_repeated_mixed_refinancing_conserves_each_lender_separately():
    import random
    from decimal import localcontext

    rng = random.Random(215)
    ledger = CapitalLedger()
    with localcontext() as ctx:
        ctx.prec = 60
        for index in range(300):
            external = index % 2 == 0
            apply(
                ledger,
                "external_draw" if external else "draw",
                100,
                destination="cash",
                external="same-name" if external else None,
                ilk=None if external else "same-name",
            )
            apply(ledger, "income", 5, destination="cash")
            venue = "venue-" + str(rng.randrange(5))
            apply(ledger, "transfer", 105, "cash", venue)
            if index > 2:
                amount = min(ledger.account(venue).value, D(75))
                apply(ledger, "transfer", amount, venue, "cash")
                apply(
                    ledger,
                    "repay" if external else "external_repay",
                    amount,
                    "cash",
                    ilk="same-name" if external else None,
                    external=None if external else "same-name",
                )
            sky = sum((a.borrowed for a in ledger.accounts.values()), D(0))
            saver = sum(
                (a.external_by_source.get("same-name", D(0)) for a in ledger.accounts.values()),
                D(0),
            )
            assert abs(sky - ledger.drawn + ledger.repaid) < D("1e-45")
            assert abs(
                saver
                - ledger.external_drawn.get("same-name", D(0))
                + ledger.external_repaid.get("same-name", D(0))
            ) < D("1e-45")
            assert all(a.borrowed >= -D("1e-45") for a in ledger.accounts.values())
