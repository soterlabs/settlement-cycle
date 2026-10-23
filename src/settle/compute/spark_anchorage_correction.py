"""Correct a cash-funded Anchorage round trip in preserved tracing snapshots.

July 21, 2026: two payments of 10,000,008.643597 USDC, then a return of
10,000,017.287194 USDC, leave exactly 10m deployed. See QUESTIONS.md S32(b)
and config/spark.yaml's July rollover note. The old tracing normalizer treated
this return as earned income because that day's cash flow was net outbound.
The report's day-net policy already treats it as capital; no reports change.

Complete receipts: tests/fixtures/spark_anchorage_july_correction.json.gz.
"""

from dataclasses import replace
from decimal import Decimal as D

from ..normalize.allocation_capital import AssetMovement

ACCOUNT = "facility:ethereum:0x1601843c5e9bc251a3272907010afa41fa18347e:0x49506c3aa028693458d6ee816b2ec28522946872"
CASH = (
    "ethereum:0x1601843c5e9bc251a3272907010afa41fa18347e:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
)
TX = "ethereum:0x4ad39783662761407cc239326f07ebe8102485838934f03a94d1b59bec512c69"
AMOUNT = D("10000017.287194")
MARKER = ":anchorage-july-correction"


def correct_spark_anchorage_round_trip(history):
    if history.venue_accounts.get("S23") != ACCOUNT:
        return history
    selected = [b for b in history.batches if b.identity in (TX, TX + MARKER)]
    if not selected:
        return history
    if len(selected) != 1:
        raise ValueError("Duplicate Anchorage correction")
    target = selected[0]
    if (target.chain, target.block, target.timestamp) != (
        "ethereum",
        25581853,
        1784645975,
    ) or target.minted:
        raise ValueError("Anchorage correction metadata changed")
    cash = [m for m in target.movements if m.account == CASH]
    claim = [m for m in target.movements if m.account == ACCOUNT]
    if len(cash) != 1 or cash[0].change != AMOUNT:
        raise ValueError("Anchorage correction cash changed")
    if claim:
        if len(claim) != 1 or claim[0].change != -AMOUNT or cash[0].external_income:
            raise ValueError("Anchorage correction partly applied")
        return history  # Fresh extraction or an already-patched snapshot.
    if target.identity != TX or cash[0].external_income != AMOUNT:
        raise ValueError("Unexpected old Anchorage correction shape")
    balance = D(0)
    offset = D(0)
    updated = {}
    for b in sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index)):
        movements = list(b.movements)
        slots = [i for i, m in enumerate(movements) if m.account == ACCOUNT]
        if len(slots) > 1:
            raise ValueError("Duplicate Anchorage claim movement")
        if slots:
            i = slots[0]
            old = movements[i]
            opening = old.value_before - offset
            if abs(opening - balance) > D("1e-12") or opening < 0:
                raise ValueError("Anchorage claim balance inconsistent before correction")
            movements[i] = replace(old, value_before=opening)
            balance = opening + old.change
            if offset:
                updated[b.identity] = replace(b, movements=tuple(movements))
        if b.identity == TX:
            if balance < AMOUNT:
                raise ValueError("Anchorage correction exceeds prior cash funding")
            movements = [
                replace(m, external_income=D(0)) if m.account == CASH else m for m in movements
            ]
            movements.append(AssetMovement(ACCOUNT, balance, -AMOUNT, preserve_basis=True))
            balance -= AMOUNT
            offset = AMOUNT
            updated[b.identity] = replace(b, identity=TX + MARKER, movements=tuple(movements))
    return replace(history, batches=tuple(updated.get(b.identity, b) for b in history.batches))
