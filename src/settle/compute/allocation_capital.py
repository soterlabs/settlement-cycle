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

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, localcontext
from typing import Literal

ZERO = Decimal("0")


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


@dataclass
class CapitalAccount:
    value: Decimal = ZERO
    borrowed: Decimal = ZERO


@dataclass
class CapitalLedger:
    accounts: dict[str, CapitalAccount] = field(default_factory=dict)
    drawn: Decimal = ZERO
    repaid: Decimal = ZERO
    realised_principal_loss: Decimal = ZERO
    # Repaying with own funds extinguishes debt without creating negative basis.
    equity_funded_repayment: Decimal = ZERO

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
            return
        if e.kind not in ("transfer", "repay"):
            raise ValueError(f"Unknown capital event kind: {e.kind}")
        if e.kind == "transfer" and (e.destination is None or e.source == e.destination):
            raise ValueError(f"Invalid transfer accounts: {e.event_id}")
        source = self.account(e.source)
        value = e.source_value if e.source_value is not None else source.value
        if e.amount > value:
            raise ValueError(f"Capital movement exceeds source value: {e.event_id}")
        if value == ZERO:
            basis = ZERO
        elif e.amount == value:
            basis = source.borrowed  # exact full exit; leave no rounding dust
        else:
            basis = source.borrowed * e.amount / value
        source.value = value - e.amount
        source.borrowed -= basis
        carried = basis if e.preserve_basis and e.kind == "transfer" else min(basis, e.amount)
        self.realised_principal_loss += basis - carried
        if e.kind == "transfer":
            target = self.account(e.destination)
            target.value += e.amount
            target.borrowed += carried
        else:
            self.repaid += e.amount
            self.equity_funded_repayment += e.amount - carried
            # Own-money repayment refinances a proportional slice of the
            # remaining borrowed holdings. It must reduce their future costs.
            remaining = sum((a.borrowed for a in self.accounts.values()), ZERO)
            reduction = min(remaining, e.amount - carried)
            if remaining:
                keys = sorted(k for k, a in self.accounts.items() if a.borrowed)
                left = reduction
                for key in keys[:-1]:
                    a = self.accounts[key]
                    part = reduction * a.borrowed / remaining
                    a.borrowed -= part
                    left -= part
                self.accounts[keys[-1]].borrowed -= left


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


def replay_history(history, start: date, end: date) -> CapitalReplay:
    """Pair the asset legs of each transaction, preserving average cost basis.

    An unpaired outflow keeps its basis in a distinct custody account. An
    unpaired receipt has UNKNOWN funding; it never becomes a presumed draw.
    These are reconciliation evidence for the normalizer's custody adapters,
    not proof that the money was earned. Uncertainty propagates on reinvestment.
    """
    if end < start:
        raise ValueError("Capital period ends before it starts")
    if len({b.identity for b in history.batches}) != len(history.batches):
        raise ValueError("Duplicate capital transaction")
    ledger = CapitalLedger()
    daily = {}
    unmatched_receipts: dict[str, Decimal] = {}
    unmatched_outflows: dict[str, Decimal] = {}
    uncertain: set[str] = set()
    uncertain_daily = {}
    batches = sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index))
    cursor = 0

    def apply_batch(b):
        clearing = f"clearing:{b.identity}"
        step = 0

        def apply(kind, amount, source=None, destination=None, value=None, preserve_basis=False):
            nonlocal step
            step += 1
            ledger.apply(CapitalEvent(f"{b.identity}:{step}", b.day, (step,),
                                     kind, amount, source, destination, value, preserve_basis))

        if b.minted > ZERO:
            apply("draw", b.minted, destination=clearing)
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
        if b.minted < ZERO:
            repay = -b.minted
            cash = ledger.account(clearing).value
            if repay > cash:
                missing = repay - cash
                apply("income", missing, destination=clearing)
                unmatched_receipts[b.identity] = missing
            apply("repay", repay, clearing)
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
                # Pricing/wei dust is retained numerically but is not a
                # missing financing route. This is NOT a yield bounds check.
                if missing > Decimal("0.01"):
                    unmatched_receipts[b.identity] = unmatched_receipts.get(b.identity, ZERO) + missing
                    uncertain.add(account)
        residue = ledger.account(clearing).value
        if residue:
            custody = f"unallocated:{b.identity}"
            apply("transfer", residue, clearing, custody, preserve_basis=True)
            if residue > Decimal("0.01"):
                unmatched_outflows[b.identity] = residue
        ledger.accounts.pop(clearing, None)

    day = start
    with localcontext() as ctx:
        ctx.prec = 60
        while day <= end:
            while cursor < len(batches) and batches[cursor].day <= day:
                apply_batch(batches[cursor])
                cursor += 1
            daily[day] = {k: a.borrowed for k, a in ledger.accounts.items()}
            uncertain_daily[day] = set(uncertain)
            day += timedelta(days=1)
    return CapitalReplay(ledger, daily, unmatched_receipts, unmatched_outflows, uncertain, uncertain_daily)
