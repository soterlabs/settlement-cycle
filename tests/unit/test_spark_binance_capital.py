import gzip
import json
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from decimal import localcontext
from pathlib import Path

import pytest

from settle.compute.allocation_capital import replay_history
from settle.compute.spark_binance_capital import (
    ACCOUNT,
    CASH,
    MARKER,
    PREFIX,
    RULES,
    SAVER,
    SKY_MARKER,
    VENUE,
    link_spark_binance,
)
from settle.extract._keccak import keccak256
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import (
    AssetMovement,
    CapitalBatch,
    CapitalHistory,
    ExternalFundingOperation,
)

PROOF = json.loads(gzip.decompress(
    (Path(__file__).parents[1] / 'fixtures/spark_binance_otc_history.json.gz').read_bytes()))


def history():
    batches = []
    for raw in PROOF['normalized_batches']:
        b = dict(raw)
        b['day'] = date.fromisoformat(b['day'])
        b['minted'] = D(b['minted'])
        b['minted_by_ilk'] = {k: D(v) for k, v in b['minted_by_ilk'].items()}
        b['external_funding'] = tuple(ExternalFundingOperation(x['kind'], x['source'], D(x['amount']))
                                      for x in b['external_funding'])
        b['movements'] = tuple(AssetMovement(m['account'], D(m['value_before']), D(m['change']),
                                               D(m['external_income']), m['preserve_basis'])
                                for m in b['movements'])
        batches.append(CapitalBatch(**b))
    return CapitalHistory(tuple(batches), {'S1': PREFIX + '0xc02ab1a5eaa8d1b114ef786d9bde108cd4364359'}, {})


def test_contract_events_and_actual_boundary_cash_agree():
    sent = '0x' + keccak256(b'OTCSwapSent(address,address,address,uint256,uint256)').hex()
    claimed = '0x' + keccak256(b'OTCClaimed(address,address,address,uint256,uint256)').hex()
    selected = [r for r in PROOF['rows'] if r['topic0'] in (sent, claimed)]
    assert len(selected) == 3
    expected = [2_000_000_000, 2_249_428_190, 2_000_000_000]
    for row, amount in zip(selected, expected, strict=True):
        assert row['address'] == '0x5c46fc65855c0c7465a1ea85eea0b24b601502d3'
        assert '0x' + row['topic1'][-40:] == PROOF['exchange']
        assert '0x' + row['topic2'][-40:] == PROOF['buffer']
        assert '0x' + row['topic3'][-40:] == CASH.split(':')[-1]
        assert int(row['data'][2:66], 16) == amount
        assert int(row['data'][66:], 16) == amount * 10**12
        source, target = ((PROOF['holder'], PROOF['exchange']) if row['topic0'] == sent
                          else (PROOF['buffer'], PROOF['holder']))
        transfers = [r for r in PROOF['rows'] if r['transaction_hash'] == row['transaction_hash']
                     and r['address'] == CASH.split(':')[-1] and r['topic0'] == TRANSFER_TOPIC0
                     and '0x' + r['topic1'][-40:] == source and '0x' + r['topic2'][-40:] == target]
        assert sum(int(r['data'], 16) for r in transfers) == amount
    balances = {}
    for row in PROOF['buffer_history']['rows']:
        delta = int(row['data'], 16) * ((row['topic2'][-40:] == PROOF['buffer'][2:])
                                       - (row['topic1'][-40:] == PROOF['buffer'][2:]))
        balances[row['address']] = balances.get(row['address'], 0) + delta
    assert balances == {k: int(v) for k, v in PROOF['buffer_history']['closing_balances'].items()}


def test_first_test_is_saver_funded_without_diluting_sky_deposits():
    h = history()
    first = replace(h, batches=(h.batches[0],))
    linked = link_spark_binance(first)
    assert len(linked.batches) == 2
    with localcontext() as ctx:
        ctx.prec = 100
        assert sum(b.minted for b in linked.batches) == first.batches[0].minted
    result = replay_history(linked, date(2026, 8, 12), date(2026, 8, 12))
    account = result.ledger.accounts[ACCOUNT]
    assert account.borrowed == 0
    assert account.external_by_source[SAVER] == D(2000)
    assert account.value == D(2000)
    for m in h.batches[0].movements:
        if m.change > 0:
            assert abs(result.ledger.accounts[m.account].borrowed - m.change) < D('1e-15')
    assert link_spark_binance(linked) == linked


def test_claim_caps_principal_leaves_excess_unknown_and_keeps_debt():
    h = replace(history(), analytics_only_venues=('ZZZ', 'AAA'))
    linked = link_spark_binance(h)
    assert linked.venue_accounts[VENUE] == ACCOUNT
    assert linked.analytics_only_venues == tuple(sorted(('ZZZ', 'AAA', VENUE)))
    assert sum(b.minted for b in linked.batches) == sum(b.minted for b in h.batches)
    claims = [m for b in linked.batches for m in b.movements if m.account == ACCOUNT]
    assert [(m.value_before, m.change) for m in claims] == [(D(0), D(2000)), (D(2000), D(-2000)), (D(0), D(2000))]
    assert all(m.preserve_basis and not m.external_income for m in claims)
    old_income = sum(m.external_income for b in h.batches for m in b.movements)
    assert sum(m.external_income for b in linked.batches for m in b.movements) == old_income
    assert link_spark_binance(linked) == linked
    without_savings = replace(h, batches=tuple(replace(b, external_funding=()) for b in h.batches))
    assert sum(m.change for b in link_spark_binance(without_savings).batches for m in b.movements
               if m.account == ACCOUNT) == D(2000)


def test_reject_changed_funding_missing_history_or_partial_repair():
    h = history()
    b = h.batches[0]
    changed = replace(b, external_funding=(replace(b.external_funding[0], amount=D(2001)),))
    with pytest.raises(ValueError, match='saver funding changed'):
        link_spark_binance(replace(h, batches=(changed, *h.batches[1:])))
    with pytest.raises(ValueError, match='historical leg missing'):
        link_spark_binance(replace(h, batches=h.batches[1:]))
    linked = link_spark_binance(h)
    with pytest.raises(ValueError, match='funding branch'):
        link_spark_binance(replace(linked, batches=tuple(b for b in linked.batches
                                                        if b.identity != RULES[0][0] + SKY_MARKER)))
    with pytest.raises(ValueError, match='Partially repaired'):
        link_spark_binance(replace(linked, batches=tuple(
            replace(b, identity=RULES[2][0]) if b.identity == RULES[2][0] + MARKER else b
            for b in linked.batches)))
    corrupted = replace(linked, batches=tuple(
        replace(b, movements=tuple(replace(m, change=D(2001)) if m.account == ACCOUNT else m
                                  for m in b.movements))
        if b.identity == RULES[2][0] + MARKER else b for b in linked.batches))
    with pytest.raises(ValueError, match='Repaired Binance claim changed'):
        link_spark_binance(corrupted)
