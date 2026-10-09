from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from settle.compute.allocation_capital import replay_history
from settle.domain.config import load_prime_by_id
from settle.domain.primes import Chain
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory
from settle.normalize.allocation_eoa import boundary_scope, eoa_boundaries, link_eoa_boundaries
from settle.normalize.allocation_history_cache import load_history, save_history

DAY = date(2026, 8, 1)


def test_boundary_ignores_commingled_wallets_and_roundtrip_gains(tmp_path):
    p = load_prime_by_id('grove')
    scoped, covered = boundary_scope(p)
    v, anchor = eoa_boundaries(scoped)[0]
    assert {'E30', 'E31', 'E32', 'E33', 'E25'} <= covered.keys()
    assert Chain.MONAD not in scoped.alm
    assert not any(x.id in covered for x in scoped.venues)
    holder = p.alm[v.chain].hex
    cash = f'ethereum:{holder}:{v.token.address.hex}'
    returns = f'ethereum:{holder}:{anchor.token.address.hex}'
    def topic(s):
        return '0x' + s[2:].rjust(64, '0')

    def log(n, token, sender, target, amount):
        return LogRow(n, n, n, token, TRANSFER_TOPIC0, topic(sender), topic(target), None,
                      '0x' + f'{int(D(amount)*10**6):064x}', f'0x{n:064x}')

    def batch(n, ms, minted=0):
        return CapitalBatch(f'ethereum:0x{n:064x}', DAY, n, 'ethereum', n, tuple(ms), D(minted),
                            minted_by_ilk={'A': D(minted)} if minted else {})

    # Deposit 80 borrowed + 20 earned, return 40, then 65. The last 5 is
    # realized gain under the existing principal-cap convention. Reinvestment
    # carries only returned borrowed basis; third-party wallet cash is ignored.
    bs = [batch(1, [AssetMovement(cash, D(0), D(100), D(20))], 80),
          batch(2, [AssetMovement(cash, D(100), D(-100))]),
          batch(3, [AssetMovement(returns, D(0), D(40))]),
          batch(4, [AssetMovement(returns, D(40), D(65))]),
          batch(5, [AssetMovement(returns, D(105), D(-105)), AssetMovement('new-vault', D(0), D(105))])]
    logs = [log(2, v.token.address.hex, holder, v.holder_override.hex, 100),
            log(3, anchor.token.address.hex, v.paired_source.hex, holder, 40),
            log(4, anchor.token.address.hex, v.paired_source.hex, holder, 65),
            log(6, v.token.address.hex, '0x' + '11'*20, v.holder_override.hex, 1000000)]
    va, unsupported = {}, {'E36': 'old unsupported'}
    fixed = link_eoa_boundaries(scoped, Chain.ETHEREUM, bs, logs, va, unsupported)
    history = CapitalHistory(tuple(fixed), va, unsupported, analytics_only_venues=('E36',),
                             covered_by_boundary=covered)
    replay = replay_history(history, DAY, DAY)
    assert not replay.unmatched_receipts and not replay.unmatched_outflows
    assert replay.ledger.drawn == 80
    assert replay.ledger.account('new-vault').borrowed == 80
    assert replay.ledger.account('new-vault').value == 105
    assert replay.ledger.account(va['E36']).borrowed == 0
    partial = replay_history(replace(history, batches=tuple(fixed[:3])), DAY, DAY)
    assert partial.ledger.account(va['E36']).borrowed == 48
    assert partial.ledger.account(returns).borrowed == 32
    assert not unsupported
    path = tmp_path / 'history.gz'
    save_history(path, history, 'test')
    assert load_history(path, 'test') == history
    with pytest.raises(ValueError, match='missing from normalized history'):
        link_eoa_boundaries(scoped, Chain.ETHEREUM, bs[:2], logs, {}, {})


def test_boundary_rejects_conflicting_venue_ownership():
    p = load_prime_by_id('grove')
    v, _ = eoa_boundaries(p)[0]
    with pytest.raises(ValueError, match='Overlapping'):
        boundary_scope(replace(p, venues=[*p.venues, replace(v, id='another')]))


