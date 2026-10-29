import importlib
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory as H

DAY = date(2026, 1, 1)


@pytest.fixture
def coverage(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    return importlib.import_module('audit_allocation_origin_coverage').coverage


def test_ranking_keeps_external_or_unknown_capital_unclassified(coverage):
    h = H((B('a', DAY, 1, 'ethereum', 1, (M('a', D(0), D(100)),), D(100),
             minted_by_ilk={'ilk': D(100)}),
           B('b', DAY, 2, 'ethereum', 2, (M('b', D(0), D(200)),))), {'A': 'a', 'B': 'b'}, {})
    r = coverage(h, [('A',), ('B',)], DAY, DAY)
    assert r['allocations'][0]['venues'] == ['B']
    assert D(r['selected_closing_capital']) == 300
    assert D(r['selected_traced_borrowing']) == 100
    assert D(r['selected_remaining_origin_to_classify']) == 200


def test_coverage_rejects_double_counting_a_shared_custody_account(coverage):
    h = H((), {'A': 'a', 'B': 'b'}, {}, {'A': ['pending'], 'B': ['pending']})
    with pytest.raises(ValueError, match='overlap'):
        coverage(h, [('A',), ('B',)], DAY, DAY)


def test_surviving_sources_rank_closing_exposure_not_gross_turnover(coverage):
    h = H((B('large-old', DAY, 1, 'ethereum', 1, (M('a', D(0), D(1000)),)),
           B('withdraw', DAY, 2, 'ethereum', 2, (M('a', D(1000), D(-990)),)),
           B('small-new', DAY, 3, 'ethereum', 3, (M('a', D(10), D(20)),))),
          {'A': 'a'}, {})
    row = coverage(h, [('A',)], DAY, DAY)['allocations'][0]
    sources = row['surviving_unclassified_sources']
    assert [s['transaction'] for s in sources] == ['small-new', 'large-old']
    assert [D(s['unclassified_closing_capital']) for s in sources] == [20, 10]
    assert D(row['remaining_origin_to_classify']) == 30
