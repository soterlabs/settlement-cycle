"""Reporting-only issuer income, ACT/365 on UTC end-of-day eligible balances."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

ZERO = Decimal(0)
YEAR = Decimal(365)


def apy_to_apr(apy: Decimal) -> Decimal:
    if not apy.is_finite() or apy <= -1:
        raise ValueError('APY must be finite and greater than -1')
    return YEAR * ((1 + apy) ** (1 / YEAR) - 1)


def issuer_apr(kind: str, day: date, fed_lower: Decimal | None = None) -> Decimal:
    if kind == 'rlusd':
        return Decimal('0.045')
    if kind == 'pyusd':
        if fed_lower is None or not fed_lower.is_finite():
            raise ValueError(f'Missing DFEDTARL observation for {day}')
        spread = Decimal('0.02575') if day < date(2026, 3, 9) else Decimal('0.018')
        return apy_to_apr(fed_lower - Decimal('0.00625') + spread)
    raise ValueError(f'Unknown incentive program {kind}')


@dataclass(frozen=True)
class EligibleBalance:
    component: str
    balance: Decimal
    net_vault_apr: Decimal | None = None


def daily_accrual(kind: str, day: date, components: list[EligibleBalance],
                  fed_lower: Decimal | None = None) -> tuple[Decimal, list[dict]]:
    rate = issuer_apr(kind, day, fed_lower)
    total, rows = ZERO, []
    for item in components:
        if not item.balance.is_finite() or item.balance < 0:
            raise ValueError(f'Invalid eligible balance: {item.component}')
        applied = rate
        if item.net_vault_apr is not None:
            if kind != 'rlusd' or not item.net_vault_apr.is_finite():
                raise ValueError('Invalid vault top-up rate')
            applied = max(rate - item.net_vault_apr, ZERO)
        earned = item.balance * applied / YEAR
        total += earned
        rows.append({'component': item.component, 'balance': str(item.balance),
                     'net_vault_apr': str(item.net_vault_apr) if item.net_vault_apr is not None else None,
                     'apr': str(applied), 'accrual': str(earned)})
    return total, rows


def reconcile(kind: str, start: date, period_start: date, cutoff: date,
              daily: dict[date, tuple[Decimal, list[dict]]], receipts: dict[date, Decimal],
              opening_balance: Decimal) -> dict:
    """Full history from start; negative accrued balances are legitimate prepayments.

    USDG is receipt-basis by agreement: its 8-hour payments are the accrual,
    therefore its receivable is zero (apart from any explicit opening balance).
    """
    if start > period_start or period_start > cutoff or not opening_balance.is_finite():
        raise ValueError('Invalid incentive reconciliation window/opening balance')
    cumulative_earned = cumulative_paid = period_earned = period_paid = ZERO
    opening = opening_balance
    rows = []
    day = start
    while day <= cutoff:
        paid = receipts.get(day, ZERO)
        if not paid.is_finite() or paid < 0:
            raise ValueError(f'Invalid receipt on {day}')
        if kind == 'usdg':
            earned, components = paid, []
        else:
            if day not in daily:
                raise ValueError(f'Missing daily accrual for {day}')
            earned, components = daily[day]
            if not earned.is_finite():
                raise ValueError(f'Invalid accrual on {day}')
        cumulative_earned += earned
        cumulative_paid += paid
        balance = opening_balance + cumulative_earned - cumulative_paid
        if day < period_start:
            opening = balance
        else:
            period_earned += earned
            period_paid += paid
            rows.append({'date': str(day), 'accrual': str(earned), 'receipts': str(paid),
                         'accrued_balance': str(balance), 'components': components})
        day += timedelta(days=1)
    return {'program': kind, 'basis': 'receipts (8-hour payments)' if kind == 'usdg' else 'ACT/365, UTC EOD',
            'accrual_start': str(start), 'opening_balance': str(opening_balance),
            'period_opening_balance': str(opening), 'period_accrual': str(period_earned),
            'period_receipts': str(period_paid), 'cumulative_accrual': str(cumulative_earned),
            'cumulative_receipts': str(cumulative_paid),
            'accrued_balance': str(opening_balance + cumulative_earned - cumulative_paid),
            'daily': rows}
