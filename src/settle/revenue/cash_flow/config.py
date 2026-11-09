"""Reviewed cash perimeter and explicit counterparty classifications."""

import json
from importlib.resources import files

from ..verification import digest


LEGACY_POLICY = "spark-cash-flow.events.v1"
REVIEW_POLICY = "spark-cash-flow.events.v2"


def load(prime="spark", *, review=True, policy=None):
    if prime != "spark":
        raise ValueError("Cash-flow policy is currently defined for Spark")
    if policy is not None:
        if policy not in (LEGACY_POLICY, REVIEW_POLICY):
            raise ValueError("Unsupported cash-flow policy")
        review = policy == REVIEW_POLICY
    result = json.loads(files(__package__).joinpath("spark.json" if review else "spark-legacy.v1.json").read_text())
    for family in ("wallets", "tokens", "counterparties"):
        for values in result[family].values():
            if any(len(a) != 42 or not a.startswith("0x") for a in values):
                raise ValueError("Invalid cash-flow address registry")
    result["revision"] = digest(result)
    return result


def layout(*, review=True):
    return json.loads(files(__package__).joinpath("layout.json" if review else "layout-legacy.v1.json").read_text())


def expenses():
    return json.loads(files(__package__).joinpath("expenses-spark-2026.v1.json").read_text())
