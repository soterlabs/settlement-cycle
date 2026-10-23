import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_buidl_partial_payments import (
    CASH,
    LINKS,
    link_grove_buidl_partial_payments,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROWS = json.loads((Path(__file__).parents[1] / 'fixtures/grove_buidl_partial_payment_events.json').read_text())
SHARES = 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041'


def row(tx):
    return next(r for r in ROWS if r['transaction_hash'] == tx)


def test_all_six_cash_groups_match_actual_issuer_payments_and_request():
    assert len(LINKS) == 6 and len(ROWS) == 18
    for req, log, rb, face, partial, pb, advance, final, fb, cash in LINKS:
        for tx, block, amount in [(req, rb, face), (partial, pb, advance), (final, fb, cash)]:
            r = row(tx)
            assert r['block_number'] == block
            assert D(int(r['data'], 16))/10**6 == amount
            if tx == req:
                assert r['log_index'] == log
                assert r['topic2'].endswith('8780dd016171b91e4df47075da0a947959c34200')
            else:
                assert r['topic1'].endswith('cfc0f98f30742b6d880f90155d4ebb885e55ab33')
                assert r['topic2'].endswith('491edfb0b8b608044e227225c715981a30f3a44e')
        assert rb < pb < fb
        assert abs(face * D('.9995') - advance - cash) < D(5)
        assert datetime.fromtimestamp(row(partial)['block_time'], UTC).date() == \
               datetime.fromtimestamp(row(final)['block_time'], UTC).date()


def history(link=LINKS[0]):
    req, log, rb, face, partial, pb, advance, final, fb, cash = link
    claim = f'redemption:ethereum:{req}:{log}'
    t = row(req)['block_time']
    day = datetime.fromtimestamp(t, UTC).date()
    batches = [CapitalBatch('fund', day, t-1, 'ethereum', rb-1,
                           (AssetMovement(SHARES, D(0), face),), face)]
    for tx, block, movements in [
        (req, rb, (AssetMovement(SHARES, face, -face, preserve_basis=True),
                   AssetMovement(claim, D(0), face, preserve_basis=True))),
        (partial, pb, (AssetMovement(CASH, D(0), advance),)),
        (final, fb, (AssetMovement(CASH, advance, cash), AssetMovement(claim, cash, -cash))),
    ]:
        t = row(tx)['block_time']
        batches.append(CapitalBatch('ethereum:'+tx, datetime.fromtimestamp(t, UTC).date(),
                                   t, 'ethereum', block, movements))
    return CapitalHistory(tuple(batches), {'E10': SHARES, 'E15': CASH}, {}, {'E10': [claim]})


def test_partial_and_final_release_one_claim_without_unknown_income():
    h = history()
    fixed = link_grove_buidl_partial_payments(h)
    assert fixed == link_grove_buidl_partial_payments(fixed)
    r = replay_history(fixed, h.batches[0].day, h.batches[-1].day)
    face, advance, cash = LINKS[0][3], LINKS[0][6], LINKS[0][9]
    assert r.ledger.account(CASH).value == cash + advance
    assert r.ledger.account(CASH).borrowed == cash + advance
    assert r.ledger.drawn == face
    assert r.ledger.realised_principal_loss == face - cash - advance
    assert not r.unmatched_receipts and not r.unmatched_outflows
    claim = h.custody_accounts['E10'][0]
    assert r.ledger.account(claim).value == r.ledger.account(claim).borrowed == 0


def test_cutoff_after_advance_retains_unpaid_claim_without_looking_ahead():
    h = history()
    h = replace(h, batches=h.batches[:-1])
    fixed = link_grove_buidl_partial_payments(h)
    r = replay_history(fixed, h.batches[0].day, h.batches[-1].day)
    face, advance = LINKS[0][3], LINKS[0][6]
    assert len(fixed.batches) == len(h.batches)
    assert r.ledger.account(h.custody_accounts['E10'][0]).borrowed == face - advance
    assert r.ledger.account(CASH).borrowed == advance
    assert not r.unmatched_receipts


def test_wrong_advance_or_final_or_missing_source_fails():
    h = history()
    for idx in (2, 3):
        b = h.batches[idx]
        bs = list(h.batches)
        bs[idx] = replace(b, movements=(replace(b.movements[0], change=D(42)), *b.movements[1:]))
        with pytest.raises(ValueError, match='differs'):
            link_grove_buidl_partial_payments(replace(h, batches=tuple(bs)))
    with pytest.raises(ValueError, match='lacks its funded request'):
        link_grove_buidl_partial_payments(replace(h, batches=h.batches[2:]))


@pytest.mark.parametrize('link', LINKS)
def test_each_reviewed_group_applies_and_preserves_original_movements(link):
    h = history(link)
    # This request shares a transaction with an authenticated JAAA transfer.
    if link == LINKS[1]:
        bs = list(h.batches)
        bs[1] = replace(bs[1], identity=bs[1].identity + ':jaaa-crosschain')
        h = replace(h, batches=tuple(bs))
    fixed = link_grove_buidl_partial_payments(h)
    assert fixed.batches[0] == h.batches[0]
    assert fixed.batches[1] == h.batches[1]
    assert fixed.batches[-1] == h.batches[-1]
    assert fixed.batches[2].movements[:-1] == h.batches[2].movements
    assert fixed.batches[2].movements[-1].change == -link[6]
