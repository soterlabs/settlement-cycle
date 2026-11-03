"""Complete principal controls for conditional per-allocation financing.

The ledger already nets draws/repayments within a normalized transaction.
Savings cash replacing Sky debt retires the repaid ilk's remaining principal
proportionally; it does not reduce Savings-only positions or charge gains.
The split is an accepted accounting convention, not proven cash provenance.
"""

from decimal import Decimal, localcontext

ZERO = Decimal(0)
REFINANCING_POLICY = "proportional_remaining_principal_of_repaid_lender"


def funding_bridge(ledger, selected_accounts):
    """Include expense/custody accounts and losses omitted by venue snapshots.

    This is a principal stock bridge, NOT the global borrowing-cost bridge:
    MSC debt, cumulative Vat accrual and utilized deductions remain separate.
    A zero difference proves conservation only, not correctness of attribution.
    """
    with localcontext() as ctx:
        ctx.prec = 60
        sources = {}

        def source(kind, key):
            return sources.setdefault(
                (kind, key),
                dict(
                    selected_principal=ZERO,
                    financing_principal=ZERO,
                    other_principal=ZERO,
                    historical_realised_principal_loss=ZERO,
                    outstanding_principal=ZERO,
                ),
            )

        for key in ledger.external_drawn.keys() | ledger.external_repaid.keys():
            row = source("savings", key)
            row["outstanding_principal"] = ledger.external_drawn.get(
                key, ZERO
            ) - ledger.external_repaid.get(key, ZERO)
            row["historical_realised_principal_loss"] = ledger.external_realised_loss.get(key, ZERO)
        # Sky loss is stored globally rather than by ilk. Keep a single Sky
        # control instead of inventing a per-ilk loss allocation.
        sky = source("sky", "all_ilks")
        sky["outstanding_principal"] = ledger.drawn - ledger.repaid
        sky["historical_realised_principal_loss"] = ledger.realised_principal_loss
        for key, account in ledger.accounts.items():
            bucket = (
                "financing_principal"
                if key.startswith("financing:")
                else "selected_principal"
                if key in selected_accounts
                else "other_principal"
            )
            sky[bucket] += account.borrowed
            for lender, amount in account.external_by_source.items():
                source("savings", lender)[bucket] += amount
        rows = []
        for (kind, key), values in sorted(sources.items()):
            represented = sum(
                (
                    values[k]
                    for k in ("selected_principal", "financing_principal", "other_principal")
                ),
                ZERO,
            )
            residual = values["outstanding_principal"] - represented
            # Realised losses are cumulative and may subsequently be refinanced.
            # They are evidence, not an amount to subtract automatically.
            rows.append(dict(kind=kind, source=key, **values, principal_not_in_accounts=residual))
        return dict(
            policy=REFINANCING_POLICY,
            attribution="accounting_convention",
            scope="principal_stock_not_borrowing_cost",
            sources=rows,
        )


def validate_funding_bridge(bridge, selected):
    """Check a persisted summary against its selected-account snapshot."""
    if (
        bridge.get("policy") != REFINANCING_POLICY
        or bridge.get("attribution") != "accounting_convention"
        or bridge.get("scope") != "principal_stock_not_borrowing_cost"
    ):
        raise ValueError("Invalid funding bridge policy")
    fields = {
        "selected_principal",
        "financing_principal",
        "other_principal",
        "historical_realised_principal_loss",
        "outstanding_principal",
        "principal_not_in_accounts",
    }
    seen = set()
    expected = {("sky", "all_ilks"): ZERO}
    with localcontext() as ctx:
        ctx.prec = 60
        for account in selected.values():
            expected["sky", "all_ilks"] += Decimal(account["sky_principal"])
            for lender, amount in account["savings_by_source"].items():
                key = ("savings", lender)
                expected[key] = expected.get(key, ZERO) + Decimal(amount)
        for row in bridge["sources"]:
            key = row["kind"], row["source"]
            if (
                key in seen
                or key[0] not in ("sky", "savings")
                or (key[0] == "sky" and key[1] != "all_ilks")
            ):
                raise ValueError("Invalid funding bridge source")
            seen.add(key)
            values = {field: Decimal(row[field]) for field in fields}
            if any(not v.is_finite() for v in values.values()) or any(
                value < -Decimal("1e-8")
                for field, value in values.items()
                if field != "principal_not_in_accounts"
            ):
                raise ValueError("Invalid funding bridge amount")
            gap = values["outstanding_principal"] - sum(
                (
                    values[k]
                    for k in ("selected_principal", "financing_principal", "other_principal")
                ),
                ZERO,
            )
            if abs(gap - values["principal_not_in_accounts"]) > Decimal("1e-8") or abs(
                values["selected_principal"] - expected.get(key, ZERO)
            ) > Decimal("1e-8"):
                raise ValueError("Funding bridge differs from saved principal")
        if set(expected) - seen:
            raise ValueError("Incomplete funding bridge sources")
