"""Borrowed-principal attribution; deliberately independent of settlement debt.

An account holds a market value and a separately conserved borrowed basis.
Withdrawals move the weighted-average borrowed fraction of the position. Yield,
gifts and price changes never create basis. A realised loss releases its missing
basis to the prime's financing adjustment rather than funding another deposit.

Events must cover inception through the requested end, in economic order. The
normalizer owns transaction matching, historical marks and bridge custody.
This module does not infer a loan from an unexplained receipt or an opening NAV.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Literal

ZERO = Decimal("0")
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CapitalEvent:
    event_id: str
    day: date
    order: tuple[int, ...]
    kind: Literal["draw", "repay", "transfer", "income", "mark"]
    amount: Decimal
    source: str | None = None
    destination: str | None = None
    # Required for a transfer/repayment from a yield-bearing account. Cash
    # accounts can use their running balance. Values are at execution, not EoM.
    source_value: Decimal | None = None
    # Moving the same beneficial holding into custody is not a realization.
    preserve_basis: bool = False
    ilk: str | None = None


@dataclass
class CapitalAccount:
    value: Decimal = ZERO
    borrowed: Decimal = ZERO
    borrowed_by_ilk: dict[str, Decimal] = field(default_factory=dict)


@dataclass
class CapitalLedger:
    accounts: dict[str, CapitalAccount] = field(default_factory=dict)
    drawn: Decimal = ZERO
    repaid: Decimal = ZERO
    realised_principal_loss: Decimal = ZERO
    # Repaying with own funds extinguishes debt without creating negative basis.
    equity_funded_repayment: Decimal = ZERO
    drawn_by_ilk: dict[str, Decimal] = field(default_factory=dict)
    repaid_by_ilk: dict[str, Decimal] = field(default_factory=dict)

    def account(self, key: str | None) -> CapitalAccount:
        if key is None:
            raise ValueError("Capital event is missing its account")
        return self.accounts.setdefault(key, CapitalAccount())

    def apply(self, event: CapitalEvent) -> None:
        if not event.amount.is_finite() or event.amount < ZERO:
            raise ValueError(f"Invalid capital amount: {event.event_id}")
        if event.source_value is not None:
            if not event.source_value.is_finite() or event.source_value < ZERO:
                raise ValueError(f"Invalid source mark: {event.event_id}")
        with localcontext() as ctx:
            ctx.prec = 60
            self._apply(event)

    def _apply(self, e: CapitalEvent) -> None:
        if e.kind == "mark":
            self.account(e.destination).value = e.amount
            return
        if e.kind in ("draw", "income"):
            target = self.account(e.destination)
            target.value += e.amount
            if e.kind == "draw":
                target.borrowed += e.amount
                self.drawn += e.amount
                ilk = e.ilk or 'unattributed'
                target.borrowed_by_ilk[ilk] = target.borrowed_by_ilk.get(ilk, ZERO) + e.amount
                self.drawn_by_ilk[ilk] = self.drawn_by_ilk.get(ilk, ZERO) + e.amount
            return
        if e.kind not in ("transfer", "repay"):
            raise ValueError(f"Unknown capital event kind: {e.kind}")
        if e.kind == "transfer" and (e.destination is None or e.source == e.destination):
            raise ValueError(f"Invalid transfer accounts: {e.event_id}")
        source = self.account(e.source)
        value = e.source_value if e.source_value is not None else source.value
        if e.amount > value:
            raise ValueError(f"Capital movement exceeds source value: {e.event_id}; "
                             f"source={e.source}, amount={e.amount}, value={value}")
        if value == ZERO:
            basis = ZERO
        elif e.amount == value:
            basis = source.borrowed  # exact full exit; leave no rounding dust
        else:
            basis = source.borrowed * e.amount / value
        # Carry original funding labels through swaps, bridges and custody.
        # Never allocate origins from the destination's current value or ilk.
        parts = {ilk: amount * basis / source.borrowed
                 for ilk, amount in source.borrowed_by_ilk.items()} if source.borrowed else {}
        if parts:
            last = next(reversed(parts))
            parts[last] += basis - sum(parts.values(), ZERO)
        source.value = value - e.amount
        source.borrowed -= basis
        carried = basis if e.preserve_basis and e.kind == "transfer" else min(basis, e.amount)
        self.realised_principal_loss += basis - carried
        carried_parts = {ilk: amount * carried / basis for ilk, amount in parts.items()} if basis else {}
        if carried_parts:
            last = next(reversed(carried_parts))
            carried_parts[last] += carried - sum(carried_parts.values(), ZERO)
        for ilk, amount in parts.items():
            source.borrowed_by_ilk[ilk] -= amount
        if e.kind == "transfer":
            target = self.account(e.destination)
            target.value += e.amount
            target.borrowed += carried
            for ilk, amount in carried_parts.items():
                target.borrowed_by_ilk[ilk] = target.borrowed_by_ilk.get(ilk, ZERO) + amount
        else:
            self.repaid += e.amount
            ilk = e.ilk or 'unattributed'
            self.repaid_by_ilk[ilk] = self.repaid_by_ilk.get(ilk, ZERO) + e.amount
            self.equity_funded_repayment += e.amount - carried
            replacement = {origin: amount for origin, amount in carried_parts.items()
                           if e.ilk and origin != e.ilk}
            refinancing = e.amount - carried + sum(replacement.values(), ZERO)
            if refinancing == ZERO:
                return  # No refinancing; no global account scan.
            # Apply refinancing proportionally to the repaid ilk's remaining
            # holdings. Earned cash reduces their future borrowed basis;
            # another ilk's cash replaces its origin below. The legacy
            # unlabelled synthetic API retains its aggregate behavior.
            eligible = {k: (a.borrowed_by_ilk.get(e.ilk, ZERO) if e.ilk else a.borrowed)
                        for k, a in self.accounts.items()}
            remaining = sum(eligible.values(), ZERO)
            reduction = min(remaining, refinancing)
            if remaining and reduction:
                keys = sorted(k for k, amount in eligible.items() if amount)
                left = reduction
                for key in keys:
                    a = self.accounts[key]
                    part = left if key == keys[-1] else reduction * eligible[key] / remaining
                    if e.ilk:
                        a.borrowed_by_ilk[e.ilk] -= part
                    else:
                        for origin in a.borrowed_by_ilk:
                            a.borrowed_by_ilk[origin] *= (a.borrowed - part) / a.borrowed
                    a.borrowed -= part
                    # Paying ilk A with cash borrowed from B refinances the
                    # existing A-funded holdings with B; it is not new yield.
                    for origin, amount in replacement.items():
                        new_basis = part * amount / refinancing
                        a.borrowed_by_ilk[origin] = a.borrowed_by_ilk.get(origin, ZERO) + new_basis
                        a.borrowed += new_basis
                    left -= part


def replay_capital(
    events: list[CapitalEvent], start: date, end: date,
) -> tuple[CapitalLedger, dict[date, dict[str, Decimal]]]:
    """End-of-day principal, replayed from inception (never seeded from NAV).

    Repeated log identities are rejected rather than double counted. Stable
    ordering is explicit so two movements on the same day cannot be netted.
    """
    if end < start:
        raise ValueError("Capital period ends before it starts")
    seen: set[str] = set()
    ordered = sorted(events, key=lambda e: (e.day, e.order, e.event_id))
    for e in ordered:
        if e.event_id in seen:
            raise ValueError(f"Duplicate capital event: {e.event_id}")
        seen.add(e.event_id)
    ledger = CapitalLedger()
    daily: dict[date, dict[str, Decimal]] = {}
    cursor = 0
    day = start
    while day <= end:
        while cursor < len(ordered) and ordered[cursor].day <= day:
            ledger.apply(ordered[cursor])
            cursor += 1
        daily[day] = {k: a.borrowed for k, a in ledger.accounts.items()}
        day += timedelta(days=1)
    return ledger, daily


@dataclass
class CapitalReplay:
    ledger: CapitalLedger
    daily: dict[date, dict[str, Decimal]]
    unmatched_receipts: dict[str, Decimal]
    unmatched_outflows: dict[str, Decimal]
    uncertain_accounts: set[str]
    uncertain_daily: dict[date, set[str]] = field(default_factory=dict)
    daily_by_ilk: dict[date, dict[str, dict[str, Decimal]]] = field(default_factory=dict)
    uncertain_repayments: dict[str, list[dict]] = field(default_factory=dict)
    funding_bounds_daily: dict = field(default_factory=dict)
    observed_debt_daily: dict = field(default_factory=dict)


def replay_history(history, start: date, end: date, *, quantify_uncertainty=False) -> CapitalReplay:
    """Pair the asset legs of each transaction, preserving average cost basis.

    An unpaired outflow keeps its basis in a distinct custody account. An
    unpaired receipt has UNKNOWN funding; it never becomes a presumed draw.
    These are reconciliation evidence for the normalizer's custody adapters,
    not proof that the money was earned. Uncertainty propagates on reinvestment.
    """
    from .executed_spell_capital import apply_executed_spells

    history = apply_executed_spells(history)
    if end < start:
        raise ValueError("Capital period ends before it starts")
    if len({b.identity for b in history.batches}) != len(history.batches):
        raise ValueError("Duplicate capital transaction")
    ledger = CapitalLedger()
    envelope = None
    cash_account_keys = set()
    if quantify_uncertainty:
        from .allocation_uncertainty import FundingEnvelope, cash_accounts

        envelope = FundingEnvelope()
        cash_account_keys = cash_accounts(history)
    funding_bounds_daily = {}
    observed_debt_daily = {}
    daily = {}
    unmatched_receipts: dict[str, Decimal] = {}
    unmatched_outflows: dict[str, Decimal] = {}
    uncertain: set[str] = set()
    uncertain_daily = {}
    daily_by_ilk = {}
    uncertain_repayments = {}
    batches = sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index))
    cursor = 0
    _log.info('Capital replay: %d transactions, reporting %s through %s', len(batches), start, end)

    def apply_batch(b):
        clearing = f"clearing:{b.identity}"
        step = 0

        def apply(kind, amount, source=None, destination=None, value=None, preserve_basis=False, ilk=None):
            nonlocal step
            step += 1
            event = CapitalEvent(f"{b.identity}:{step}", b.day, (step,),
                                 kind, amount, source, destination, value, preserve_basis, ilk)
            if envelope is not None:
                envelope.apply(event, ledger)
            ledger.apply(event)

        funding = b.minted_by_ilk or {'unattributed': b.minted}
        if sum(funding.values(), ZERO) != b.minted:
            raise ValueError('Per-ilk funding does not sum to transaction debt change')
        for ilk, amount in funding.items():
            if amount > ZERO:
                apply("draw", amount, destination=clearing, ilk=ilk)
        # Mark before processing gifts; both affect withdrawal fractions.
        for m in b.movements:
            if m.value_before == ZERO and ledger.account(m.account).borrowed == ZERO:
                # A fully exited holding has no old funding uncertainty to
                # transfer to a later, independently funded position.
                uncertain.discard(m.account)
            apply("mark", m.value_before, destination=m.account)
            if m.external_income:
                apply("income", m.external_income, destination=m.account)
        for m in b.movements:
            change = m.change - m.external_income
            if change < ZERO:
                apply("transfer", -change, m.account, clearing, preserve_basis=m.preserve_basis)
                if m.account in uncertain:
                    uncertain.add(clearing)
        for ilk, amount in funding.items():
            if amount >= ZERO:
                continue
            repay = -amount
            cash = ledger.account(clearing).value
            if repay > cash:
                missing = repay - cash
                apply("income", missing, destination=clearing)
                if envelope is not None:
                    envelope.unknown(clearing, missing, b.identity, cash=True)
                unmatched_receipts[b.identity] = unmatched_receipts.get(b.identity, ZERO) + missing
                if missing > Decimal("0.01"):
                    uncertain.add(clearing)
            # Unknown receipts are zero-basis placeholders, not proven earned
            # cash. A repayment from them can retire basis in other holdings.
            # Preserve that uncertainty wherever the repayment changes origin
            # attribution, including refinancing between two ilks.
            before = {k: dict(a.borrowed_by_ilk) for k, a in ledger.accounts.items()
                      if k != clearing and a.borrowed} if clearing in uncertain else {}
            apply("repay", repay, clearing, ilk=None if ilk == 'unattributed' else ilk)
            if clearing in uncertain:
                affected = [k for k, origins in before.items()
                            if origins != ledger.account(k).borrowed_by_ilk]
                uncertain.update(affected)
                uncertain_repayments.setdefault(b.identity, []).append({
                    'ilk': ilk, 'amount': repay, 'affected_account_count': len(affected),
                    # Bound diagnostic output for long histories (Spark has
                    # hundreds of thousands of transaction custody accounts).
                    'affected_accounts_sample': sorted(affected)[:10],
                })
        incoming = [(m.account, m.change - m.external_income) for m in b.movements
                    if m.change > m.external_income]
        total_in = sum((value for _, value in incoming), ZERO)
        available = ledger.account(clearing).value
        matched = min(total_in, available)
        left = matched
        for i, (account, amount) in enumerate(incoming):
            funded = left if i == len(incoming) - 1 else matched * amount / total_in
            left -= funded
            if funded:
                apply("transfer", funded, clearing, account, preserve_basis=True)
                if clearing in uncertain:
                    uncertain.add(account)
            if amount > funded:
                missing = amount - funded
                apply("income", missing, destination=account)
                if envelope is not None:
                    envelope.unknown(account, missing, b.identity, cash=account in cash_account_keys)
                # Pricing/wei dust is retained numerically but is not a
                # missing financing route. This is NOT a yield bounds check.
                if missing > Decimal("0.01"):
                    unmatched_receipts[b.identity] = unmatched_receipts.get(b.identity, ZERO) + missing
                    uncertain.add(account)
        residue = ledger.account(clearing).value
        if residue:
            # Per-transaction evidence already excludes sub-cent dust. Pool
            # that dust, preserving every dollar and its borrowed basis, so
            # repayments do not repeatedly scan hundreds of thousands of
            # economically empty transaction accounts.
            custody = (f"rounding:{b.chain}" if residue <= Decimal("0.01")
                       else f"unallocated:{b.identity}")
            apply("transfer", residue, clearing, custody, preserve_basis=True)
            if clearing in uncertain:
                uncertain.add(custody)
            if residue > Decimal("0.01"):
                unmatched_outflows[b.identity] = residue
        ledger.accounts.pop(clearing, None)
        if envelope is not None:
            envelope.accounts.pop(clearing, None)
            envelope.constrain()
        uncertain.discard(clearing)  # Uncertainty has propagated to destinations.
        # An exhausted claim has no remaining exposure to qualify. Preserve
        # the uncertainty already carried to its proceeds, but do not leave a
        # closed EOA allocation unresolved forever after its final return.
        for m in b.movements:
            a = ledger.account(m.account)
            if a.value == ZERO and a.borrowed == ZERO:
                uncertain.discard(m.account)

    day = start
    with localcontext() as ctx:
        ctx.prec = 60
        while day <= end:
            while cursor < len(batches) and batches[cursor].day <= day:
                apply_batch(batches[cursor])
                cursor += 1
                if cursor % 10000 == 0:
                    _log.info('Capital replay: %d/%d transactions through %s',
                              cursor, len(batches), batches[cursor - 1].day)
            daily[day] = {k: a.borrowed for k, a in ledger.accounts.items()}
            daily_by_ilk[day] = {k: dict(a.borrowed_by_ilk) for k, a in ledger.accounts.items()
                                if a.borrowed}
            uncertain_daily[day] = set(uncertain)
            if envelope is not None:
                funding_bounds_daily[day] = envelope.snapshot()
                observed_debt_daily[day] = dict(envelope.outstanding)
            day += timedelta(days=1)
    _log.info('Capital replay complete: %d transactions; %d unmatched receipts, %d unmatched outflows',
              cursor, len(unmatched_receipts), len(unmatched_outflows))
    return CapitalReplay(ledger, daily, unmatched_receipts, unmatched_outflows, uncertain,
                         uncertain_daily, daily_by_ilk, uncertain_repayments,
                         funding_bounds_daily, observed_debt_daily)
