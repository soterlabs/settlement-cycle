"""Explicit custody costs and signed deduction credits for allocation analytics.

These rows do not change debt, global borrowing cost, or settlement. Shared PSM
deductions stay at pool scope until per-chain leg evidence is supplied; they are
never spread arbitrarily over chains. Credits are not negative venue principal.
"""
from decimal import Decimal as D
from decimal import localcontext

ZERO = D(0)


def _amount(value):
    amount = D(value)
    if not amount.is_finite() or amount < 0:
        raise ValueError('Expected finite nonnegative principal, deduction or rate')
    return amount


def cost_row(identifier, kind, daily, *, scope, basis):
    if not daily or any(not v.is_finite() for v in daily.values()):
        raise ValueError('Cost row requires finite daily values')
    with localcontext() as ctx:
        ctx.prec = 60
        return {'id': identifier, 'kind': kind, 'scope': scope, 'basis': basis,
                'daily_cost': daily, 'cost_of_funds': sum(daily.values(), ZERO)}


def deduction_credit(identifier, deductions, rates, *, scope):
    """A positive deduction produces a negative cost, including zero-funded cases."""
    if set(deductions) != set(rates):
        raise ValueError('Deduction and rate days must match')
    with localcontext() as ctx:
        ctx.prec = 60
        daily = {d: -_amount(deductions[d]) * _amount(rates[d]) for d in rates}
        return cost_row(identifier, 'deduction_credit', daily, scope=scope,
                        basis='existing_global_dollar_deduction_not_funding_principal')


def excess_deduction_credit(identifier, principal, deductions, rates, *, scope):
    """Keep a venue's floored cost and expose only its unapplied deduction."""
    if set(principal) != set(rates) or set(deductions) != set(rates):
        raise ValueError('Principal, deduction and rate days must match')
    with localcontext() as ctx:
        ctx.prec = 60
        excess = {d: max(ZERO, _amount(deductions[d]) - _amount(principal[d])) for d in rates}
        return deduction_credit(identifier, excess, rates, scope=scope)


def psm_cost_rows(daily_by_ilk, rates, *, selected_accounts=()):
    """Price each PSM's borrowed basis, preserving ilk ownership and chain labels.

    Input contains Sky debt basis only, not savings principal or position NAV.
    Deductions are emitted separately because aggregate global controls cannot
    identify which chain owns each deductible leg.
    """
    if not rates or set(rates) - set(daily_by_ilk):
        raise ValueError('Missing daily PSM funding snapshot')
    accounts = sorted({a for d in rates for a in daily_by_ilk[d] if a.startswith('psm:')})
    if set(accounts) & set(selected_accounts):
        raise ValueError('PSM account already included in selected allocation costs')
    with localcontext() as ctx:
        ctx.prec = 60
        rows = []
        for account in accounts:
            parts = account.split(':')
            if len(parts) != 4:
                raise ValueError('Invalid PSM account identity')
            ilks = sorted({ilk for d in rates for ilk in daily_by_ilk[d].get(account, {})})
            for ilk in ilks:
                principal = {d: _amount(daily_by_ilk[d].get(account, {}).get(ilk, ZERO)) for d in rates}
                row = cost_row(f'{account}:{ilk}', 'psm_custody',
                    {d: principal[d] * _amount(rates[d]) for d in rates},
                    scope=account, basis='saved_sky_principal_before_shared_deductions')
                row.update(chain=parts[1], psm_address=parts[2], holder=parts[3], ilk=ilk,
                           daily_sky_principal=principal,
                           average_sky_principal=sum(principal.values(), ZERO)/len(rates),
                           net_cost_after_shared_deductions=None)
                rows.append(row)
        return rows
