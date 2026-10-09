"""Carry non-Sky funding alongside, but never inside, Sky borrowing costs.

Repaying source A with source B refinances A's remaining investments pro rata.
This is an attribution policy, not a claim that a particular on-chain deposit
was funded by that exact lender. Marks and earned income create no principal.
"""

from decimal import Decimal

ZERO = Decimal(0)


def origins(account):
    result = {("sky", k): v for k, v in account.borrowed_by_ilk.items() if v}
    if not result and account.borrowed:
        result[("sky", "unattributed")] = account.borrowed
    result.update({("external", k): v for k, v in account.external_by_source.items() if v})
    return result


def change(account, origin, amount):
    kind, key = origin
    mapping = account.borrowed_by_ilk if kind == "sky" else account.external_by_source
    mapping[key] = mapping.get(key, ZERO) + amount
    if kind == "sky":
        account.borrowed += amount


def apply_external(ledger, e):
    if e.kind in ("external_draw", "external_repay") and not e.external_source:
        raise ValueError("External funding needs an identified funding source")
    if e.kind == "external_repay":
        outstanding = ledger.external_drawn.get(
            e.external_source, ZERO
        ) - ledger.external_repaid.get(e.external_source, ZERO)
        if e.amount - outstanding > Decimal("1e-30"):
            raise ValueError("External repayment exceeds observed source borrowing")
    if e.kind == "mark":
        ledger.account(e.destination).value = e.amount
        return
    if e.kind in ("draw", "external_draw", "income"):
        account = ledger.account(e.destination)
        account.value += e.amount
        if e.kind == "draw":
            origin = ("sky", e.ilk or "unattributed")
            change(account, origin, e.amount)
            ledger.drawn += e.amount
            ledger.drawn_by_ilk[origin[1]] = ledger.drawn_by_ilk.get(origin[1], ZERO) + e.amount
        elif e.kind == "external_draw":
            change(account, ("external", e.external_source), e.amount)
            ledger.external_drawn[e.external_source] = (
                ledger.external_drawn.get(e.external_source, ZERO) + e.amount
            )
        return
    if e.kind not in ("transfer", "repay", "external_repay"):
        raise ValueError(f"Unknown capital event kind: {e.kind}")
    if e.kind == "transfer" and (e.destination is None or e.source == e.destination):
        raise ValueError(f"Invalid transfer accounts: {e.event_id}")
    source = ledger.account(e.source)
    value = source.value if e.source_value is None else e.source_value
    if e.amount > value:
        raise ValueError(f"Capital movement exceeds source value: {e.event_id}")
    parts = (
        {
            origin: basis if e.amount == value else basis * e.amount / value
            for origin, basis in origins(source).items()
        }
        if value
        else {}
    )
    basis = sum(parts.values(), ZERO)
    carried = basis if e.kind == "transfer" and e.preserve_basis else min(basis, e.amount)
    fraction = carried / basis if basis else ZERO
    carried_parts = {origin: amount * fraction for origin, amount in parts.items()}
    if carried_parts:
        last = next(reversed(carried_parts))
        carried_parts[last] += carried - sum(carried_parts.values(), ZERO)
    source.value = value - e.amount
    for origin, amount in parts.items():
        change(source, origin, -amount)
        lost = amount - carried_parts[origin]
        if origin[0] == "sky":
            ledger.realised_principal_loss += lost
        else:
            key = origin[1]
            ledger.external_realised_loss[key] = ledger.external_realised_loss.get(key, ZERO) + lost
    if e.kind == "transfer":
        target = ledger.account(e.destination)
        target.value += e.amount
        for origin, amount in carried_parts.items():
            change(target, origin, amount)
        return
    if e.kind == "external_repay":
        retiring = ("external", e.external_source)
        ledger.external_repaid[e.external_source] = (
            ledger.external_repaid.get(e.external_source, ZERO) + e.amount
        )
    else:
        retiring = ("sky", e.ilk or "unattributed")
        ledger.repaid += e.amount
        ledger.repaid_by_ilk[retiring[1]] = ledger.repaid_by_ilk.get(retiring[1], ZERO) + e.amount
        ledger.equity_funded_repayment += e.amount - carried
    replacement = {origin: amount for origin, amount in carried_parts.items() if origin != retiring}
    refinancing = e.amount - carried_parts.get(retiring, ZERO)
    if not refinancing:
        return
    eligible = [(key, origins(a).get(retiring, ZERO)) for key, a in ledger.accounts.items()]
    eligible = [(key, amount) for key, amount in eligible if amount]
    remaining = sum((amount for _, amount in eligible), ZERO)
    reduction = min(remaining, refinancing)
    left = reduction
    replaced = {origin: ZERO for origin in replacement}
    for index, (key, amount) in enumerate(eligible):
        part = left if index == len(eligible) - 1 else reduction * amount / remaining
        account = ledger.account(key)
        change(account, retiring, -part)
        for origin, funding in replacement.items():
            new = part * funding / refinancing
            change(account, origin, new)
            replaced[origin] += new
        left -= part
    # Debt may have funded a realized loss or an expense. Replacement cash
    # still has a lender, but cannot fabricate principal in a live investment.
    for origin, funding in replacement.items():
        residue = funding - replaced[origin]
        if residue:
            target = ledger.account("financing:retired-basis:" + retiring[0] + ":" + retiring[1])
            change(target, origin, residue)
