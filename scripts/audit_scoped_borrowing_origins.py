"""Original borrowing attribution within explicitly selected allocation accounts.

No unrelated repayments refinance these accounts. Internal transfers carry a
weighted-average origin mix; gifts and mark growth never create borrowed basis.
For mixed transactions, other destinations and unaccounted execution losses
consume the draw first. The remainder is a conservative draw contribution.
Results are conditional on normalized movements and this attribution convention.
"""
from decimal import Decimal as D
from decimal import localcontext

ZERO = D(0)
EPS = D('1e-8')


def trace(history, venues, end):
    with localcontext() as ctx:
        ctx.prec = 60
        return _trace(history, venues, end)


def _trace(history, venues, end):
    owned = [{history.venue_accounts[v], *history.custody_accounts.get(v, [])} for v in venues]
    accounts = set().union(*owned)
    if len(accounts) != sum(map(len, owned)):
        raise ValueError('Duplicate origin-group account')
    borrowed = dict.fromkeys(accounts, ZERO)
    withdrawals = {}
    witnesses = []
    seen = set()
    for b in sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index)):
        if b.day > end:
            break
        ms = [m for m in b.movements if m.account in accounts]
        if not ms:
            continue
        if b.identity in seen or len({m.account for m in b.movements}) != len(b.movements):
            raise ValueError('Duplicate scoped movement')
        seen.add(b.identity)
        if sum(b.minted_by_ilk.values(), ZERO) != b.minted:
            raise ValueError('Inconsistent scoped per-ilk draw')
        incoming = []
        out = carried = ZERO
        released = {}
        for m in ms:
            if not all(x.is_finite() for x in (m.value_before, m.change, m.external_income)):
                raise ValueError('Invalid scoped amount')
            if m.value_before < 0 or m.external_income < 0 or m.value_before + m.change < -EPS:
                raise ValueError('Invalid scoped balance')
            if m.value_before == 0 and borrowed[m.account] > EPS:
                raise ValueError('Unexplained reset in scoped funding')
            value = m.value_before + m.external_income
            change = m.change - m.external_income
            if change < 0:
                if value <= 0 or -change > value + EPS:
                    raise ValueError('Scoped withdrawal exceeds source')
                basis = borrowed[m.account] * min(D(1), -change / value)
                borrowed[m.account] -= basis
                recovered = min(basis, -change)
                out -= change
                carried += recovered
                released[m.account] = str(recovered)
            elif change > 0:
                incoming.append((m.account, change))
        received = sum((x for _, x in incoming), ZERO)
        retained = carried * min(D(1), received / out) if out else ZERO
        new_capital = max(ZERO, received - out)
        direct = ZERO
        if (b.minted > 0 and len(b.minted_by_ilk) == 1
                and not b.external_funding and not b.funding_assumption):
            other_destinations = sum((max(ZERO, m.change - m.external_income) for m in b.movements
                                      if m.account not in accounts), ZERO)
            # Do not call a draw/asset shortfall new income, or assume that
            # the missing draw funded this allocation. Charge it first.
            missing_draw = max(ZERO, b.minted - sum(
                (m.change - m.external_income for m in b.movements), ZERO))
            direct = min(new_capital, max(ZERO, b.minted - other_destinations - missing_draw))
        for account, amount in incoming:
            borrowed[account] += (retained + direct) * amount / received
        if released:
            withdrawals[b.identity] = released
        witnesses.append({
            'transaction': b.identity, 'day': str(b.day),
            'new_capital': str(new_capital), 'conservative_draw_contribution': str(direct),
            'draw_by_ilk': {k: str(v) for k, v in b.minted_by_ilk.items()},
            'withdrawn_borrowed_basis': released,
            'borrowed_by_account': {a: str(v) for a, v in sorted(borrowed.items())},
        })
    return {'venues': list(venues), 'scope': __doc__.strip(),
            'withdrawals': withdrawals, 'transactions': witnesses,
            'closing_borrowed_by_account': {a: str(v) for a, v in sorted(borrowed.items())}}
