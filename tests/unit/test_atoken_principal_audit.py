"""Independent event-principal audit arithmetic, including interest-only mints."""
import importlib.util
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('atoken_audit', Path(__file__).parents[2]/'scripts/audit_atoken_event_principal.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
aave = audit.aave


def event(topic, values, sender='other', receiver='holder'):
    return SimpleNamespace(topic0=topic, topic1=sender, topic2=receiver,
                           data='0x'+''.join(f'{v:064x}' for v in values))


def test_mint_and_burn_remove_already_accrued_interest():
    assert audit.principal_raw(event(aave.MINT_T0, [110,10,aave.RAY]), 'holder') == 100
    # Withdrawal smaller than accrued interest emits Mint, not Burn.
    assert audit.principal_raw(event(aave.MINT_T0, [5,10,aave.RAY]), 'holder') == -5
    assert audit.principal_raw(event(aave.MINT_T0, [10,10,aave.RAY]), 'holder') == 0
    assert audit.principal_raw(event(aave.BURN_T0, [90,10,aave.RAY]), 'holder') == -100


def test_direct_transfer_uses_index_and_direction_and_self_transfer_nets_zero():
    row=event(aave.BT_T0, [50,aave.RAY*11//10])
    assert audit.principal_raw(row,'holder') == Decimal(55)
    assert audit.principal_raw(row,'other') == Decimal(-55)
    row=event(aave.BT_T0, [50,aave.RAY], 'holder','holder')
    assert audit.principal_raw(row,'holder') == 0
