"""Basin cash: historical ownership, compartment cap, and rate application."""
from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from unittest.mock import Mock

import pandas as pd
import pytest

from settle.compute.sky_revenue import compute_sky_revenue_daily
from settle.domain.config import load_prime_by_id
from settle.domain.period import Period
from settle.domain.primes import Address, Chain
from settle.domain.subsidy import ReferenceRateHistory, SubsidyConfig
from settle.extract import basin as rpc
from settle.normalize.basin import get_basin_idle_usds


def period(start=date(2026, 9, 1), end=date(2026, 9, 2)):
    return Period(start, end, {Chain.ETHEREUM: 200})


def snapshot(cash, shares=1, total=1, holder='pocket'):
    return {'balances': {holder: int(D(cash) * 10**18)}, 'shares': shares,
            'total_shares': total, 'pocket': holder}


def test_config_scope():
    cfg = load_prime_by_id('grove').basin_idle_usds
    assert cfg.effective_from == date(2026, 9, 1)
    assert cfg.ilk.rstrip(b'\0') == b'ALLOCATOR-GROVE-A'
    for name in ['spark', 'obex', 'osero', 'keel', 'skybase']:
        assert load_prime_by_id(name).basin_idle_usds is None
    with pytest.raises(ValueError, match='belong'):
        replace(load_prime_by_id('spark'), basin_idle_usds=cfg)
    with pytest.raises(ValueError, match='unique'):
        replace(cfg, basins=(cfg.basins[0], cfg.basins[0]))


@pytest.mark.parametrize('prime_id,end', [('grove', date(2026, 8, 31)),
                                          ('spark', date(2026, 9, 30))])
def test_inactive_scope_has_no_reads(prime_id, end):
    resolver = Mock()
    assert get_basin_idle_usds(load_prime_by_id(prime_id),
                              period(end, end), block_resolver=resolver).empty
    resolver.block_at_or_before.assert_not_called()


def test_ownership_and_single_cap_at_effective_boundary(monkeypatch):
    prime = load_prime_by_id('grove')
    cfg = prime.basin_idle_usds
    resolver = Mock()
    resolver.block_at_or_before.side_effect = [101, 102]
    debt_read = Mock(side_effect=[D(80), D(300)])
    monkeypatch.setattr(rpc, 'ilk_debt', debt_read)
    def cash(basin, holder, block):
        assert holder == cfg.holder
        # Day 1: 50 + 100 = 150, capped ONCE at 80 for Grove-A.
        # Day 2: changed ownership and cash = 20 + 100 = 120.
        if basin == cfg.basins[0]:
            return snapshot(200 if block == 101 else 80, 1, 4, 'p1')
        return snapshot(100, holder='p2')
    monkeypatch.setattr(rpc, 'idle_usds', cash)
    result = get_basin_idle_usds(prime, period(date(2026, 8, 31)), block_resolver=resolver)
    assert result.cum_balance.tolist() == [D(0), D(80), D(120)]
    assert result.ilk_debt.tolist() == [D(0), D(80), D(300)]
    assert all(call.args[0] == cfg.ilk for call in debt_read.call_args_list)
    assert [c.args[1].date() for c in resolver.block_at_or_before.call_args_list] == [
        date(2026, 9, 1), date(2026, 9, 2)]


def test_failed_historical_read_does_not_carry_cash(monkeypatch):
    monkeypatch.setattr(rpc, 'ilk_debt', lambda *a: D(1000))
    reader = Mock(side_effect=[snapshot(10, holder='a'), snapshot(10, holder='b'),
                               rpc.RPCError('unavailable')])
    monkeypatch.setattr(rpc, 'idle_usds', reader)
    with pytest.raises(rpc.RPCError):
        get_basin_idle_usds(load_prime_by_id('grove'), period(), block_resolver=Mock())


