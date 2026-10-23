from datetime import date
from decimal import Decimal as D

from settle.compute.allocation_capital import CapitalEvent, CapitalLedger, replay_history
from settle.compute.allocation_uncertainty import Band, FundingEnvelope
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

DAY = date(2026, 8, 1)


def test_known_gains_do_not_get_borrowed_bounds_and_full_exit_clears():
    ledger, bounds = CapitalLedger(), FundingEnvelope()
    for n, (kind, amount, source, destination) in enumerate([
        ('draw', '80', None, 'cash'), ('income', '20', None, 'cash'),
        ('transfer', '100', 'cash', 'venue'), ('transfer', '100', 'venue', 'next'),
    ]):
        e = CapitalEvent(str(n), DAY, (n,), kind, D(amount), source, destination, ilk='A')
        bounds.apply(e, ledger)
        ledger.apply(e)
        bounds.constrain()
    assert bounds.accounts['next']['A'] == Band(D(80), D(80))
    assert bounds.accounts['venue']['A'] == Band()
    assert bounds.outstanding['A'] == D(80)


def test_subdollar_unknown_cash_repayment_has_subdollar_effect():
    ledger, bounds = CapitalLedger(), FundingEnvelope()
    def apply(kind, amount, source=None, destination=None):
        e = CapitalEvent(kind, DAY, (), kind, D(amount), source, destination, ilk='A')
        bounds.apply(e, ledger)
        ledger.apply(e)
        bounds.constrain()
    apply('draw', '100000000', destination='venue')
    apply('income', '0.44', destination='cash')
    bounds.unknown('cash', D('0.44'), 'receipt', cash=True)
    apply('repay', '0.44', source='cash')
    band = bounds.accounts['venue']['A']
    assert D('99999999.11') < band.low <= band.high <= D('99999999.56')
    assert band.high - band.low < D('0.45')
    assert bounds.outstanding['A'] == D('99999999.56')


def test_unknown_receipt_does_not_double_shared_debt_and_underwater_basis_not_capped_at_nav():
    bounds = FundingEnvelope()
    bounds.outstanding['A'] = D(100)
    bounds.accounts['custody']['A'] = Band(D(100), D(100))
    bounds.unknown('shares', D(90), 'receipt')
    bounds.constrain()
    assert bounds.accounts['shares']['A'].high == D(100)
    assert bounds.accounts['custody']['A'].low == 0
    assert bounds.outstanding['A'] == D(100)
    # Marginal maxima describe mutually exclusive possibilities.
    assert sum(p['A'].high for p in bounds.accounts.values()) == D(200)


def test_cross_ilk_repayment_bounds_contain_exact_replay():
    ledger, bounds = CapitalLedger(), FundingEnvelope()
    for n, (kind, amount, source, destination, ilk) in enumerate([
        ('draw', '100', None, 'venue', 'A'), ('draw', '40', None, 'cash', 'B'),
        ('repay', '40', 'cash', None, 'A'),
    ]):
        e = CapitalEvent(str(n), DAY, (n,), kind, D(amount), source, destination, ilk=ilk)
        bounds.apply(e, ledger)
        ledger.apply(e)
        bounds.constrain()
    assert bounds.accounts['venue']['A'] == Band(D(60), D(60))
    assert bounds.accounts['venue']['B'] == Band(D(40), D(40))


def test_opt_in_bounds_do_not_change_replay_or_treat_known_income_as_unknown():
    h = CapitalHistory((
        CapitalBatch('draw', DAY, 1, 'ethereum', 1,
                     (AssetMovement('a', D(0), D(100)),), D(100)),
        CapitalBatch('return', DAY, 2, 'ethereum', 2,
                     (AssetMovement('a', D(110), D(-110)), AssetMovement('b', D(0), D(110)))),
    ), {'V': 'b'}, {})
    before = replay_history(h, DAY, DAY)
    after = replay_history(h, DAY, DAY, quantify_uncertainty=True)
    assert before.daily == after.daily
    assert before.ledger == after.ledger
    assert after.funding_bounds_daily[DAY]['b']['unattributed'] == Band(D(100), D(100))


def test_marginal_bounds_contain_alternative_unknown_origins_after_repayment():
    # Enumerate cash that is either earned, or returned from A/B custody. The
    # alternatives share draws; none creates borrowing at the receipt.
    for origin in (None, 'A', 'B'):
        for returned in (D(0), D('0.44')):
            ledger, bounds = CapitalLedger(), FundingEnvelope()
            def apply(kind, amount, source=None, destination=None, ilk='A', bounds=bounds, ledger=ledger):
                e = CapitalEvent(kind, DAY, (), kind, D(amount), source, destination, ilk=ilk)
                bounds.apply(e, ledger)
                ledger.apply(e)
                bounds.constrain()
            apply('draw', '100', destination='a', ilk='A')
            apply('draw', '50', destination='b', ilk='B')
            bounds.unknown('cash', D('0.44'), 'unknown', cash=True)
            if origin is not None:
                ledger.apply(CapitalEvent('actual', DAY, (), 'transfer', returned,
                                          origin.lower(), 'cash'))
            else:
                returned = D(0)
            ledger.apply(CapitalEvent('earned', DAY, (), 'income', D('0.44') - returned,
                                      destination='cash'))
            apply('repay', '0.44', 'cash')
            apply('transfer', '30', 'a', 'new')
            for account, actual in ledger.accounts.items():
                for ilk in ('A', 'B'):
                    band = bounds.accounts[account].get(ilk, Band())
                    assert band.low <= actual.borrowed_by_ilk.get(ilk, D(0)) <= band.high


def test_joint_cost_caps_shared_debt_and_does_not_change_validated_subtotal():
    from types import SimpleNamespace

    from settle.compute.allocation_financing import allocation_financing
    from settle.domain.monthly_pnl import VenueRevenue

    h = CapitalHistory((
        CapitalBatch('draw', DAY, 1, 'ethereum', 1,
                     (AssetMovement('source', D(0), D(100)),), D(100)),
        CapitalBatch('send', DAY, 2, 'ethereum', 2,
                     (AssetMovement('source', D(100), D(-100)),)),
        CapitalBatch('unknown', DAY, 3, 'ethereum', 3,
                     (AssetMovement('receipt', D(0), D(100)),)),
    ), {'V1': 'unallocated:send', 'V2': 'receipt'}, {})
    pnl = SimpleNamespace(period=SimpleNamespace(start=DAY, end=DAY),
        sky_revenue=D(1), sde_revenue=D(0), susds_spread_reimbursement=D(0),
        venue_breakdown=[VenueRevenue(v, v, D(0), D(100), D(0), D(0)) for v in ('V1', 'V2')],
        sky_revenue_daily=[{'date': str(DAY), 'utilized': '100', 'daily_sky_rev': '1', 'base_apr': '3.65'}],
        sde_daily_breakdown=[])
    before = allocation_financing(pnl, h)
    after = allocation_financing(pnl, h, quantify_uncertainty=True)
    bounds = after.pop('funding_uncertainty')
    assert before == after
    assert bounds['joint_cost_by_ilk']['unattributed'] == {'lower': D(0), 'upper': D(1)}
    assert sum(r['cost_by_ilk']['unattributed']['upper'] for r in bounds['allocations'].values()) == D(2)
