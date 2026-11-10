"""Real gateway evidence, source-chain ownership and daily CoF conservation."""
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal as D
from pathlib import Path

import pandas as pd
import pytest

from settle.compute.sky_revenue import compute_sky_revenue_daily
from settle.domain import Chain, Month, Period
from settle.domain.config import load_prime_by_id
from settle.extract.hypersync import LogRow
from settle.normalize import usds_bridge_transit as bridge

FIXTURE = Path(__file__).parents[1] / 'fixtures/usds_bridge/spark_october_2026.json'


def sample():
    return {c: [LogRow(**r) for r in rows] for c, rows in json.loads(FIXTURE.read_text()).items()}


def reader(logs):
    def fetch(chain, selections, start, end, **kw):
        return [r for r in logs.get(chain, []) if r.address in selections[0]['address']
                and r.topic0 in selections[0]['topics'][0] and start <= r.block_number <= end]
    return fetch


def period(month=10):
    return Period.from_month(Month(2026, month), {c: 600000000 for c in Chain})


def test_real_october_gateway_pair_preserves_exact_claim_and_daily_deduction():
    prime = load_prime_by_id('spark')
    transfers = bridge.collect_transfers(prime, period(), fetch=reader(sample()))
    assert len(transfers) == 1
    t = transfers[0]
    assert t['amount_usds'] == '99326272.77906090005429408'
    assert t['source_chain'] == 'arbitrum' and t['destination_chain'] == 'ethereum'
    exposure = bridge.daily_exposure(period(), transfers)
    active = exposure[exposure.cum_balance > 0]
    assert active.block_date.tolist() == [date(2026, 10, d) for d in range(12, 19)]
    assert all(row == {'arbitrum': t['amount_usds']} for row in active.source_chain_claims)
    # The old wallet-only base loses the deduction for seven closing days.
    amount = D(t['amount_usds'])
    wallets = exposure[['block_date']].copy()
    wallets['cum_balance'] = [D(0) if d in active.block_date.tolist() else amount for d in wallets.block_date]
    debt = pd.DataFrame({'block_date': [date(2026, 10, 1)], 'cum_debt': [amount]})
    ssr = pd.DataFrame({'effective_date': [date(2026, 1, 1)], 'ssr_apy': [D('.04')]})
    before, _, _ = compute_sky_revenue_daily(period(), debt, wallets, ssr)
    after, daily, _ = compute_sky_revenue_daily(period(), debt, wallets, ssr, bridge_usds=exposure)
    assert before > 0 and after == 0
    assert (daily['alm_usds'] + daily['bridge_usds']).tolist() == [amount] * 31
    assert daily['cum_debt'].tolist() == [amount] * 31


def test_unfinished_claim_survives_month_boundary_and_stops_on_release_date():
    logs = sample()
    logs['ethereum'] = []
    pending = bridge.collect_transfers(load_prime_by_id('spark'), period(), fetch=reader(logs))
    assert pending[0]['received_at'] is None
    assert bridge.daily_exposure(period(11), pending).cum_balance.min() > 0
    sent = datetime(2026, 9, 29, tzinfo=UTC).timestamp()
    done = datetime(2026, 10, 2, tzinfo=UTC).timestamp()
    t = {**pending[0], 'sent_at': sent, 'received_at': done}
    assert bridge.daily_exposure(period(), [t]).iloc[0].cum_balance > 0
    assert bridge.daily_exposure(period(), [t]).iloc[1].cum_balance == 0
    t['received_at'] = sent
    assert bridge.daily_exposure(period(9), [t]).cum_balance.sum() == 0


def test_delivery_without_origin_and_conflicting_duplicates_fail():
    logs = sample()
    logs['arbitrum'] = []
    with pytest.raises(ValueError, match='no matching'):
        bridge.collect_transfers(load_prime_by_id('spark'), period(), fetch=reader(logs))
    logs = sample()
    origin = logs['arbitrum'][0]
    logs['arbitrum'].append(replace(origin, transaction_hash='0x' + '11' * 32))
    with pytest.raises(ValueError, match='Conflicting'):
        bridge.collect_transfers(load_prime_by_id('spark'), period(), fetch=reader(logs))


