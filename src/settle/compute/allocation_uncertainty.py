"""Conservative funding envelopes for read-only allocation diagnostics.

These are outer bounds, not independent scenarios or validated charges. An
unmatched receipt may be a return of already-observed borrowing: relaxing the
possible source and destination never creates another draw. Individual upper
bounds must not be added without the shared outstanding-debt constraint.

Completeness of observed draws/repayments and normalized economic movements is
an assumption. Missing borrowing, bad prices, or erroneous custody matches are
not covered by these bounds. Known income never receives borrowed basis.
"""
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext

ZERO = Decimal(0)
ONE = Decimal(1)


def rounded(operation, upper=False):
    with localcontext() as ctx:
        ctx.prec = 60
        ctx.rounding = ROUND_CEILING if upper else ROUND_FLOOR
        return operation()


@dataclass(frozen=True)
class Band:
    low: Decimal = ZERO
    high: Decimal = ZERO

    def __post_init__(self):
        if not (self.low.is_finite() and self.high.is_finite()
                and ZERO <= self.low <= self.high):
            raise ValueError(f'Invalid funding envelope: {self}')

    def __add__(self, other):
        return Band(rounded(lambda: self.low + other.low),
                    rounded(lambda: self.high + other.high, True))

    def __mul__(self, other):
        return Band(rounded(lambda: self.low * other.low),
                    rounded(lambda: self.high * other.high, True))

    def cap(self, amount):
        return Band(min(self.low, amount), min(self.high, amount))

    def complement(self):
        return Band(rounded(lambda: ONE - self.high), rounded(lambda: ONE - self.low, True))

    def as_dict(self):
        return {'lower': self.low, 'upper': self.high}


def exact(value):
    return Band(value, value)


def total(parts):
    return sum(parts, Band())


def fraction(numerator, denominator):
    """Nonnegative ratio clamped to one, including zero-denominator corners."""
    low = (min(ONE, rounded(lambda: numerator.low / denominator.high))
           if denominator.high else ZERO)
    high = (min(ONE, rounded(lambda: numerator.high / denominator.low, True))
            if denominator.low else (ONE if numerator.high else ZERO))
    return Band(low, high)


@dataclass
class FundingEnvelope:
    accounts: dict = field(default_factory=lambda: defaultdict(dict))
    outstanding: dict = field(default_factory=lambda: defaultdict(Decimal))
    unknown_receipts: dict = field(default_factory=dict)

    def unknown(self, account, amount, identity, *, cash=False):
        """Potential reattribution, not a draw or an income classification.

        Cash carries at most its face value. An unpriced/underwater custody
        receipt can carry more historical basis than its current value, so its
        upper limit is outstanding observed borrowing, never its NAV.
        """
        self.unknown_receipts[identity] = self.unknown_receipts.get(identity, ZERO) + amount
        target = self.accounts[account]
        for ilk, debt in self.outstanding.items():
            possible = min(amount, debt) if cash else debt
            if possible == ZERO:
                continue
            # Its unknown origin could be any already-recorded holding. Each
            # marginal lower bound is relaxed; joint sums remain debt-limited.
            for parts in self.accounts.values():
                old = parts.get(ilk, Band())
                parts[ilk] = Band(max(ZERO, rounded(lambda old=old, possible=possible: old.low - possible)), old.high)
            old = target.get(ilk, Band())
            target[ilk] = Band(old.low, min(debt, rounded(lambda old=old, possible=possible: old.high + possible, True)))

    def apply(self, event, ledger):
        """Called BEFORE the baseline ledger consumes the same economic event."""
        e = event
        if e.kind in ('mark', 'income'):
            return
        if e.kind == 'draw':
            ilk = e.ilk or 'unattributed'
            self.outstanding[ilk] += e.amount
            parts = self.accounts[e.destination]
            parts[ilk] = parts.get(ilk, Band()) + exact(e.amount)
            return
        source = self.accounts[e.source]
        value = e.source_value if e.source_value is not None else ledger.account(e.source).value
        share = fraction(exact(e.amount), exact(value))
        removed = {ilk: part * share for ilk, part in source.items()}
        for ilk in source:
            source[ilk] = source[ilk] * share.complement()
        moved = removed
        if not (e.kind == 'transfer' and e.preserve_basis):
            # Realizing an underwater position cannot transfer more borrowed
            # capital than the cash proceeds. The per-origin haircut is shared.
            haircut = fraction(exact(e.amount), total(removed.values()))
            moved = {ilk: part * haircut for ilk, part in removed.items()}
        if e.kind == 'transfer':
            target = self.accounts[e.destination]
            for ilk, part in moved.items():
                target[ilk] = target.get(ilk, Band()) + part
            return
        ilk = e.ilk or 'unattributed'
        own = moved.get(ilk, Band()).cap(e.amount)
        refinancing = Band(max(ZERO, rounded(lambda: e.amount - own.high)),
                           max(ZERO, rounded(lambda: e.amount - own.low, True)))
        remaining = total(p.get(ilk, Band()) for p in self.accounts.values())
        reduction_share = fraction(refinancing, remaining)
        replacements = {origin: fraction(part, refinancing)
                        for origin, part in moved.items() if origin != ilk}
        for parts in self.accounts.values():
            old = parts.get(ilk, Band())
            retired = old * reduction_share
            parts[ilk] = old * reduction_share.complement()
            for origin, replacement in replacements.items():
                parts[origin] = parts.get(origin, Band()) + retired * replacement
        debt = self.outstanding[ilk] - e.amount
        if debt < -Decimal('0.01'):
            raise ValueError('Funding bounds require complete observed debt draws')
        self.outstanding[ilk] = max(ZERO, debt)

    def constrain(self):
        """Apply shared debt limits; marginal upper bounds remain non-additive."""
        for ilk, debt in self.outstanding.items():
            lower = sum((p.get(ilk, Band()).low for p in self.accounts.values()), ZERO)
            if lower > debt + Decimal('1e-40'):
                raise ValueError('Funding lower bounds exceed outstanding observed borrowing')
            for parts in self.accounts.values():
                old = parts.get(ilk, Band())
                ceiling = max(ZERO, rounded(lambda debt=debt, lower=lower, old=old: debt - lower + old.low, True))
                high = min(old.high, ceiling)
                parts[ilk] = Band(min(old.low, high), high)

    def snapshot(self):
        return {a: {i: b for i, b in parts.items() if b.high}
                for a, parts in self.accounts.items() if any(b.high for b in parts.values())}


