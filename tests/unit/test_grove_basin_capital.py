import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.grove_basin_capital import EVENTS, link_grove_basin_shares
from settle.extract.hypersync import LogRow
from settle.normalize.allocation_basin import (
    BASINS,
    DECIMALS,
    ESCROW,
    FEES,
    HOLDER,
    basin_account,
    basin_events,
    link_basin_shares,
)
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

ROWS = [LogRow(**r) for r in json.loads(
    (Path(__file__).parents[1] / 'fixtures/grove_basin_capital_events.json').read_text())]
USDS = next(t for t, d in DECIMALS.items() if d == 18)
CASH = f'ethereum:{HOLDER}:{USDS}'
OLD_ESCROW = f'ethereum:{ESCROW}:{USDS}'
ILK = '0x414c4c4f4341544f522d47524f56452d41000000000000000000000000000'


def test_reviewed_events_equal_contract_logs_and_match_actual_cash_transfers():
    assert basin_events(ROWS) == list(EVENTS)
    assert len(EVENTS) == 12
    for tx, block, _, _, _, deposit, token, amount, _ in EVENTS:
        matches = [r for r in ROWS if r.transaction_hash == tx and r.address == token
                   and r.topic0 == '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
                   and (r.topic1 if deposit else r.topic2).endswith(HOLDER[2:])]
        assert len(matches) == 1
        assert matches[0].block_number == block and int(matches[0].data, 16) == amount


def history(events):
    # Actual event amounts; deposits financed by own-ilk debt and withdrawals
    # immediately repaid, as in the July 29 path. Pool custody intentionally
    # remains in the old escrow observation until the adapter replaces it.
    batches, escrow = [], D(0)
    for tx, block, timestamp, log_index, _, deposit, token, assets, _ in events:
        amount = D(assets) / 10**DECIMALS[token]
        movement = AssetMovement(OLD_ESCROW, escrow, amount if deposit else D(0))
        if deposit:
            escrow += amount
        minted = amount if deposit else -amount
        batches.append(CapitalBatch('ethereum:' + tx, datetime.fromtimestamp(timestamp, UTC).date(),
            timestamp, 'ethereum', block, (movement, AssetMovement(CASH, D(0), D(0))),
            minted, log_index, {ILK: minted}))
    return CapitalHistory(tuple(batches), {'E40': CASH, 'E41': OLD_ESCROW}, {})


def test_withdrawal_releases_lp_basis_even_while_usds_stays_in_pocket():
    # The real July 21 deposit and July 22/29 withdrawals.
    events = [e for e in EVENTS if e[1] in (25582253, 25589861, 25640345)]
    h = history(events)
    fixed = link_basin_shares(h, events)
    assert fixed == link_basin_shares(fixed, events)
    assert 'E41' not in fixed.venue_accounts
    assert fixed.covered_by_boundary['E41'] == 'E_JTRSY_BASIN'
    assert all(m.account != OLD_ESCROW for b in fixed.batches for m in b.movements)
    r = replay_history(fixed, h.batches[0].day, h.batches[-1].day)
    account = basin_account(events[0][4])
    assert not r.unmatched_receipts and not r.unmatched_outflows
    # 1m - 111084.67881 cash repayment; the redemption's accrued gain is
    # available to repay debt too. It never becomes a new debt draw.
    assert r.ledger.drawn == D('1000000')
    assert r.ledger.repaid == D('111084.67881')
    assert abs(r.ledger.account(account).borrowed - D('888915.32119')) < D('1e-18')


def test_full_history_and_cutoff_are_idempotent_and_keep_debt_unchanged():
    h = history(EVENTS)
    fixed = link_grove_basin_shares(h)
    assert fixed == link_grove_basin_shares(fixed)
    assert sum(b.minted for b in fixed.batches) == sum(b.minted for b in h.batches)
    cutoff = replace(h, batches=tuple(b for b in h.batches if b.block < 25640345))
    before = link_grove_basin_shares(cutoff)
    assert len(before.batches) == len(cutoff.batches)
    assert max(b.block for b in before.batches) < 25640345
    assert {'E_JTRSY_BASIN', 'E_BUIDL_BASIN'} <= set(fixed.analytics_only_venues)


def test_missing_deposit_cannot_fund_redemption():
    withdrawal = [e for e in EVENTS if e[1] == 25640345]
    with pytest.raises(ValueError, match='exceeds observed'):
        link_basin_shares(history(withdrawal), withdrawal)


def test_unknown_asset_third_party_and_unvalued_fee_shares_fail_closed():
    r = next(r for r in ROWS if r.transaction_hash == EVENTS[0][0]
             and r.address in BASINS and r.topic2 and r.topic2.endswith(HOLDER[2:]))
    with pytest.raises(ValueError, match='Unsupported'):
        basin_events([replace(r, topic1='0x'+'99'*32)])
    with pytest.raises(ValueError, match='third-party'):
        basin_events([replace(r, topic3='0x'+'99'*32)])
    with pytest.raises(ValueError, match='fee shares'):
        basin_events([replace(r, topic0=FEES, topic1=r.topic2)])


def test_other_lp_and_other_prime_are_not_attributed_to_pau():
    r = next(r for r in ROWS if r.address in BASINS)
    assert basin_events([replace(r, topic2='0x'+'99'*32, topic3='0x'+'99'*32)]) == []
    h = history(EVENTS)
    other = replace(h, venue_accounts={'other': 'ethereum:someone:token'})
    assert link_grove_basin_shares(other) == other


def test_reinvested_earnings_and_full_exit_do_not_create_borrowed_basis():
    basin = next(iter(BASINS))
    t = EVENTS[0][2]
    day = datetime.fromtimestamp(t, UTC).date()
    events = [
        ('deposit1', 1, t, 1, basin, True, USDS, 100 * 10**18, 100 * 10**18),
        ('deposit2', 3, t + 2, 1, basin, True, USDS, 50 * 10**18, 50 * 10**18),
        ('exit', 4, t + 3, 1, basin, False, USDS, 165 * 10**18, 150 * 10**18),
    ]
    batches = (
        CapitalBatch('ethereum:deposit1', day, t, 'ethereum', 1, (), D(100)),
        CapitalBatch('income', day, t + 1, 'ethereum', 2,
                     (AssetMovement(CASH, D(0), D(50), external_income=D(50)),)),
        CapitalBatch('ethereum:deposit2', day, t + 2, 'ethereum', 3,
                     (AssetMovement(CASH, D(50), D(-50)),)),
        CapitalBatch('ethereum:exit', day, t + 3, 'ethereum', 4,
                     (AssetMovement(CASH, D(0), D(165)),)),
    )
    h = CapitalHistory(batches, {'E40': CASH}, {})
    r = replay_history(link_basin_shares(h, events), day, day)
    assert r.ledger.account(basin_account(basin)).borrowed == 0
    assert r.ledger.account(CASH).borrowed == D(100)
    assert r.ledger.account(CASH).value == D(165)
    assert r.ledger.drawn == D(100)
    assert not r.unmatched_receipts and not r.unmatched_outflows
