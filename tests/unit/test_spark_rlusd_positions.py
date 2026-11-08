"""Withdrawal cash is not inferred from the sign of a receipt Transfer event."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.syspath_prepend(str(Path('scripts').resolve()))
    spec = importlib.util.spec_from_file_location('rlusd_positions', 'scripts/audit_spark_rlusd_positions.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def event(m, target, topic, values):
    return {'address': target, 'topic0': topic, 'topic1': m.ALM, 'topic2': m.ALM,
            'topic3': m.ALM, 'data': '0x' + ''.join(f'{v:064x}' for v in values)}


def test_withdrawal_emitting_mint_uses_interest_minus_event_value(audit):
    m = audit
    rows = [event(m, m.SPRLUSD, m.MINT_T0, [3, 103, 10**27])]
    evidence = m.match_withdrawal(rows, m.SPRLUSD, 100)
    assert evidence[0]['kind'] == 'Mint_on_withdrawal'
    assert evidence[0]['cash_raw'] == 100


def test_normal_burn_adds_accrued_interest(audit):
    m = audit
    rows = [event(m, m.SPRLUSD, m.BURN_T0, [80, 20, 10**27])]
    assert m.match_withdrawal(rows, m.SPRLUSD, 100)[0]['cash_raw'] == 100


def test_deposit_mint_cannot_explain_cash_withdrawal(audit):
    m = audit
    with pytest.raises(ValueError, match='Unmatched cash'):
        m.match_withdrawal([event(m, m.SPRLUSD, m.MINT_T0, [100, 0, 10**27])], m.SPRLUSD, 100)


def test_vault_withdrawal_must_burn_matching_shares(audit):
    m = audit
    withdrawal = event(m, m.SENTORA, m.WITHDRAW, [100, 90])
    burn = {**event(m, m.SENTORA, m.TRANSFER, [90]), 'topic2': m.ZERO}
    assert m.match_withdrawal([withdrawal, burn], m.SENTORA, 100)[0]['shares_burned_raw'] == 90
    with pytest.raises(ValueError, match='match burns'):
        m.match_withdrawal([withdrawal], m.SENTORA, 100)


def test_different_cash_amount_is_rejected(audit):
    m = audit
    with pytest.raises(ValueError, match='Unmatched cash'):
        m.match_withdrawal([event(m, m.SPRLUSD, m.BURN_T0, [80, 20, 10**27])], m.SPRLUSD, 101)
