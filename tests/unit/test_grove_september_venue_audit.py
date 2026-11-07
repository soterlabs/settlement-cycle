"""Pinned September evidence validates the two new venues against existing books."""
import importlib.util
import json
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def replay():
    spec = importlib.util.spec_from_file_location(
        'grove_september_audit', ROOT / 'scripts/audit_grove_september_2026.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.audit(json.loads(module.EVIDENCE.read_text()))


def test_existing_booked_venues_are_reproduced(replay):
    # Negative controls: these positions are already in September's report.
    assert D(replay['vaults']['E19']['revenue_existing_method']).quantize(D('.01')) == D('281614.44')
    assert D(replay['vaults']['E23']['revenue_existing_method']).quantize(D('.01')) == D('710966.35')
    assert D(replay['uniswap']['E12']['revenue']).quantize(D('.01')) == D('12817.36')


def test_missing_venues_separate_principal_from_revenue(replay):
    vault = replay['vaults']['E43']
    assert D(vault['cash_deposited_net']) == D('45000000')
    assert D(vault['revenue_existing_method']).quantize(D('.01')) == D('7219.49')
    lp = replay['uniswap']['E44']
    assert lp['token_ids'] == [1352494]
    assert D(lp['capital_net_of_collections']) == D('18978957.122864')
    assert D(lp['revenue']) == D('1002.553047')
