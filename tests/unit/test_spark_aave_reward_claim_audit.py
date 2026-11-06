import gzip
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location('aave_claim', ROOT/'scripts/audit_spark_aave_reward_claim.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def evidence():
    return json.loads(gzip.decompress((ROOT/'tests/fixtures/spark_aave_rewards_claim.json.gz').read_bytes()))


def test_real_claim_matches_tokens_and_normalized_unexplained_receipt():
    proof = evidence()
    result = module.audit(proof)
    assert result['amount'] == '243167.543642328131607315'
    assert result['purpose'] == 'Aave Core aUSDS rewards'
    assert proof == evidence()


def test_interest_mint_cannot_replace_actual_reward_delivery():
    proof = evidence()
    proof['receipt']['logs'] = [r for r in proof['receipt']['logs'] if r['logIndex'] != '0x1ee']
    with pytest.raises(ValueError, match='delivered'):
        module.audit(proof)


def test_other_recipient_cannot_authenticate_alm_income():
    proof = evidence()
    row = next(r for r in proof['receipt']['logs'] if r['logIndex'] == '0x1f0')
    row['topics'][3] = '0x'+'00'*32
    with pytest.raises(ValueError, match='beneficiary'):
        module.audit(proof)


def test_mixed_funding_and_changed_normalized_cash_fail():
    proof = evidence()
    proof['batch']['minted'] = '1'
    with pytest.raises(ValueError, match='funding'):
        module.audit(proof)
    proof = evidence()
    proof['batch']['movements'][-1]['change'] = '999'
    with pytest.raises(ValueError, match='normalized'):
        module.audit(proof)
