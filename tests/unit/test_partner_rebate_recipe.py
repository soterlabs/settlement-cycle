"""Independent expected invoice allocations and invalid evidence rejection."""
import copy
import json
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from calculate_october_partner_rebate import FOLDER, calculate, replacements  # noqa: E402


def test_recipe_reproduces_agreed_split_and_saved_output():
    result = calculate()
    assert [(r['fund'], r['year'], r['sky'], r['grove']) for r in result['rows']] == [
        ('JAAA', '2025', '58578.87', '354741.13'),
        ('JAAA', '2026', '117303.63', '455972.37'),
        ('JTRSY', '2025', '356999.00', '0.00'),
        ('JTRSY', '2026', '713408.00', '0.00'),
    ]
    assert result == json.loads((FOLDER / 'partner_rebates.json').read_text())
    assert result['sky']['total'] == '1246289.50'
    assert result['grove']['total'] == '810713.50'


@pytest.mark.parametrize('fault', ['duplicate_receipt', 'receipt_amount', 'missing_day', 'duplicate_day', 'wrong_month'])
def test_invalid_evidence_rejected(fault):
    inputs = json.loads((FOLDER / 'partner-rebate-inputs.json').read_text())
    daily = json.loads((FOLDER / 'partner-rebate-daily.json').read_text())
    if fault == 'duplicate_receipt':
        inputs['receipts'].append(inputs['receipts'][0])
    elif fault == 'receipt_amount':
        inputs['receipts'][0]['amount'] = '1'
    elif fault == 'wrong_month':
        inputs['settlement_month'] = '2026-09'
    elif fault == 'missing_day':
        daily['spark_pretransfer_daily'].pop()
    else:
        daily['spark_pretransfer_daily'].append(daily['spark_pretransfer_daily'][0])
    with pytest.raises(ValueError):
        calculate(inputs, daily)


def test_replace_once_preserves_historical_and_total_mint_adjustment():
    ledger = json.loads((ROOT / 'reconciliation/2026-01_to_2026-09/data.json').read_text())
    before = copy.deepcopy(ledger)
    result = replacements(ledger)
    assert ledger == before
    assert replacements(ledger) == result
    old = ledger['proposed_combined_october_adjustments']['grove']
    new = result['grove']
    assert D(old['sky_adj']) + D(old['sv_adj']) == D(new['sky_adj']) + D(new['sv_adj'])
    assert D(new['sky_adj']) - D(old['sky_adj']) == D('105143.70')
    for prime in result:
        if prime != 'grove':
            assert result[prime] == ledger['proposed_combined_october_adjustments'][prime]
