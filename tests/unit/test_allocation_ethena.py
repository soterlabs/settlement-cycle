from datetime import date
from decimal import Decimal as D

import pytest

from settle.compute.allocation_capital import replay_history
from settle.domain.pricing import PricingCategory
from settle.domain.primes import Address, Chain, Prime, Token, Venue
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize import allocation_ethena as ethena
from settle.normalize.allocation_capital import AssetMovement as M
from settle.normalize.allocation_capital import CapitalBatch as B
from settle.normalize.allocation_capital import CapitalHistory as H

DAY = date(2026, 8, 1)
HOLDER = '0x' + '11' * 20
SILO = '0x' + '22' * 20


def topic(a):
    return '0x' + a[2:].rjust(64, '0')


def test_multiple_cooldowns_accumulate_and_release_original_borrowing(monkeypatch):
    holder = Address.from_str(HOLDER)
    venue = Venue('S', Chain.ETHEREUM, Token(Chain.ETHEREUM, Address.from_str(ethena.SUSDE), 'sUSDe', 18),
                  PricingCategory.ERC4626_VAULT)
    prime = Prime('test', b'test'.ljust(32, b'\0'), DAY, alm={Chain.ETHEREUM: holder}, venues=[venue])
    logs = []
    for block, amount in [(2, 110), (3, 220)]:
        logs.append(LogRow(block, 1, block, ethena.SUSDE, ethena.WITHDRAW,
                           topic(HOLDER), topic(SILO), topic(HOLDER),
                           '0x' + f'{amount * 10**18:064x}{amount * 10**18:064x}', f'0x{block}'))
    logs.append(LogRow(4, 1, 4, ethena.USDE, TRANSFER_TOPIC0, topic(SILO), topic(HOLDER), None,
                       '0x' + f'{330 * 10**18:064x}', '0x4'))
    monkeypatch.setattr(ethena.hypersync_store, 'fetch_logs', lambda *a, **k: logs)
    monkeypatch.setattr(ethena, '_view', lambda *a: topic(SILO))
    monkeypatch.setattr(ethena, '_pending_claim', lambda holder, block: {2: 110, 3: 330, 4: 0}[block] * 10**18)
    batches = [B('seed', DAY, 1, 'ethereum', 1, (M('staked', D(0), D(300)),), D(300)),
               B('ethereum:0x2', DAY, 2, 'ethereum', 2, (M('staked', D(330), D(-110)),)),
               B('ethereum:0x3', DAY, 3, 'ethereum', 3, (M('staked', D(220), D(-220)),)),
               B('ethereum:0x4', DAY, 4, 'ethereum', 4, (M('cash', D(0), D(330)),))]
    fixed, custody = ethena.link_ethena_cooldowns(prime, {Chain.ETHEREUM: 4}, batches, {})
    replay = replay_history(H(tuple(fixed), {'S': 'staked'}, {}, custody), DAY, DAY)
    assert replay.ledger.account('cash').borrowed == D(300)
    assert replay.ledger.account('cash').value == D(330)
    assert not replay.unmatched_receipts and not replay.unmatched_outflows
    assert replay.ledger.account(custody['S'][0]).borrowed == 0
    monkeypatch.setattr(ethena, '_pending_claim', lambda holder, block: 0)
    with pytest.raises(ValueError, match='state does not match'):
        ethena.link_ethena_cooldowns(prime, {Chain.ETHEREUM: 4}, batches, {})
    monkeypatch.setattr(ethena, '_pending_claim', lambda holder, block: {2: 110, 3: 330, 4: 0}[block] * 10**18)
    logs[-1] = LogRow(4, 1, 4, ethena.USDE, TRANSFER_TOPIC0, topic(SILO), topic(HOLDER), None,
                      '0x' + f'{331 * 10**18:064x}', '0x4')
    with pytest.raises(ValueError, match='exceeds owned'):
        ethena.link_ethena_cooldowns(prime, {Chain.ETHEREUM: 4}, batches, {})