def test_foreign_recipient_is_not_an_owned_claim():
    logs = sample()
    for chain in logs:
        logs[chain] = [replace(r, topic2='0x' + '00' * 12 + '11' * 20) for r in logs[chain]]
    assert bridge.collect_transfers(load_prime_by_id('spark'), period(), fetch=reader(logs)) == []


def test_zero_floor_is_preserved():
    rows = pd.DataFrame({'block_date': [date(2026, 10, 1)], 'cum_balance': [D(100)]})
    debt = pd.DataFrame({'block_date': [date(2026, 10, 1)], 'cum_debt': [D(50)]})
    ssr = pd.DataFrame({'effective_date': [date(2026, 1, 1)], 'ssr_apy': [.04]})
    total, daily, _ = compute_sky_revenue_daily(period(), debt, None, ssr, bridge_usds=rows)
    assert total == 0 and daily.daily_sky_rev.max() == 0


def word(value):
    return f'{value:064x}' if isinstance(value, int) else value.removeprefix('0x').rjust(64, '0')


def synthetic_pair(l2, outbound):
    prime = load_prime_by_id('spark')
    family, l1_gateway, l2_gateway = bridge.ROUTES[l2]
    source, destination = (Chain.ETHEREUM, l2) if outbound else (l2, Chain.ETHEREUM)
    sender, receiver = prime.alm[source].hex, prime.alm[destination].hex
    events = {source.value: [], destination.value: []}
    for send, chain in [(True, source), (False, destination)]:
        gateway = l1_gateway if chain == Chain.ETHEREUM else l2_gateway
        amount = 10**20
        if family == 'op':
            remote = destination if send else source
            topics = [bridge.OP_SEND if send else bridge.OP_RECEIVE,
                      '0x' + word(bridge.USDS_BY_CHAIN[chain].address.hex),
                      '0x' + word(bridge.USDS_BY_CHAIN[remote].address.hex), '0x' + word(sender)]
            data = '0x' + word(receiver) + word(amount) + word(96) + word(0)
        elif send and outbound:
            topics = [bridge.ARB_DEPOSIT, '0x' + word(sender), '0x' + word(receiver), '0x' + word(7)]
            data = '0x' + word(bridge.USDS_BY_CHAIN[Chain.ETHEREUM].address.hex) + word(amount)
        elif not send and outbound:
            topics = [bridge.ARB_DEPOSIT_DONE, '0x' + word(bridge.USDS_BY_CHAIN[Chain.ETHEREUM].address.hex),
                      '0x' + word(sender), '0x' + word(receiver)]
            data = '0x' + word(amount)
        elif send:
            topics = [bridge.ARB_WITHDRAW, '0x' + word(sender), '0x' + word(receiver), '0x' + word(7)]
            data = '0x' + word(bridge.USDS_BY_CHAIN[Chain.ETHEREUM].address.hex) + word(0) + word(amount)
        else:
            topics = [bridge.ARB_WITHDRAW_DONE, '0x' + word(sender), '0x' + word(receiver), '0x' + word(0)]
            data = '0x' + word(bridge.USDS_BY_CHAIN[Chain.ETHEREUM].address.hex) + word(amount)
        events[chain.value].append(LogRow(100 if send else 200, 1,
            int(datetime(2026, 10, 1 if send else 3, tzinfo=UTC).timestamp()),
            gateway, *topics, data, '0x' + ('11' if send else '22') * 32))
    return events


