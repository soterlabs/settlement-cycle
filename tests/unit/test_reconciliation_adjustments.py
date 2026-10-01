from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import yaml


_ROOT = Path(__file__).resolve().parents[2]


def test_jan_aug_reconciliation_data_matches_settlement_adjustments():
    data = json.loads(
        (_ROOT / "reconciliation/2026-01_to_2026-08/data.json").read_text()
    )
    config = yaml.safe_load((_ROOT / "config/sky_total.yaml").read_text())
    preview = config["msc_preview"]["2026-09"]

    spark = data["spark"]
    assert Decimal(spark["reserve_factor_revenue_usd"]) == Decimal("2392354.07")
    assert Decimal(str(preview["spark"]["sv_adj"])) == Decimal(
        spark["settlement_adjustment_usd"]
    )

    grove = data["grove"]
    assert (
        Decimal(grove["buidl_realized_fee_credit_usd"])
        + Decimal(grove["august_31_in_flight_cof_credit_usd"])
        == Decimal(grove["total_credit_usd"])
    )
    assert Decimal(str(preview["grove"]["sky_adj"])) == Decimal(
        grove["settlement_adjustment_usd"]
    )