def cash_accounts(history):
    """Only recognized par tokens; a vault share is not cash at its NAV."""
    from ..domain.primes import Chain
    from ..domain.sky_tokens import PAR_STABLES_BY_CHAIN, USDS_BY_CHAIN

    tokens = {c.value: {'0x' + a.hex() for a in stables} for c, stables in PAR_STABLES_BY_CHAIN.items()}
    for chain, token in USDS_BY_CHAIN.items():
        tokens.setdefault(chain.value, set()).add(token.address.hex)
    result = set(history.idle_accounts)
    for batch in history.batches:
        for movement in batch.movements:
            components = movement.account.split(':')
            if (len(components) == 3 and components[0] in {c.value for c in Chain}
                    and components[2] in tokens.get(components[0], set())):
                result.add(movement.account)
    return result


def financing_bounds(pnl, history, replay, rates, idle_amounts):
    """Signed utilization/cost envelopes, separate from payable venue charges.

    Exact dollar deductions are retained, including on incomplete basis. A
    negative lower bound is diagnostic uncertainty, never a negative invoice.
    Joint bounds use shared debt caps rather than adding venue upper bounds.
    """
    from datetime import timedelta

    days = sorted(d for d in rates if pnl.period.start <= d <= pnl.period.end)
    venues = {v.venue_id: v for v in pnl.venue_breakdown}
    sde = {v.venue_id: {r['block_date']: r for r in v.daily} for v in pnl.sde_daily_breakdown}
    mapped = {}
    skipped = {}
    ownership = {}
    for vid in sorted(set(venues) | set(history.analytics_only_venues)):
        if vid in history.covered_by_boundary:
            continue
        account = history.venue_accounts.get(vid)
        if account is None or vid in history.unsupported:
            skipped[vid] = history.unsupported.get(vid, 'No capital account mapping')
            continue
        accounts = {account, *history.custody_accounts.get(vid, [])}
        for a in accounts:
            if a in ownership:
                raise ValueError('Funding bounds require disjoint allocation account ownership')
            ownership[a] = vid
        mapped[vid] = accounts
    daily = {}
    rows = {v: {'principal_average_by_ilk': {}, 'cost_by_ilk': {}} for v in mapped}
    accum_principal = {v: {} for v in mapped}
    accum_cost = {v: {} for v in mapped}
    for day in days:
        debt = replay.observed_debt_daily[day]
        state = replay.funding_bounds_daily[day]
        charged_accounts = set()
        deductions_by_ilk = defaultdict(Band)
        deduction_totals = {'sde_av': Band(), 'venue_idle': Band()}
        day_rows = {}
        for vid, accounts in mapped.items():
            v = venues.get(vid)
            exempt = (v is not None and v.cof_excluded) or history.venue_accounts[vid] in history.idle_accounts
            principal = {ilk: total(state.get(a, {}).get(ilk, Band()) for a in accounts).cap(amount)
                         for ilk, amount in debt.items()}
            all_principal = total(principal.values()).cap(sum(debt.values(), ZERO))
            row = sde.get(vid, {}).get(day)
            idle = idle_amounts.get(vid, {}).get(day, ZERO)
            if not idle.is_finite() or idle < ZERO:
                raise ValueError('Invalid idle deduction in funding bounds')
            sde_deduction = (exact(row['cum_value']) if row else
                             all_principal * exact(v.sd_share if v else ZERO))
            deduction = exact(idle) + sde_deduction
            day_rows[vid] = {}
            if not exempt:
                charged_accounts.update(accounts)
                deduction_totals['sde_av'] = deduction_totals['sde_av'] + sde_deduction
                deduction_totals['venue_idle'] = deduction_totals['venue_idle'] + exact(idle)
            for ilk, p in principal.items():
                share = fraction(p, all_principal)
                allocated_deduction = Band() if exempt else deduction * share
                if not exempt:
                    deductions_by_ilk[ilk] = deductions_by_ilk[ilk] + allocated_deduction
                cost = signed_cost(Band() if exempt else p, allocated_deduction, rates[day])
                day_rows[vid][ilk] = {'principal': p.as_dict(), 'cost': cost}
                accum_principal[vid][ilk] = accum_principal[vid].get(ilk, Band()) + p
                previous = accum_cost[vid].setdefault(ilk, {'lower': ZERO, 'upper': ZERO})
                add_signed(previous, cost)
        joint = {}
        for ilk, amount in debt.items():
            principal = total(state.get(a, {}).get(ilk, Band()) for a in charged_accounts).cap(amount)
            deduction = deductions_by_ilk[ilk]
            joint[ilk] = {'principal': principal.as_dict(), 'deduction': deduction.as_dict(),
                          'observed_debt_cap': amount, 'cost': signed_cost(principal, deduction, rates[day])}
        daily[str(day)] = {'allocations': day_rows, 'joint_by_ilk': joint,
                           'deduction_totals': {k: v.as_dict() for k, v in deduction_totals.items()}}
    for vid, values in rows.items():
        values['principal_average_by_ilk'] = {
            ilk: (p * fraction(exact(ONE), exact(Decimal(len(days))))).as_dict()
            for ilk, p in accum_principal[vid].items()}
        values['cost_by_ilk'] = accum_cost[vid]
    period = {}
    for values in daily.values():
        for ilk, row in values['joint_by_ilk'].items():
            add_signed(period.setdefault(ilk, {'lower': ZERO, 'upper': ZERO}), row['cost'])
    # The opening snapshot is retained by replay for callers wanting exposure
    # history; these period costs use the same EOD convention as existing CoF.
    assert pnl.period.start - timedelta(days=1) in replay.funding_bounds_daily
    return {'method': 'conservative_marginal_funding_envelopes_v1',
            'assumptions': ['Observed debt draws and repayments are complete',
                            'Normalized movements, prices and custody matches are correct',
                            'Unknown receipts may reattribute existing debt but never create debt'],
            'interpretation': 'Outer bounds, not validated charges; venue upper bounds are not additive',
            'skipped_allocations': skipped, 'allocations': rows,
            'joint_cost_by_ilk': period, 'daily': daily}


def signed_cost(principal, deduction, rate):
    if rate < ZERO or not rate.is_finite():
        raise ValueError('Funding bounds require nonnegative finite borrowing rates')
    return {'lower': rounded(lambda: (principal.low - deduction.high) * rate),
            'upper': rounded(lambda: (principal.high - deduction.low) * rate, True)}


def add_signed(target, addition):
    target['lower'] = rounded(lambda: target['lower'] + addition['lower'])
    target['upper'] = rounded(lambda: target['upper'] + addition['upper'], True)
