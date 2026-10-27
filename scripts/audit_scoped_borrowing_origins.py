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


def trace(history, venues, end, *, snapshot_accounts=None, bound_external_funding=False, verified_draws=None):
    with localcontext() as ctx:
        ctx.prec = 60
        return _trace(history, venues, end, snapshot_accounts, bound_external_funding, verified_draws)


def _trace(history, venues, end, snapshot_accounts, bound_external_funding, verified_draws):
    owned = [{history.venue_accounts[v], *history.custody_accounts.get(v, [])} for v in venues]
    accounts = set().union(*owned)
    if len(accounts) != sum(map(len, owned)):
        raise ValueError('Duplicate origin-group account')
    snapshots = accounts if snapshot_accounts is None else set(snapshot_accounts)
    if not snapshots <= accounts:
        raise ValueError('Snapshot account outside origin group')
    verified = {} if verified_draws is None else verified_draws
    if verified and (len(accounts) != 1 or any(not v.is_finite() or v < 0 for v in verified.values())):
        raise ValueError('Verified draw contributions require one account and nonnegative finite amounts')
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
        bound_evidence = None
        bounded_external = bound_external_funding and bool(b.external_funding)
        if bounded_external and any(op.kind not in ('draw', 'repay', 'interest')
                                    or not op.amount.is_finite() or op.amount < 0
                                    for op in b.external_funding):
            raise ValueError('Invalid external funding bound input')
        if (b.minted > 0 and len(b.minted_by_ilk) == 1
                and ((not b.external_funding and not b.funding_assumption) or bounded_external)):
            other_destinations = sum((max(ZERO, m.change - m.external_income) for m in b.movements
                                      if m.account not in accounts), ZERO)
            # Do not call a draw/asset shortfall new income, or assume that
            # the missing draw funded this allocation. Charge it first.
            # External draws can mask same-transaction payments in the net
            # asset change. Add them back before bounding unobserved uses.
            # This does not use the provisional principal/interest repayment
            # split: every cash payment/shortfall consumes Sky funding first.
            external_draws = sum((op.amount for op in b.external_funding if op.kind == 'draw'), ZERO)
            missing_draw = max(ZERO, b.minted + external_draws - sum(
                (m.change - m.external_income for m in b.movements), ZERO))
            direct = min(new_capital, max(ZERO, b.minted - other_destinations - missing_draw))
            if bounded_external:
                bound_evidence = {'external_draws': str(external_draws),
                                  'other_destinations': str(other_destinations),
                                  'cash_payments_and_shortfall': str(missing_draw)}
        if b.identity in verified:
            # Receipt validation is performed by the caller. A proven gross
            # draw may fund this position even if a later repayment makes the
            # transaction's net debt negative. A separately replayed custody
            # return can also carry original borrowing without a new draw.
            # Neither proof alters net debt or adds to the baseline twice.
            if new_capital <= 0 or verified[b.identity] > new_capital + EPS:
                raise ValueError('Verified draw exceeds positive position contribution')
            direct = min(new_capital, verified[b.identity])
        for account, amount in incoming:
            borrowed[account] += (retained + direct) * amount / received
        if released:
            withdrawals[b.identity] = released
        witnesses.append({
            'transaction': b.identity, 'day': str(b.day),
            'new_capital': str(new_capital), 'conservative_draw_contribution': str(direct),
            'draw_by_ilk': {k: str(v) for k, v in b.minted_by_ilk.items()},
            'withdrawn_borrowed_basis': released,
            'borrowed_by_account': {a: str(borrowed[a]) for a in sorted(snapshots)},
        })
        if bound_evidence is not None:
            witnesses[-1]['external_cash_bound'] = bound_evidence
        if b.identity in verified:
            witnesses[-1]['receipt_verified_contribution'] = str(direct)
    if set(verified) - seen:
        raise ValueError('Verified draw transaction missing from scoped period')
    result = {'venues': list(venues), 'scope': __doc__.strip(),
            'withdrawals': withdrawals, 'transactions': witnesses,
            'closing_borrowed_by_account': {a: str(v) for a, v in sorted(borrowed.items())}}
    if bound_external_funding:
        result['external_funding_policy'] = (
            'Conservative draw bound: add external draws to net-asset shortfall; '
            'charge all other destinations and cash uses to Sky first. '
            'No external-repayment principal/interest split is used.')
    if snapshot_accounts is not None:
        result['snapshot_accounts'] = sorted(snapshots)
    return result
