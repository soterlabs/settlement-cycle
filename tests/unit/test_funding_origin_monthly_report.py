import importlib
from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest


def test_report_presentation_does_not_make_dust_look_like_material_coverage(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    module = importlib.import_module('report_funding_origin_monthly')
    data = {'allocations':[{'prime':'spark','venues':['S34'], 'monthly':[
        module.row(date(2026,1,31), D('1e-40'), D('9e-41')),
        module.row(date(2026,2,28), D(0), D(0)),
        module.row(date(2026,3,31), D(100), D(90)),
    ]}], 'inventory':[]}
    text = module.markdown(data)
    assert '| dust | — | 90.00% |' in text
    assert data['allocations'][0]['monthly'][0]['capital'] == '1E-40'
    with pytest.raises(ValueError, match='exceed'):
        module.row(date(2026,1,31),D(100),D(99),D(2))


def test_savings_is_explained_origin_but_never_shown_as_sky_borrowing(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    module = importlib.import_module('report_funding_origin_monthly')
    row = {'capital': '100', 'borrowed': '98', 'identified_savings': '1.5',
           'unclassified': '0.5', 'borrowed_percent': '98', 'explained_percent': '99.5'}
    text = module.markdown({'allocations': [{'prime': 'spark', 'venues': ['S23'], 'monthly': [row]}],
                            'inventory': []})
    assert '| spark S23 | 98.00% |' in text
    assert '| spark S23 | $98.00 | $1.50 | 99.500% |' in text
    assert 'not labelled Sky borrowing' in text
