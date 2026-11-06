"""Opt-in principal/interest scenarios, never a published recognition policy.

QUESTIONS.md S33 and config/spark.yaml document these atomic receipts. The
receipt amounts are proved; their splits need Anchorage confirmation. This
module is deliberately NOT called by apply_executed_spells or production.
"""

from dataclasses import replace
from decimal import Decimal as D

from ..normalize.allocation_capital import AssetMovement

ALM = "0x1601843c5e9bc251a3272907010afa41fa18347e"
ESCROW = "0x49506c3aa028693458d6ee816b2ec28522946872"
ACCOUNT = f"facility:ethereum:{ALM}:{ESCROW}"
CASH = f"ethereum:{ALM}:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
RETURNS = (
    (
        "ethereum:0xe74aeeb85c45d4ee617fb2c422f5ed36c4887202e2074194f0926af53cb1a1d6",
        25547403,
        1784231087,
        D("10036438"),
    ),
    (
        "ethereum:0x5b719b6e1f8d7ae0035edbe4d4832efa544a3d79474f86c2fbea5899fc4a67fe",
        25777206,
        1786998671,
        D("51267944"),
    ),
)
SCENARIOS = {
    "10m-50m": (D("10000000"), D("50000000")),
    "10m-51m": (D("10000000"), D("51000000")),
    "all-principal": tuple(r[3] for r in RETURNS),
}
MARKER = ":anchorage-scenario-"


def apply_anchorage_scenario(history, scenario):
    """Release observed facility funding under an explicitly selected split.

    Adjust subsequent opening marks by cumulative principal already returned.
    Mark all affected facility movements as assumed; retain unsupported status.
    Unknown funding remains unknown and global Sky debt is never changed.
    """
    principals = SCENARIOS[scenario]
    if history.venue_accounts.get("S23") != ACCOUNT:
        raise ValueError("Anchorage scenario requires the Spark facility history")
    if any(MARKER in b.identity for b in history.batches):
        raise ValueError("Anchorage scenario already applied")
    events = {r[0]: (*r[1:], p) for r, p in zip(RETURNS, principals, strict=True)}
    if len({b.identity for b in history.batches}) != len(history.batches):
        raise ValueError("Duplicate Anchorage scenario batch")
    if set(events) - {b.identity for b in history.batches}:
        raise ValueError("Both Anchorage return transactions are required")
    assumption = f"Unconfirmed Anchorage principal/interest split: {scenario}; QUESTIONS.md S33"
    released = D(0)
    balance = D(0)
    updated = {}
    for b in sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index)):
        movements = list(b.movements)
        indexes = [i for i, m in enumerate(movements) if m.account == ACCOUNT]
        if len(indexes) > 1:
            raise ValueError("Duplicate Anchorage facility movement")
        changed = False
        if indexes:
            i = indexes[0]
            m = movements[i]
            opening = m.value_before - released
            if opening < 0 or abs(opening - balance) > D("1e-12"):
                raise ValueError("Anchorage facility opening does not follow its cash history")
            if m.external_income or not m.preserve_basis:
                raise ValueError("Unexpected Anchorage facility valuation")
            movements[i] = replace(m, value_before=opening)
            balance = opening + m.change
            changed = released != 0
        if b.identity in events:
            block, timestamp, amount, principal = events[b.identity]
            if (
                (b.chain, b.block, b.timestamp) != ("ethereum", block, timestamp)
                or b.minted
                or indexes
            ):
                raise ValueError("Anchorage return metadata or existing claim changed")
            cash = [i for i, m in enumerate(movements) if m.account == CASH]
            if len(cash) != 1:
                raise ValueError("Anchorage return missing cash")
            i = cash[0]
            if movements[i].change != amount or movements[i].external_income:
                raise ValueError("Anchorage return cash shape changed")
            if balance < principal:
                raise ValueError("Anchorage assumed return exceeds observed facility funding")
            movements[i] = replace(movements[i], external_income=amount - principal)
            movements.append(AssetMovement(ACCOUNT, balance, -principal, preserve_basis=True))
            balance -= principal
            released += principal
            changed = True
        if balance < 0:
            raise ValueError("Anchorage scenario produces negative principal")
        if changed:
            note = "; ".join(filter(None, (b.funding_assumption, assumption)))
            updated[b.identity] = replace(
                b,
                identity=b.identity + MARKER + scenario,
                movements=tuple(movements),
                funding_assumption=note,
            )
    return replace(history, batches=tuple(updated.get(b.identity, b) for b in history.batches))