def test_shared_pocket_is_not_double_counted(monkeypatch):
    monkeypatch.setattr(rpc, 'ilk_debt', lambda *a: D(1000))
    monkeypatch.setattr(rpc, 'idle_usds', lambda *a: snapshot(100))
    with pytest.raises(ValueError, match='overlap'):
        get_basin_idle_usds(load_prime_by_id('grove'), period(), block_resolver=Mock())


def test_historical_pocket_switch_and_self_pocket(monkeypatch):
    basin = Address(bytes.fromhex('11' * 20))
    other = Address(bytes.fromhex('22' * 20))
    holder = Address(bytes.fromhex('33' * 20))
    seen = []
    def read(contract, sig, block, argument=b''):
        seen.append((sig, block, argument))
        if sig == 'swapToken()':
            return int.from_bytes(rpc.USDS.value, 'big')
        if sig == 'pocket()':
            return int.from_bytes((basin if block == 1 else other).value, 'big')
        if sig == 'shares(address)':
            return 25
        if sig == 'totalShares()':
            return 100
        if sig == 'balanceOf(address)':
            return 10**18
        raise AssertionError(sig)
    monkeypatch.setattr(rpc, '_word', read)
    first = rpc.idle_usds.__wrapped__(basin, holder, 1)
    second = rpc.idle_usds.__wrapped__(basin, holder, 2)
    assert first['balances'] == {basin.hex: 10**18}
    assert second['balances'] == {basin.hex: 10**18, other.hex: 10**18}
    assert second['shares'] == 25 and second['total_shares'] == 100
    assert sum(sig == 'balanceOf(address)' for sig, _, _ in seen) == 3


@pytest.mark.parametrize('raw', ['0x', '0x00', '0x' + '00' * 64])
def test_empty_or_malformed_contract_response_is_error(monkeypatch, raw):
    monkeypatch.setattr(rpc, 'eth_call', lambda *a: raw)
    with pytest.raises(rpc.RPCError):
        rpc._word(rpc.USDS, 'balanceOf(address)', 1, bytes(32))
    with pytest.raises(rpc.RPCError):
        rpc.ilk_debt.__wrapped__(bytes(32), 1)


def test_debt_uses_own_rate(monkeypatch):
    words = [100 * 10**18, 2 * 10**27, 0, 0, 0]
    monkeypatch.setattr(rpc, 'eth_call', lambda *a: '0x' + ''.join(f'{w:064x}' for w in words))
    assert rpc.ilk_debt.__wrapped__(bytes(32), 1) == D(200)


def test_deduction_crosses_one_subsidy_cap_without_changing_gross():
    p = period(date(2026, 9, 1), date(2026, 9, 1))
    def series(key, value):
        return pd.DataFrame([{'block_date': p.start, key: D(value)}])
    debt = series('cum_debt', '1100000000')
    alm = series('cum_balance', '0')
    ssr = pd.DataFrame([{'effective_date': p.start, 'ssr_apy': D('.04')}])
    history = ReferenceRateHistory(pd.DataFrame([
        {'effective_date': p.start, 'ref_rate_apr': D('.02')}]), 'sofr')
    kwargs = dict(subsidy_config=SubsidyConfig(enabled=True, ref_rate_kind='sofr'),
                  ref_rate_history=history)
    before, bd, _ = compute_sky_revenue_daily(p, debt, alm, ssr, **kwargs)
    idle = series('cum_balance', '200000000')
    idle['ilk_debt'] = D('250000000')
    after, ad, _ = compute_sky_revenue_daily(p, debt, alm, ssr, basin_idle_usds=idle, **kwargs)
    expected = D('100000000') * (D(str(bd.iloc[0].base_apr)) + D(str(bd.iloc[0].sub_apr))) / 365
    assert abs(before - after - expected) < D('0.000001')
    assert ad.iloc[0].utilized == D('900000000')
    assert ad.iloc[0].daily_sky_rev_gross == bd.iloc[0].daily_sky_rev_gross
    idle['ilk_debt'] = D('100000000')
    with pytest.raises(ValueError, match='exceeds'):
        compute_sky_revenue_daily(p, debt, alm, ssr, basin_idle_usds=idle, **kwargs)
