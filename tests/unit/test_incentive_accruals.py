from datetime import date, timedelta
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from settle.compute.incentives import (
    EligibleBalance,
    apy_to_apr,
    daily_accrual,
    issuer_apr,
    reconcile,
)
from settle.domain.period import Period
from settle.domain.primes import Address, Chain
from settle.normalize.incentives import IncentiveInputs, compute_incentive


def test_pyusd_spread_switch_and_daily_compounding():
    fed = D('0.035')
    before = issuer_apr('pyusd', date(2026, 3, 8), fed)
    after = issuer_apr('pyusd', date(2026, 3, 9), fed)
    assert before == apy_to_apr(D('0.0545'))
    assert after == apy_to_apr(D('0.04675'))
    assert abs((1 + after / 365)**365 - 1 - D('0.04675')) < D('1e-24')
    assert before > after
    with pytest.raises(ValueError, match='Missing DFEDTARL'):
        issuer_apr('pyusd', date(2026, 3, 9))


def test_rlusd_top_up_is_net_of_fee_and_floored():
    # A 5% gross APR with 20% performance fee is 4% net, so 50bp top-up.
    earned, parts = daily_accrual('rlusd', date(2026, 1, 1), [
        EligibleBalance('raw', D(365000)),
        EligibleBalance('vault', D(365000), D('0.05') * (1 - D('0.2'))),
        EligibleBalance('vault_above_target', D(365000), D('0.06')),
    ])
    assert earned == 45 + 5
    assert parts[1]['apr'] == '0.005'
    assert D(parts[2]['accrual']) == 0


def test_accrued_balance_rolls_across_months_and_preserves_prepayment():
    start = date(2026, 1, 31)
    daily = {start: (D(100), []), date(2026, 2, 1): (D(20), [])}
    result = reconcile('rlusd', start, date(2026, 2, 1), date(2026, 2, 1),
                       daily, {start: D(150)}, D(10))
    assert result['period_opening_balance'] == '-40'
    assert result['period_accrual'] == '20'
    assert result['cumulative_accrual'] == '120'
    assert result['accrued_balance'] == '-20'
    assert result['daily'][0]['accrued_balance'] == '-20'
    with pytest.raises(ValueError, match='Missing daily'):
        reconcile('rlusd', start, start, start + timedelta(days=1), {}, {}, D(0))


def test_usdg_accrual_equals_receipts_not_all_vault_inflows():
    day = date(2026, 7, 1)
    result = reconcile('usdg', day, day, day, {}, {day: D('123.456')}, D(0))
    assert result['period_accrual'] == result['period_receipts'] == '123.456'
    assert result['accrued_balance'] == '0.000'


def test_receivable_row_reconciles_and_does_not_enable_settlement():
    start = date(2026, 3, 8)
    venue = SimpleNamespace(id='R', label='RLUSD incentives', pricing_category=SimpleNamespace(value='I'),
                            incentive={'program': 'rlusd', 'accrual_start': str(start),
                                       'opening_balance': '0', 'receipt_sources': [{'payer': 'test'}]})
    provider = SimpleNamespace(eligible=lambda v, d: [EligibleBalance('raw', D(365000))],
                               receipts=lambda *args: {start: D(100)})
    row = compute_incentive(None, venue, Period(start, start), None, provider=provider)
    assert not row.settlement_enabled
    assert row.revenue == 45
    assert row.value_eom == -55
    assert row.value_eom - row.value_som - row.period_inflow == row.revenue
    assert row.incentive_accrual['daily'][0]['components'][0]['balance'] == '365000'


def test_curve_pyusd_incentive_ends_after_june_30(monkeypatch):
    import settle.normalize.incentives as mod
    token = Address(bytes.fromhex('11'*20))
    pool = Address(bytes.fromhex('22'*20))
    holder = Address(bytes.fromhex('33'*20))
    source = SimpleNamespace(id='S25', token=SimpleNamespace(address=pool))
    prime = SimpleNamespace(venues=[source], alm={Chain.ETHEREUM: holder})
    state = SimpleNamespace(coins=[token], balances=[200_000_000], total_supply=100)
    sources = SimpleNamespace(block_resolver=object(), curve_pool=SimpleNamespace(read_pool=lambda *a: state))
    provider = IncentiveInputs(prime, sources)
    monkeypatch.setattr(provider, 'block', lambda *a: 100)
    monkeypatch.setattr(mod.rpc, 'balance_of', lambda *a: 25)
    venue = SimpleNamespace(chain=Chain.ETHEREUM, token=SimpleNamespace(address=token, decimals=6),
                            incentive={'components': [{'kind': 'curve', 'venue': 'S25', 'through': '2026-06-30'}]})
    assert provider.eligible(venue, date(2026, 6, 30))[0].balance == 50
    assert provider.eligible(venue, date(2026, 7, 1)) == []


