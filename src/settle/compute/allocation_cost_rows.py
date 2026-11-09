"""Signed deduction credits and non-allocation custody inventory.

PSM is not an allocation: retain its principal as a reconciliation control,
without pricing it or assigning borrowing cost or yield. Global settlement
treatment remains unchanged. Credits are not negative venue principal.
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


def psm_principal_inventory(daily_by_ilk, days, *, selected_accounts=()):
    """Inventory PSM custody without assigning allocation borrowing costs."""
    days = set(days)
    if not days or days - set(daily_by_ilk):
        raise ValueError('Missing daily PSM funding snapshot')
    accounts = sorted({a for d in days for a in daily_by_ilk[d] if a.startswith('psm:')})
    if set(accounts) & set(selected_accounts):
        raise ValueError('PSM account cannot be included in selected allocation costs')
    with localcontext() as ctx:
        ctx.prec = 60
        rows = []
        for account in accounts:
            parts = account.split(':')
            if len(parts) != 4:
                raise ValueError('Invalid PSM account identity')
            ilks = sorted({ilk for d in days for ilk in daily_by_ilk[d].get(account, {})})
            for ilk in ilks:
                principal = {d: _amount(daily_by_ilk[d].get(account, {}).get(ilk, ZERO)) for d in sorted(days)}
                rows.append({'account': account, 'chain': parts[1], 'psm_address': parts[2],
                    'holder': parts[3], 'ilk': ilk, 'is_allocation': False,
                    'daily_sky_principal': principal,
                    'average_sky_principal': sum(principal.values(), ZERO)/len(days)})
        return rows
