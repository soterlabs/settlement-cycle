import gzip
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_buidl_redemptions import (
    CASH,
    HOLDER,
    LINKS,
    SHARES,
    SUFFIX,
    link_spark_buidl_redemptions,
)
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

F = json.loads(gzip.decompress((Path(__file__).parents[1] / 'fixtures/spark_buidl_redemptions.json.gz').read_bytes()))


def history():
    batches = []
    for row in F['normalized_batches']:
        b = dict(row)
        b['day'] = date.fromisoformat(b['day'])
        b['minted'] = D(b['minted'])
        b['minted_by_ilk'] = {k: D(v) for k, v in b['minted_by_ilk'].items()}
        b['movements'] = tuple(AssetMovement(m['account'], D(m['value_before']), D(m['change']), D(m['external_income']), m['preserve_basis']) for m in b['movements'])
        batches.append(CapitalBatch(**b))
    return CapitalHistory(tuple(batches), {'S19': SHARES, 'cash': CASH}, {})


def test_two_requests_and_three_cash_payments_are_unique_at_the_actual_boundary():
    rows = [r for r in F['boundary_logs'] if r['topic0'] == TRANSFER_TOPIC0]
    requests = [r for r in rows if r['address'] == SHARES.split(':')[2]
                and r['topic1'].endswith(HOLDER[2:]) and r['topic2'].endswith('8780dd016171b91e4df47075da0a947959c34200')]
    assert len(requests) == 2
    for tx, block, stamp, face, payments in LINKS:
        req = next(r for r in requests if r['transaction_hash'] == tx)
        assert (req['block_number'], req['block_time'], D(int(req['data'], 16)) / 10**6) == (block, stamp, face)
        paid = D(0)
        for cash_tx, cb, cs, amount in payments:
            candidates = [r for r in rows if r['transaction_hash'] == cash_tx and r['address'] == CASH.split(':')[2]
                          and r['topic1'].endswith('cfc0f98f30742b6d880f90155d4ebb885e55ab33') and r['topic2'].endswith(HOLDER[2:])]
            assert len(candidates) == 1
            r = candidates[0]
            assert (r['block_number'], r['block_time'], D(int(r['data'], 16)) / 10**6) == (cb, cs, amount)
            assert block < cb and stamp < cs
            paid += amount
        assert abs(face * D('.9995') - paid) < 3
        # No competing request is outstanding during either reviewed payment group.
        assert not any(block < r['block_number'] <= payments[-1][1] for r in requests)
    assert sum(x[3] for x in LINKS) == D('200250000')
    assert sum(p[3] for *_, ps in LINKS for p in ps) == D('200149871.526544')


def test_actual_snapshot_preserves_debt_and_cash_and_leaves_unassigned_april_receipt():
    h = history()
    linked = link_spark_buidl_redemptions(h)
    assert link_spark_buidl_redemptions(linked) is linked
    assert len(linked.batches) == len(h.batches)
    april = h.batches[0]
    assert next(b for b in linked.batches if b.identity == april.identity) is april
    for b in h.batches[1:]:
        after = next(x for x in linked.batches if x.identity == b.identity + SUFFIX)
        assert b.minted == after.minted and b.minted_by_ilk == after.minted_by_ilk
        assert b.movements[0].change == after.movements[0].change
        assert b.movements[0].value_before == after.movements[0].value_before
    assert len(linked.custody_accounts['S19']) == 2 and not linked.idle_accounts


def seeded(link, fraction=D(1)):
    tx, _, _, face, payments = link
    h = history()
    ids = {'ethereum:' + tx, *('ethereum:' + p[0] for p in payments)}
    bs = [b for b in h.batches if b.identity in ids]
    source = bs[0]
    # Controlled funding mix; canonical event values are tested separately.
    bs[0] = replace(source, movements=(replace(source.movements[0], value_before=face),))
    fund = CapitalBatch('fund', source.day, source.timestamp-1, 'ethereum', source.block-1,
                        (AssetMovement(SHARES, D(0), face, face * (1-fraction)),), face*fraction)
    return replace(h, batches=(fund, *bs))


@pytest.mark.parametrize('link', LINKS)
@pytest.mark.parametrize('fraction', [D(1), D('.4')])
def test_exit_never_borrows_gains_and_only_loses_basis_after_equity_is_exhausted(link, fraction):
    h = seeded(link, fraction)
    linked = link_spark_buidl_redemptions(h)
    r = replay_history(linked, h.batches[0].day, h.batches[-1].day)
    paid = sum(p[3] for p in link[4])
    assert r.ledger.account(CASH).value == paid
    # Existing funding policy: own earnings absorb the exit charge before
    # borrowed principal is impaired; gains never become new borrowed basis.
    expected_basis = min(link[3] * fraction, paid)
    assert abs(r.ledger.account(CASH).borrowed - expected_basis) < D('1e-12')
    assert abs(r.ledger.realised_principal_loss - (link[3]*fraction-expected_basis)) < D('1e-12')
    assert not r.unmatched_receipts and not r.unmatched_outflows
    claim = linked.custody_accounts['S19'][0]
    assert r.ledger.account(claim).value == r.ledger.account(claim).borrowed == 0


def test_partial_cutoff_retains_unpaid_basis_and_unfunded_receipts_remain_unknown():
    h = seeded(LINKS[1])
    cutoff = replace(h, batches=h.batches[:-1])
    linked = link_spark_buidl_redemptions(cutoff)
    r = replay_history(linked, h.batches[0].day, h.batches[-1].day)
    assert r.ledger.account(CASH).borrowed == D('98.543517')
    assert r.ledger.account(linked.custody_accounts['S19'][0]).borrowed == D('200000000') - D('98.543517')
    assert r.ledger.realised_principal_loss == 0
    receipts_only = replace(h, batches=h.batches[2:])
    assert link_spark_buidl_redemptions(receipts_only) is receipts_only
    r = replay_history(receipts_only, h.batches[0].day, h.batches[-1].day)
    assert r.ledger.account(CASH).borrowed == 0 and r.unmatched_receipts


def test_altered_cash_missing_advance_and_mixed_linked_raw_events_are_rejected():
    h = seeded(LINKS[1])
    with pytest.raises(ValueError, match='lacks prior payment'):
        link_spark_buidl_redemptions(replace(h, batches=(*h.batches[:2], h.batches[-1])))
    wrong = replace(h.batches[-1], movements=(replace(h.batches[-1].movements[0], change=D(42)),))
    with pytest.raises(ValueError, match='settlement differs'):
        link_spark_buidl_redemptions(replace(h, batches=(*h.batches[:-1], wrong)))
    linked = link_spark_buidl_redemptions(h)
    with pytest.raises(ValueError, match='append raw'):
        link_spark_buidl_redemptions(replace(linked, batches=(*linked.batches, h.batches[-1])))
    other = replace(h, venue_accounts={'E10': 'grove'})
    assert link_spark_buidl_redemptions(other) is other
