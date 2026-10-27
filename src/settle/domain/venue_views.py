"""Independent calculation settings for settlement and economic reporting."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, slots=True)
class VenueView:
    enabled: bool = True
    method: str = "existing"
    source_venue: str | None = None
    payer: str | None = None
    loan_terms: dict | None = None
    pending_reason: str | None = None

    def validate(self, kind: str) -> None:
        if type(self.enabled) is not bool:
            raise ValueError(f"{kind}.enabled must be boolean")
        if self.pending_reason is not None and (
            not isinstance(self.pending_reason, str) or not self.pending_reason.strip()
        ):
            raise ValueError("pending_reason must be a non-empty string")
        methods = {"existing", "alm_receipts"} if kind == "settlement" else {"existing", "loan_accrual"}
        if self.method not in methods:
            raise ValueError(f"unsupported {kind}.method: {self.method}")
        if self.method == "alm_receipts":
            if not self.source_venue or not self.payer:
                raise ValueError("alm_receipts requires source_venue and payer")
            from .primes import Address
            Address.from_str(self.payer)
        elif self.source_venue is not None or self.payer is not None:
            raise ValueError("source_venue/payer require alm_receipts")
        if self.method == "loan_accrual":
            if self.loan_terms is None:
                if not self.pending_reason:
                    raise ValueError("loan_accrual requires loan_terms or pending_reason")
            else:
                if self.pending_reason:
                    raise ValueError("confirmed loan_terms cannot have pending_reason")
                validate_loan_terms(self.loan_terms)
        elif self.loan_terms is not None or self.pending_reason is not None:
            raise ValueError("loan_terms/pending_reason require loan_accrual")


def amount(value) -> Decimal:
    if type(value) not in (str, int):
        raise ValueError("financial inputs must be integers or decimal strings")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid financial input") from exc
    if not result.is_finite():
        raise ValueError("non-finite financial input")
    return result


def validate_loan_terms(terms: dict) -> None:
    required = {"opening_date", "opening_unpaid_interest_usd", "confirmed_through",
                "day_count", "source", "tranches"}
    if not isinstance(terms, dict) or set(terms) != required:
        raise ValueError("loan_terms must supply opening balance, coverage, day count, source and tranches")
    def iso_day(value):
        if not isinstance(value, str):
            raise ValueError("loan dates must be ISO YYYY-MM-DD strings")
        parsed = date.fromisoformat(value)
        if str(parsed) != value:
            raise ValueError("loan dates must be ISO YYYY-MM-DD strings")
        return parsed

    opening = iso_day(terms["opening_date"])
    if iso_day(terms["confirmed_through"]) < opening:
        raise ValueError("loan terms coverage ends before opening")
    if (terms["day_count"] not in {"act/365", "act/360"}
            or not isinstance(terms["source"], str) or not terms["source"].strip()):
        raise ValueError("loan terms require a supported day count and documentary source")
    amount(terms["opening_unpaid_interest_usd"])
    if not isinstance(terms["tranches"], list) or not terms["tranches"]:
        raise ValueError("loan terms require tranches")
    ids = set()
    for tranche in terms["tranches"]:
        if (set(tranche) != {"id", "principal_schedule", "apr_schedule"}
                or not isinstance(tranche["id"], str) or not tranche["id"].strip()
                or tranche["id"] in ids):
            raise ValueError("invalid or duplicate loan tranche")
        ids.add(tranche["id"])
        for name, key in (("principal_schedule", "principal_usd"), ("apr_schedule", "apr")):
            schedule = tranche[name]
            if not isinstance(schedule, list) or not schedule:
                raise ValueError(f"missing {name}")
            days = []
            for row in schedule:
                if set(row) != {"date", key} or amount(row[key]) < 0:
                    raise ValueError(f"invalid {name}")
                days.append(iso_day(row["date"]))
            if days != sorted(set(days)):
                raise ValueError(f"{name} must be ordered with unique dates")
        if tranche["apr_schedule"][0]["date"] > max(
            str(opening), tranche["principal_schedule"][0]["date"]
        ):
            raise ValueError("APR must cover the tranche's accrual start")
