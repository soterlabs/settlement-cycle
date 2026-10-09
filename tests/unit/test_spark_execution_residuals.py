import importlib.util
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parents[2] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('execution_residuals', SCRIPTS/'audit_spark_execution_residuals.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
sys.path.pop(0)


def test_partial_witness_keeps_signed_and_absolute_remainders_without_rounding_them_away():
    result = audit.decompose({'ethereum:tx1': '10.0000001', 'ethereum:tx2': '19.9'},
                             {'ethereum:tx1': {'curve': D(-10)}, 'ethereum:tx2': {'ethena': D(-20)}}, {})
    assert D(result['witnessed_net_outflows']) == 30
    assert D(result['remaining_signed_difference']) == D('-.0999999')
    assert D(result['remaining_absolute_difference']) == D('.1000001')
    assert (D(result['observed_outflows']) == D(result['witnessed_net_outflows'])
            + D(result['remaining_signed_difference']))


def test_combined_execution_accounts_for_income_already_in_the_tracer():
    result = audit.decompose({'ethereum:tx': '8'},
                             {'ethereum:tx': {'curve': D(-8), 'par': D(3)}}, {'ethereum:tx': '3'})
    assert D(result['witnessed_net_outflows']) == 8
    assert D(result['remaining_absolute_difference']) == 0


def test_unwitnessed_and_split_routes_remain_unexplained():
    result = audit.decompose({'ethereum:tx:a': '8', 'ethereum:tx:b': '2', 'ethereum:other': '7'},
                             {'ethereum:tx': {'curve': D(-10)}}, {})
    assert D(result['witnessed_net_outflows']) == 0
    assert D(result['remaining_absolute_difference']) == 17
    assert sum(r['ambiguous_split_transaction'] for r in result['rows']) == 2


@pytest.mark.parametrize('bad', ['NaN', '-1', 'Infinity'])
def test_invalid_residuals_fail(bad):
    with pytest.raises(ValueError, match='Invalid observed'):
        audit.decompose({'ethereum:tx': bad}, {}, {})