@pytest.mark.parametrize('l2', list(bridge.ROUTES))
@pytest.mark.parametrize('outbound', [True, False])
def test_canonical_routes_in_both_directions(l2, outbound):
    transfers = bridge.collect_transfers(load_prime_by_id('spark'), period(), fetch=reader(synthetic_pair(l2, outbound)))
    assert len(transfers) == 1
    assert transfers[0]['source_chain'] == ('ethereum' if outbound else l2.value)
    assert transfers[0]['destination_chain'] == (l2.value if outbound else 'ethereum')
    assert bridge.daily_exposure(period(), transfers).cum_balance.sum() == 200


def test_wrong_token_and_repeated_identical_transfers():
    events = synthetic_pair(Chain.BASE, True)
    # Native USDS identity is essential; a parallel token such as sUSDS is not exempted.
    changed = {c: [replace(r, topic1='0x' + word('0x' + '99' * 20)) for r in rows] for c, rows in events.items()}
    assert bridge.collect_transfers(load_prime_by_id('spark'), period(), fetch=reader(changed)) == []
    for chain in events:
        events[chain] += [replace(r, log_index=2) for r in events[chain]]
    transfers = bridge.collect_transfers(load_prime_by_id('spark'), period(), fetch=reader(events))
    assert len(transfers) == 2
    assert bridge.daily_exposure(period(), transfers).cum_balance.sum() == 400


def test_missing_pin_and_zero_block_timestamp_are_not_zero_exposure():
    prime = load_prime_by_id('spark')
    with pytest.raises(ValueError, match='missing chain pin'):
        bridge.collect_transfers(prime, replace(period(), pin_blocks={Chain.ETHEREUM: 600000000}), fetch=reader(sample()))
    logs = sample()
    logs['arbitrum'] = [replace(r, block_time=0) for r in logs['arbitrum']]
    with pytest.raises(ValueError, match='identity'):
        bridge.collect_transfers(prime, period(), fetch=reader(logs))


def test_receipt_before_origin_on_same_day_is_invalid():
    with pytest.raises(ValueError, match='Invalid USDS bridge claim'):
        bridge.daily_exposure(period(), [dict(sent_at=1790812801, received_at=1790812800,
                                             amount_usds='100', source_chain='base')])


def test_2026_historical_gateway_evidence_replays_seven_claims():
    path = FIXTURE.with_name('spark_2026_gateway_logs.json')
    logs = {c: [LogRow(**r) for r in rows] for c, rows in json.loads(path.read_text()).items()}
    pins = {Chain.ETHEREUM: 26373602, Chain.ARBITRUM: 522512200, Chain.BASE: 54960121,
            Chain.OPTIMISM: 159291811, Chain.UNICHAIN: 63434040}
    span = Period(date(2026, 1, 1), date(2026, 11, 8), pins)
    transfers = bridge.collect_transfers(load_prime_by_id('spark'), span, fetch=reader(logs))
    assert len(transfers) == 7
    assert sum(t['received_at'] is None for t in transfers) == 2
    daily = bridge.daily_exposure(span, transfers).set_index('block_date')
    assert daily.loc[date(2026, 7, 6)].cum_balance == D('346875703.611569069692530370')
    assert daily.loc[date(2026, 7, 13)].cum_balance == D('30000')
    assert daily.loc[date(2026, 11, 2)].cum_balance == D('30000')
    assert daily.loc[date(2026, 11, 3)].cum_balance == D('20000')
    for name in ['grove', 'obex', 'osero', 'keel', 'skybase']:
        assert bridge.collect_transfers(load_prime_by_id(name), span, fetch=reader(logs)) == []


