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


def test_september_boundary_fee_reduces_mint_once_not_send(tmp_path):
    from settle.compute.sky_total_accrual import compute_sky_total_accrual
    from settle.domain.period import Month

    data = json.loads((_ROOT / 'reconciliation/2026-09/buidl_cash_settlement.json').read_text())
    fee = Decimal(data['september_boundary_cost_unrounded_usds'])
    month = Month(2026, 9)
    folder = tmp_path / 'settlements/grove/2026-09'
    folder.mkdir(parents=True)
    nonmsc = tmp_path / 'settlements/non_msc/2026-09'
    nonmsc.mkdir(parents=True)
    (nonmsc / 'provenance.json').write_text(json.dumps({'results': {'total_income': '0', 'total_expense': '0'}}))
    results = {'sky_revenue': '1000000', 'sde_revenue': '200000',
               'prime_agent_revenue': '1000000', 'agent_rate': '1000',
               'distribution_rewards': '0', 'chronicle_points': '0', 'gar': '0'}
    cfg = {'accrual_primes': ['grove'], 'allocator_ilks': {'grove': 'x'}, 'msc_preview': {}}
    (folder / 'provenance.json').write_text(json.dumps({'results': results}))
    before = compute_sky_total_accrual(month, repo_root=tmp_path, config=cfg).rows[0]
    # Realization is wholly SDE; its reduction to Sky must not change prime CoF.
    results['sky_revenue'] = str(Decimal(results['sky_revenue']) - fee)
    results['sde_revenue'] = str(Decimal(results['sde_revenue']) - fee)
    (folder / 'provenance.json').write_text(json.dumps({'results': results}))
    actual_cfg = yaml.safe_load((_ROOT / 'config/sky_total.yaml').read_text())
    cfg['msc_preview'] = {'2026-09': {'grove': actual_cfg['msc_preview']['2026-09']['grove']}}
    after = compute_sky_total_accrual(month, repo_root=tmp_path, config=cfg).rows[0]
    assert before.derived_send == after.derived_send
    assert (before.derived_mint - after.derived_mint).quantize(Decimal('.01')) == Decimal('177513.75')
    assert after.sky_adj == Decimal('-165013.90')
    assert Decimal(data['historical_jan_aug_credit_usds']) == Decimal('165013.90')
    sde = yaml.safe_load((_ROOT / 'config/sky_direct_exposures.yaml').read_text())
    # Keep PR #218's separate CoF timing correction intact.
    entries = [*sde['active'], *sde['historical']]
    e10 = next(e for e in entries if e.get('prime') == 'grove' and e.get('venue_id') == 'E10')
    assert Decimal(str(e10['in_flight_redemptions'][0]['value_usd'])) == Decimal('24986500.50')
