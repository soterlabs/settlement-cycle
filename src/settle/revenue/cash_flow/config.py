"""Reviewed cash perimeter and explicit counterparty classifications."""

import json
from importlib.resources import files

from ..verification import digest


def load(prime="spark"):
    if prime != "spark":
        raise ValueError("Cash-flow policy is currently defined for Spark")
    result = json.loads(files(__package__).joinpath("spark.json").read_text())
    for family in ("wallets", "tokens", "counterparties"):
        for values in result[family].values():
            if any(len(a) != 42 or not a.startswith("0x") for a in values):
                raise ValueError("Invalid cash-flow address registry")
    result["revision"] = digest(result)
    return result


def layout():
    return json.loads(files(__package__).joinpath("layout.json").read_text())


def expenses():
    return json.loads(files(__package__).joinpath("expenses-spark-2026.v1.json").read_text())