def test_workbook_bridge_and_basin_columns_stay_aligned():
    from openpyxl import Workbook

    from scripts.build_settlement_xlsx import _write_debt
    row = dict(date='2026-10-12', cum_debt='1000', alm_usds='20', bridge_usds='100',
               psm_usds='30', sde_av='40', curve_idle='50', lending_idle='60',
               basin_idle='70', utilized='630', ssr_apy=.04, base_apr=.042,
               daily_sky_rev='1', daily_sky_rev_gross='2')
    workbook = Workbook()
    _write_debt(workbook.active, {'sky_revenue_daily': [row]})
    rows = list(workbook.active.values)
    headers = next(r for r in rows if r[0] == 'Date')
    values = next(r for r in rows if r[0] == row['date'])
    assert values[headers.index('- In-transit USDS')] == 100
    assert values[headers.index('- Basin idle')] == 70
    assert values[headers.index('= utilized')] == 630
    assert values[headers.index('base APR')] == .042


def test_monthly_orchestration_wires_gateway_claim_into_cost_and_provenance(monkeypatch):
    from settle.compute import Sources, compute_monthly_pnl
    from settle.load.provenance import render_provenance
    from tests.fixtures.mock_sources import (
        MockBalanceSource,
        MockBlockResolver,
        MockConvertToAssetsSource,
        MockDebtSource,
        MockPositionBalanceSource,
        MockSSRSource,
    )
    monkeypatch.setattr('settle.extract.rpc.ilk_rate', lambda *a, **kw: 10**27)
    spark = load_prime_by_id('spark')
    prime = replace(load_prime_by_id('obex'), venues=[],
                    alm={c: spark.alm[c] for c in [Chain.ETHEREUM, Chain.BASE]})
    sources = Sources(block_resolver=MockBlockResolver(default=200),
        balance=MockBalanceSource(), debt=MockDebtSource(pd.DataFrame({
            'block_date': [date(2026, 10, 1)], 'daily_dart': [200], 'cum_debt': [200]})),
        ssr=MockSSRSource(pd.DataFrame({'effective_date': [date(2026, 10, 1)], 'ssr_apy': [.04]})),
        position_balance=MockPositionBalanceSource(raw_balance=0),
        convert_to_assets=MockConvertToAssetsSource(raw_assets=10**6))
    pins = {Chain.ETHEREUM: 200, Chain.BASE: 200}
    before = compute_monthly_pnl(prime, Month(2026, 10), sources=sources,
                                pin_blocks_som=pins, pin_blocks_eom=pins)
    sources = replace(sources, usds_bridge_logs=reader(synthetic_pair(Chain.BASE, True)))
    after = compute_monthly_pnl(prime, Month(2026, 10), sources=sources,
                               pin_blocks_som=pins, pin_blocks_eom=pins)
    assert after.sky_revenue < before.sky_revenue
    assert sum(D(r['bridge_usds']) for r in after.sky_revenue_daily) == 200
    assert len(render_provenance(after)['usds_bridge_transfers']) == 1


def test_amount_conversion_is_independent_of_decimal_precision():
    from decimal import localcontext
    with localcontext() as ctx:
        ctx.prec = 12
        assert bridge.usds_amount(123456789012345678901234567890) == '123456789012.34567890123456789'


@pytest.mark.parametrize('mutation', [
    lambda t: t.update(recipient='0x' + '11'*20),
    lambda t: t.update(destination_chain='optimism'),
    lambda t: t.update(source_block=600000001),
    lambda t: t.update(destination_block=600000001),
    lambda t: t.update(destination_log_index=None),
    lambda t: t.update(received_at=None),
    lambda t: t.update(source_transaction='unknown'),
])
def test_retained_claims_reject_wrong_ownership_route_and_pin(mutation):
    prime = load_prime_by_id('spark')
    claims = bridge.collect_transfers(prime, period(), fetch=reader(sample()))
    mutation(claims[0])
    with pytest.raises(ValueError, match='Invalid retained USDS bridge claim'):
        bridge.validate_transfers(prime, period(), claims)


def test_retained_claims_cannot_repeat_a_source_or_delivery():
    prime = load_prime_by_id('spark')
    claims = bridge.collect_transfers(prime, period(), fetch=reader(sample()))
    with pytest.raises(ValueError, match='duplicate event'):
        bridge.validate_transfers(prime, period(), claims + claims)