def test_lending_eligibility_is_supplied_times_one_minus_utilization(monkeypatch):
    import settle.normalize.incentives as mod
    token, holder = Address(bytes.fromhex('11'*20)), Address(bytes.fromhex('22'*20))
    atoken, sd, vd = (Address(bytes.fromhex(h*20)) for h in ('33', '44', '55'))
    sources = SimpleNamespace(block_resolver=object())
    provider = IncentiveInputs(SimpleNamespace(venues=[], alm={Chain.ETHEREUM: holder}), sources)
    monkeypatch.setattr(provider, 'block', lambda *a: 100)
    monkeypatch.setattr(mod, '_call', lambda *a: [0]*8 + [int.from_bytes(x.value, 'big') for x in (atoken, sd, vd)])
    monkeypatch.setattr(mod.rpc, 'balance_of', lambda c, t, h, b: 100_000_000 if t == atoken else 40_000_000)
    monkeypatch.setattr(mod.rpc, 'total_supply_of', lambda c, t, b: 10_000_000 if t == sd else 150_000_000)
    venue = SimpleNamespace(chain=Chain.ETHEREUM, token=SimpleNamespace(address=token, decimals=6),
                            incentive={'components': [{'kind': 'lending_idle', 'pool': token.hex}]})
    assert provider.eligible(venue, date(2026, 7, 1))[0].balance == 20  # 80% utilized


def test_nft_discovery_handles_transfer_out_reentry_and_self_transfer(monkeypatch):
    from settle.extract import hypersync_store
    from settle.extract.transfer_logs import _to_topic_hex
    holder, pm = Address(bytes.fromhex('11'*20)), Address(bytes.fromhex('22'*20))
    topic, other = _to_topic_hex(holder.value), _to_topic_hex(pm.value)
    def row(block, tid, to):
        return SimpleNamespace(block_number=block, log_index=0, topic2=to, topic3=hex(tid))
    logs = [row(1, 10, topic), row(2, 10, other), row(3, 20, topic), row(4, 10, topic), row(5, 20, topic)]
    monkeypatch.setattr(hypersync_store, 'fetch_logs', lambda *a, **kw: [*logs, logs[-1]])
    provider = IncentiveInputs(None, SimpleNamespace(block_resolver=object()))
    source = SimpleNamespace(nft_position_manager=pm, chain=Chain.ETHEREUM)
    assert provider.owned_v4_ids(source, holder, 100) == [10, 20]


def test_morpho_daily_share_return_uses_fee_net_quotes_once(monkeypatch):
    import settle.normalize.incentives as mod
    token, holder = Address(bytes.fromhex('11'*20)), Address(bytes.fromhex('22'*20))
    vault = Address(bytes.fromhex('33'*20))
    provider = IncentiveInputs(SimpleNamespace(venues=[], alm={Chain.ETHEREUM: holder}),
                               SimpleNamespace(block_resolver=object()))
    today = date(2026, 7, 1)
    monkeypatch.setattr(provider, 'block', lambda c, d: 200 if d == today else 100)
    monkeypatch.setattr(mod.rpc, 'balance_of', lambda *a: 10**18)
    calls = []
    def quote(address, signature, block, args):
        calls.append(signature)
        shares = int(args, 16)
        # Net share price rises 0.01% in the day, after fee dilution.
        return [shares * (10001 if block == 200 else 10000) // 10000]
    monkeypatch.setattr(mod, '_call', quote)
    venue = SimpleNamespace(chain=Chain.ETHEREUM, token=SimpleNamespace(address=token, decimals=18),
                            incentive={'components': [{'kind': 'morpho_top_up', 'vault': vault.hex,
                                                       'net_apr_source': 'daily_share_return'}]})
    item = provider.eligible(venue, today)[0]
    assert item.balance == D('1.0001')
    assert item.net_vault_apr == D('0.0365')
    assert all(sig == 'previewRedeem(uint256)' for sig in calls)
    earned, parts = daily_accrual('rlusd', today, [item])
    assert D(parts[0]['apr']) == D('0.0085')
    assert earned == D('1.0001') * D('0.0085') / 365


def test_provider_failure_never_becomes_a_zero_accrual():
    venue = SimpleNamespace(incentive={'program': 'rlusd', 'accrual_start': '2026-01-01',
                                       'opening_balance': '0', 'receipt_sources': [{'payer': 'test'}]})
    def unavailable(*args):
        raise RuntimeError('archive unavailable')
    with pytest.raises(RuntimeError, match='archive unavailable'):
        compute_incentive(None, venue, Period(date(2026, 1, 1), date(2026, 1, 1)), None,
                          provider=SimpleNamespace(eligible=unavailable))


def test_cutoff_uses_published_pin_and_rejects_future_input():
    resolver = SimpleNamespace(block_at_or_before=lambda *a: 201)
    day = date(2026, 1, 1)
    provider = IncentiveInputs(None, SimpleNamespace(block_resolver=resolver),
                               Period(day, day, pin_blocks={Chain.ETHEREUM: 200}))
    assert provider.block(Chain.ETHEREUM, day) == 200
    with pytest.raises(ValueError, match='exceeds pinned cutoff'):
        provider.block(Chain.ETHEREUM, day - timedelta(days=1))


def test_fred_requires_each_calendar_day_and_converts_percent(monkeypatch, tmp_cache_dir):
    import settle.extract.fred as mod
    start = date(2026, 3, 8)
    response = SimpleNamespace(raise_for_status=lambda: None,
                               text='observation_date,DFEDTARL\n2026-03-08,3.50\n2026-03-09,3.50\n')
    monkeypatch.setattr(mod.requests, 'get', lambda *a, **kw: response)
    assert mod.fed_lower(start, start + timedelta(days=1))[start] == D('0.035')
    response.text = 'observation_date,DFEDTARL\n2026-03-08,3.50\n'
    with pytest.raises(ValueError, match='Incomplete DFEDTARL'):
        mod.fed_lower(start, start + timedelta(days=2))