def test_boundary_cost_is_counted_once_and_interior_deductions_cannot_disappear():
    from types import SimpleNamespace

    from settle.compute.allocation_financing import allocation_financing
    from settle.domain.monthly_pnl import VenueRevenue

    history = CapitalHistory((CapitalBatch('draw', DAY, 1, 'ethereum', 1,
        (AssetMovement('boundary', D(0), D(100)),), D(100), minted_by_ilk={'A': D(100)}),),
        {'E36': 'boundary'}, {}, analytics_only_venues=('E36',), covered_by_boundary={'E30': 'E36'})
    pnl = SimpleNamespace(period=SimpleNamespace(start=DAY, end=DAY), sky_revenue=D('0.01'),
        sde_revenue=D(0), susds_spread_reimbursement=D(0),
        venue_breakdown=[VenueRevenue('E30', 'Interior', D(100), D(101), D(0), D(1),
                                     actual_revenue=D(1), tw_avg_value=D(100))],
        sky_revenue_daily=[{'date': str(DAY), 'utilized': '100', 'daily_sky_rev': '0.01',
                           'base_apr': '0.0365'}], sde_daily_breakdown=[])
    result = allocation_financing(pnl, history)
    assert result['allocation_cost_of_funds'] == D('0.01')
    assert result['reconciliation']['complete']
    interior, boundary = result['allocations']
    assert interior['basis_status'] == 'covered_by_boundary'
    assert interior['cost_reported_under'] == 'E36'
    assert interior['net_apy'] is None
    assert boundary['cost_of_funds_by_ilk'] == {'A': D('0.01')}
    with pytest.raises(ValueError, match='deductions require'):
        allocation_financing(pnl, history, idle_amounts={'E30': {DAY: D(10)}})
    with pytest.raises(ValueError, match='missing its EOA'):
        allocation_financing(pnl, replace(history, venue_accounts={}))


def test_closed_unknown_claim_clears_only_its_own_uncertainty():
    history = CapitalHistory((
        CapitalBatch('unknown', DAY, 1, 'ethereum', 1, (AssetMovement('claim', D(0), D(100)),)),
        CapitalBatch('return', DAY, 2, 'ethereum', 2,
                     (AssetMovement('claim', D(100), D(-100)), AssetMovement('cash', D(0), D(100))))),
        {'E36': 'claim', 'cash': 'cash'}, {})
    replay = replay_history(history, DAY, DAY)
    assert 'claim' not in replay.uncertain_daily[DAY]
    assert 'cash' in replay.uncertain_daily[DAY]
    assert replay.unmatched_receipts


def test_normalizer_queries_alm_boundary_without_reading_counterparty_balances(monkeypatch):
    from settle.normalize import allocation_capital as source
    from settle.normalize.sources.hypersync_balances import _addr_topic

    p = load_prime_by_id('grove')
    v, anchor = eoa_boundaries(p)[0]
    child = next(x for x in p.venues if x.id == 'E31')
    p = replace(p, venues=[v, anchor, child], alm={Chain.ETHEREUM: p.alm[Chain.ETHEREUM]})
    owner = p.alm[Chain.ETHEREUM]
    rows = [LogRow(1, 1, 1785542400, source._VAT, source._FROB_T0,
                   '0x' + p.ilk_bytes32.hex(), None, None, '0x', '0x' + '01'*32),
            LogRow(1, 2, 1785542400, v.token.address.hex, TRANSFER_TOPIC0,
                   _addr_topic(owner.value), _addr_topic(v.holder_override.value), None,
                   '0x' + f'{100*10**6:064x}', '0x' + '01'*32)]

    def fetch(chain, selections, *args, **kwargs):
        assert _addr_topic(v.holder_override.value) not in str(selections)
        assert _addr_topic(v.paired_source.value) not in str(selections)
        return rows

    monkeypatch.setattr(source.hypersync_store, 'fetch_logs', fetch)
    monkeypatch.setattr(source.rpc, 'ilk_rate', lambda *a: 10**27)
    monkeypatch.setattr(source, '_decode_dart', lambda *a: 100*10**18)
    monkeypatch.setattr(source, 'get_unit_price', lambda *a, **k: D(1))
    # Cash is minted/swapped and sent within this transaction: its net ALM
    # movement is zero. Include the incoming stable leg, as on-chain.
    rows.append(replace(rows[1], log_index=0, topic1=_addr_topic(bytes(20)),
                        topic2=_addr_topic(owner.value)))
    h = source.fetch_capital_history(p, {Chain.ETHEREUM: 1})
    assert h.covered_by_boundary == {'E31': 'E36'}
    assert h.analytics_only_venues == ('E36',)
    replay = replay_history(h, DAY, DAY)
    assert replay.ledger.account(h.venue_accounts['E36']).borrowed == 100
    assert not replay.unmatched_receipts and not replay.unmatched_outflows
