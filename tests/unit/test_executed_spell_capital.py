from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.executed_spell_capital import (
    BUIDL,
    BUIDL_COST,
    DELIVERY,
    GROVE,
    INITIAL,
    JTRSY,
    JTRSY_COST,
    PAYMENT,
    SPARK,
    SYRUP,
    SYRUP_COST,
    USDS,
    account,
    apply_executed_spells,
)
from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory as H

DAY = date(2026, 7, 20)


def initial(holder):
    amounts = ((M(account(holder, BUIDL), D(0), BUIDL_COST),
                M(account(holder, JTRSY), D(0), JTRSY_COST + D('84472.44')))
               if holder == GROVE else
               (M(account(holder, USDS), D(0), BUIDL_COST + JTRSY_COST),
                M(account(holder, BUIDL), BUIDL_COST, -BUIDL_COST),
                M(account(holder, JTRSY), JTRSY_COST + D('84472.44'), -JTRSY_COST - D('84472.44'))))
    return B(INITIAL, DAY, 1, 'ethereum', 23018751, amounts,
             BUIDL_COST + JTRSY_COST if holder == GROVE else D(0))


def test_initial_grove_purchase_uses_cost_and_never_finances_mark_to_market():
    b = initial(GROVE)
    h = H((b,), {'E9': account(GROVE, JTRSY)}, {})
    r = replay_history(h, DAY, DAY)
    assert r.ledger.account(account(GROVE, JTRSY)).borrowed == JTRSY_COST
    assert r.ledger.account(account(GROVE, BUIDL)).borrowed == BUIDL_COST
    assert not r.unmatched_receipts and not r.unmatched_outflows
    assert h.batches[0] == b  # Raw history remains immutable.
    assert apply_executed_spells(apply_executed_spells(h)) == apply_executed_spells(h)


def test_initial_spark_sale_uses_actual_proceeds():
    b = initial(SPARK)
    h = H((b,), {'S': account(SPARK, JTRSY)}, {})
    fixed = apply_executed_spells(h).batches[0]
    assert sum(m.change for m in fixed.movements) == 0
    j = next(m for m in fixed.movements if m.account == account(SPARK, JTRSY))
    assert j.value_before == -j.change == JTRSY_COST


def exchange(holder, include_delivery=True):
    cash, asset = account(holder, USDS), account(holder, SYRUP)
    p = B(PAYMENT, DAY, 100, 'ethereum', 25574512,
          (M(cash, D(0), D(0) if holder == SPARK else SYRUP_COST),),
          SYRUP_COST if holder == SPARK else D(0))
    d = B(DELIVERY, DAY, 244, 'ethereum', 25574524,
          (M(asset, D(0), SYRUP_COST - 39) if holder == SPARK else
           M(asset, SYRUP_COST - 39, -SYRUP_COST + 39),))
    return H((p, d) if include_delivery else (p,), {'venue': asset}, {})


def test_spark_prepaid_purchase_does_not_fund_unrelated_spell_receipts():
    h = exchange(SPARK)
    p, d = h.batches
    p = replace(p, movements=(*p.movements, M('unrelated', D(0), D(500))))
    h = replace(h, batches=(p, d))
    r = replay_history(h, DAY, DAY)
    assert r.ledger.drawn == SYRUP_COST
    assert r.ledger.account(account(SPARK, SYRUP)).borrowed == SYRUP_COST
    assert r.ledger.account('unrelated').borrowed == 0
    assert 'unrelated' in r.uncertain_accounts
    assert account(SPARK, SYRUP) not in r.uncertain_accounts
    assert not r.unmatched_outflows
    assert apply_executed_spells(apply_executed_spells(h)) == apply_executed_spells(h)


def test_grove_sale_releases_existing_basis_without_creating_new_debt():
    h = exchange(GROVE)
    seed = B('seed', DAY, 1, 'ethereum', 1,
             (M(account(GROVE, SYRUP), D(0), D('100000000')),), D('100000000'))
    h = replace(h, batches=(seed, *h.batches))
    r = replay_history(h, DAY, DAY)
    assert r.ledger.drawn == D('100000000')
    assert r.ledger.account(account(GROVE, USDS)).borrowed == D('100000000')
    assert r.ledger.account(account(GROVE, USDS)).value == SYRUP_COST
    assert not r.unmatched_receipts and not r.unmatched_outflows
    assert apply_executed_spells(apply_executed_spells(h)) == apply_executed_spells(h)


def test_pin_before_delivery_retains_spark_prepayment_without_future_receipt():
    h = exchange(SPARK, False)
    r = replay_history(h, DAY, DAY)
    assert r.ledger.account('spell-prepayment:' + PAYMENT).borrowed == SYRUP_COST
    assert not r.unmatched_receipts and not r.unmatched_outflows
    assert account(SPARK, SYRUP) not in r.ledger.accounts


def test_unverified_lookalike_is_not_matched():
    h = exchange(GROVE)
    h = replace(h, batches=tuple(replace(b, identity='other:' + b.identity) for b in h.batches))
    assert apply_executed_spells(h) == h


def test_changed_draw_and_intervening_seller_cash_use_are_rejected():
    h = H((replace(initial(GROVE), minted=D(1)),), {'E9': account(GROVE, JTRSY)}, {})
    with pytest.raises(ValueError, match='acquisition draw'):
        apply_executed_spells(h)
    h = exchange(GROVE)
    between = B('intervening', DAY, 110, 'ethereum', 25574515,
                (M(account(GROVE, USDS), SYRUP_COST, D(-1)),))
    with pytest.raises(ValueError, match='intervening'):
        apply_executed_spells(replace(h, batches=(*h.batches, between)))


def test_spark_other_cash_use_does_not_consume_its_separate_prepayment():
    h = exchange(SPARK)
    between = B('intervening', DAY, 110, 'ethereum', 25574515,
                (M(account(SPARK, USDS), D(10), D(-10)),
                 M('other-venue', D(0), D(10))))
    r = replay_history(replace(h, batches=(*h.batches, between)), DAY, DAY)
    assert r.ledger.account(account(SPARK, SYRUP)).borrowed == SYRUP_COST
    assert r.ledger.account('other-venue').borrowed == 0


def test_grove_pin_before_delivery_does_not_use_future_sale():
    h = exchange(GROVE, False)
    assert apply_executed_spells(h) == h
    r = replay_history(h, DAY, DAY)
    assert r.ledger.drawn == 0
    assert r.unmatched_receipts == {PAYMENT: SYRUP_COST}
