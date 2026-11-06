import gzip
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SPEC = importlib.util.spec_from_file_location('v4_fee_witness', ROOT/'scripts/audit_spark_v4_fee_witness.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def evidence():
    return json.loads(gzip.decompress((ROOT/'tests/fixtures/spark_v4_fee_witness.json.gz').read_bytes()))


def test_real_v4_fees_and_returned_principal_are_independent_of_residual():
    proof = evidence()
    result = module.audit(proof)
    assert result['fees_at_par'] == '12667.024366075564808842'
    assert result['returned_principal_at_par'] == '1215.925898327927459078'
    assert result['remaining_signed_receipt'] == '0.00905259889230006504868'
    assert proof == evidence()
    # Changing the reported discrepancy cannot manufacture a fee explanation.
    proof['item']['residual']['amount'] = '20000'
    assert module.audit(proof)['fees_at_par'] == result['fees_at_par']


def test_second_modification_in_same_block_cannot_use_block_checkpoint_shortcut():
    proof = evidence()
    proof['block_modify_events'].append(dict(proof['block_modify_events'][0]))
    with pytest.raises(ValueError, match='Multiple'):
        module.audit(proof)


def test_wrong_storage_key_or_liquidity_checkpoint_is_rejected():
    proof = evidence()
    proof['reads'][0]['data'] = '0x1e2eaeaf'+'00'*32
    with pytest.raises(ValueError, match='storage'):
        module.audit(proof)
    proof = evidence()
    proof['reads'][0]['result'] = '0x0'
    with pytest.raises(ValueError, match='Liquidity checkpoint'):
        module.audit(proof)


def test_fees_must_have_actual_cash_delivery():
    proof = evidence()
    proof['item']['receipt']['logs'] = [r for r in proof['item']['receipt']['logs']
                                        if r['logIndex'] != '0x192']
    with pytest.raises(ValueError, match='delivered cash'):
        module.audit(proof)


def test_principal_witness_must_match_normalized_nft_leg():
    proof = evidence()
    proof['item']['batch']['movements'][0]['change'] = '-1'
    with pytest.raises(ValueError, match='normalized NFT'):
        module.audit(proof)
