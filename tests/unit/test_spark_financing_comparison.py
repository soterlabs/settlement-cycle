import copy
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def comparison(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    return importlib.import_module('compare_spark_financing_snapshots')


def snapshot():
    return {'control_sha256': 'same-control', 'drawn_by_ilk': {'ilk': '100'},
            'repaid_by_ilk': {'ilk': '10'},
            'per_ilk_reconciliation': {'by_ilk': {'ilk': {'global_cost': '9',
                'msc_cost': '1', 'global_excluding_msc': '8', 'allocation_cost': '0'}}}}


def test_comparison_allows_progress_without_rewriting_global_cost(comparison):
    old = snapshot()
    new = copy.deepcopy(old)
    new['per_ilk_reconciliation']['by_ilk']['ilk']['allocation_cost'] = '8'
    new['drawn_by_ilk']['ilk'] = '100.0'
    comparison.validate_unchanged_controls(old, new, 'same-control')


@pytest.mark.parametrize('field', ['global_cost', 'msc_cost', 'global_excluding_msc'])
def test_comparison_rejects_changed_global_cost_or_msc_exclusion(comparison, field):
    old = snapshot()
    new = copy.deepcopy(old)
    new['per_ilk_reconciliation']['by_ilk']['ilk'][field] = '0'
    with pytest.raises(ValueError, match='Global borrowing'):
        comparison.validate_unchanged_controls(old, new, 'same-control')


@pytest.mark.parametrize('field', ['drawn_by_ilk', 'repaid_by_ilk'])
def test_comparison_rejects_changed_observed_debt(comparison, field):
    old = snapshot()
    new = copy.deepcopy(old)
    new[field]['ilk'] = '0'
    with pytest.raises(ValueError, match='Observed debt'):
        comparison.validate_unchanged_controls(old, new, 'same-control')


def test_comparison_requires_the_same_published_control_and_ilks(comparison):
    old = snapshot()
    with pytest.raises(ValueError, match='Published control'):
        comparison.validate_unchanged_controls(old, old, 'different-control')
    new = copy.deepcopy(old)
    new['per_ilk_reconciliation']['by_ilk']['other'] = {}
    with pytest.raises(ValueError, match='different ilks'):
        comparison.validate_unchanged_controls(old, new, 'same-control')
