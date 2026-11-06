from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace

import pytest

from settle.compute.allocation_capital import replay_history
from settle.domain.primes import Address, Chain
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory
from settle.normalize.allocation_psm import DEPOSIT, WITHDRAW, PsmCapital

HOLDER = Address(bytes.fromhex('11' * 20))
PSM = Address(bytes.fromhex('22' * 20))
OTHER = Address(bytes.fromhex('33' * 20))
TOKEN = Address(bytes.fromhex('44' * 20))
DAY = date(2026, 8, 1)


def topic(a):
    return '0x' + a.value.hex().rjust(64, '0')


def event(kind, amount, shares, sender=HOLDER, receiver=HOLDER):
    return SimpleNamespace(address=PSM.hex, topic0=kind, topic1=topic(TOKEN),
                           topic2=topic(sender), topic3=topic(receiver),
                           data='0x' + f'{amount:064x}{shares:064x}')


def adapter():
    return PsmCapital(SimpleNamespace(psm={Chain.BASE: SimpleNamespace(address=PSM)},
                                      alm={Chain.BASE: HOLDER}), Chain.BASE)


def test_psm_roundtrip_preserves_principal_without_borrowing_the_gain():
    psm = adapter()
    deposit = psm.movements([event(DEPOSIT, 100, 80)], lambda token, n: D(n))
    withdrawal = psm.movements([event(WITHDRAW, 110, 80)], lambda token, n: D(n))
    batches = (
        CapitalBatch('draw', DAY, 1, 'base', 1, (AssetMovement('cash', D(0), D(100)),), D(100)),
        CapitalBatch('deposit', DAY, 2, 'base', 2, (AssetMovement('cash', D(100), D(-100)), *deposit)),
        CapitalBatch('withdraw', DAY, 3, 'base', 3, (*withdrawal, AssetMovement('cash', D(0), D(110)))),
    )
    result = replay_history(CapitalHistory(batches, {}, {}), DAY, DAY)
    assert result.ledger.account('cash').borrowed == 100
    assert result.ledger.account('cash').value == 110
    assert not result.unmatched_receipts
    assert not result.unmatched_outflows
    assert psm.shares == 0


def test_psm_ownership_uses_receiver_on_deposit_and_user_on_withdrawal():
    psm = adapter()
    assert not psm.movements([event(DEPOSIT, 100, 80, receiver=OTHER)], lambda t, n: D(n))
    assert psm.movements([event(DEPOSIT, 100, 80, sender=OTHER)], lambda t, n: D(n))
    result = psm.movements([event(WITHDRAW, 55, 40, receiver=OTHER)], lambda t, n: D(n))
    assert result[0].change == -55
    assert result[0].value_before == 110
    assert psm.shares == 40


def test_psm_rejects_missing_opening_history():
    with pytest.raises(ValueError, match='exceeds reconstructed'):
        adapter().movements([event(WITHDRAW, 10, 10)], lambda t, n: D(n))


def test_normalizer_includes_psm_custody_leg(monkeypatch):
    from settle.domain.primes import Prime, PsmConfig, PsmKind
    from settle.domain.sky_tokens import USDS_BY_CHAIN
    from settle.extract.hypersync import LogRow
    from settle.extract.transfer_logs import TRANSFER_TOPIC0
    from settle.normalize import allocation_capital as source

    token = USDS_BY_CHAIN[Chain.BASE]
    prime = Prime('test', None, DAY, alm={Chain.BASE: HOLDER},
                  psm={Chain.BASE: PsmConfig(PsmKind.ERC4626_SHARES, PSM)})
    def row(block, index, address, sig, t1, t2, t3, words):
        return LogRow(block, index, 1785542400 + block, address.hex, sig, t1, t2, t3,
                      '0x' + ''.join(f'{w:064x}' for w in words), f'0x{block:064x}')
    logs = [row(1, 0, token.address, TRANSFER_TOPIC0, topic(OTHER), topic(HOLDER), None, [100 * 10**18]),
            row(2, 1, token.address, TRANSFER_TOPIC0, topic(HOLDER), topic(PSM), None, [100 * 10**18]),
            row(2, 2, PSM, DEPOSIT, topic(token.address), topic(HOLDER), topic(HOLDER), [100 * 10**18, 80 * 10**18])]
    monkeypatch.setattr(source.hypersync_store, 'fetch_logs', lambda *a, **k: logs)
    monkeypatch.setattr(source, 'get_unit_price', lambda *a, **k: D(1))
    history = source.fetch_capital_history(prime, {Chain.BASE: 2})
    movements = history.batches[-1].movements
    assert sum(m.change for m in movements) == 0
    assert next(m for m in movements if m.account.startswith('psm:')).change == 100


def test_full_psm_exit_uses_exact_cash_not_rounded_share_price():
    psm = adapter()
    shares = 6775611091
    psm.movements([event(DEPOSIT, 7000000000, shares)], lambda t, n: D(n) / D(10**6))
    movement, = psm.movements([event(WITHDRAW, 7924217003, shares)], lambda t, n: D(n) / D(10**6))
    assert movement.value_before == -movement.change == D('7924.217003')
