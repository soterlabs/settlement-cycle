from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace as NS

from settle.compute.allocation_capital import replay_history
from settle.domain.pricing import PricingCategory
from settle.domain.primes import Chain, Prime, Token, Venue
from settle.domain.sky_tokens import USDS_ETHEREUM
from settle.extract import uniswap_v3 as v3
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory
from settle.normalize.allocation_nfts import NFTCapital
from tests.unit.test_allocation_capital_source import HOLDER, VAULT, log, topic


def test_nft_delayed_collect_separates_fees_from_borrowed_principal(monkeypatch):
    venue = Venue('N1', Chain.ETHEREUM, Token(Chain.ETHEREUM, VAULT, 'NFT', 0),
                  PricingCategory.LP_POOL, lp_kind='uniswap_v3')
    prime = Prime('test', b'TEST'.ljust(32, b'\0'), date(2026, 8, 1),
                  alm={Chain.ETHEREUM: HOLDER}, venues=[venue])
    adapter = NFTCapital(prime, Chain.ETHEREUM)
    position = NS(token0=USDS_ETHEREUM.address, token1=USDS_ETHEREUM.address)
    adapter.positions[(v3.NFPM_CANONICAL.hex, 1, 'N1')] = (venue, position)
    from settle.extract import rpc
    monkeypatch.setattr(rpc, 'eth_call', lambda *a: topic(HOLDER))
    batches = []
    events = [
        log(1, 1, v3.NFPM_CANONICAL, v3.TOPIC_INCREASE_LIQUIDITY, ['0x' + f'{1:064x}'], [100, 100 * 10**18, 0]),
        log(2, 2, v3.NFPM_CANONICAL, v3.TOPIC_DECREASE_LIQUIDITY, ['0x' + f'{1:064x}'], [50, 50 * 10**18, 0]),
        log(3, 3, v3.NFPM_CANONICAL, v3.TOPIC_COLLECT, ['0x' + f'{1:064x}'], [int(HOLDER.hex, 16), 55 * 10**18, 0]),
    ]
    for i, event in enumerate(events):
        movements, fees = adapter.movements([event])
        if i == 2:
            assert fees[(USDS_ETHEREUM.address.hex, HOLDER.hex)] == D(5)
            movements.append(AssetMovement('cash', D(0), D(55), D(5)))
        batches.append(CapitalBatch(str(i), date(2026, 8, i + 1), i, 'ethereum', i,
                                    tuple(movements), D(100) if i == 0 else D(0)))
    history = CapitalHistory(tuple(batches), adapter.accounts, {}, dict(adapter.custody_accounts))
    replay = replay_history(history, date(2026, 8, 1), date(2026, 8, 3))
    assert replay.ledger.account('cash').borrowed == D(50)
    assert sum(replay.daily[date(2026, 8, 2)].values()) == D(100)
    assert not replay.unmatched_receipts
    assert not replay.unmatched_outflows


def test_v4_liquidity_withdrawal_does_not_borrow_the_gain(monkeypatch):
    from settle.domain.primes import Address, UniV4PoolKey
    from settle.extract import uniswap_v4 as v4
    key = v4.V4PoolKey(USDS_ETHEREUM.address, USDS_ETHEREUM.address, 5, 1, Address(bytes(20)))
    venue = Venue('N4', Chain.ETHEREUM, Token(Chain.ETHEREUM, VAULT, 'NFT', 0),
                  PricingCategory.LP_POOL, lp_kind='uniswap_v4',
                  nft_position_manager=v4.POSITION_MANAGER_CANONICAL,
                  univ4_pool_key=UniV4PoolKey(key.currency0, key.currency1, 5, 1, key.hooks),
                  univ4_token_ids=(1,))
    prime = Prime('test', b'TEST'.ljust(32, b'\0'), date(2026, 8, 1),
                  alm={Chain.ETHEREUM: HOLDER}, venues=[venue])
    adapter = NFTCapital(prime, Chain.ETHEREUM)
    adapter.positions[(v4.POSITION_MANAGER_CANONICAL.hex, 1, 'N4')] = (venue, key)
    monkeypatch.setattr(v4, 'owner_of', lambda *a: HOLDER)
    monkeypatch.setattr(v4, 'read_slot0', lambda *a: NS(sqrt_price_x96=a[-1]))
    monkeypatch.setattr(v3, 'get_amounts_for_liquidity',
                        lambda price, lower, upper, liq: (liq * 10**18 * price, 0))
    batches = []
    for block, delta in [(1, 100), (2, -50)]:
        event = log(block, block, v4.POOL_MANAGER_CANONICAL, v4.TOPIC_MODIFY_LIQUIDITY,
                    ['0x' + key.pool_id().hex(), topic(v4.POSITION_MANAGER_CANONICAL)],
                    [0, 1, delta % (1 << 256), 1])
        moves, _ = adapter.movements([event])
        if block == 2:
            moves.append(AssetMovement('cash', D(0), D(100)))
        batches.append(CapitalBatch(str(block), date(2026, 8, block), block, 'ethereum', block,
                                   tuple(moves), D(100) if block == 1 else D(0)))
    replay = replay_history(CapitalHistory(tuple(batches), {}, {}), date(2026, 8, 1), date(2026, 8, 2))
    assert replay.ledger.account('cash').borrowed == D(50)
    assert not replay.unmatched_receipts


def test_nft_holder_transfer_moves_queued_principal_as_well_as_liquidity(monkeypatch):
    from dataclasses import replace

    from settle.domain.primes import Address
    other = Address(bytes.fromhex('77' * 20))
    venue = Venue('A', Chain.ETHEREUM, Token(Chain.ETHEREUM, VAULT, 'NFT', 0),
                  PricingCategory.LP_POOL, lp_kind='uniswap_v3')
    prime = Prime('test', b'TEST'.ljust(32, b'\0'), date(2026, 8, 1),
                  alm={Chain.ETHEREUM: HOLDER}, venues=[venue, replace(venue, id='B', holder_override=other)])
    adapter = NFTCapital(prime, Chain.ETHEREUM)
    position = NS(token0=USDS_ETHEREUM.address, token1=USDS_ETHEREUM.address, tick_lower=0, tick_upper=1)
    for v in prime.venues:
        adapter.positions[(v3.NFPM_CANONICAL.hex, 1, v.id)] = (v, position)
    old = f'nft:ethereum:{v3.NFPM_CANONICAL.hex}:1:A'
    new = f'nft:ethereum:{v3.NFPM_CANONICAL.hex}:1:B'
    adapter.liquidity[old] = 100
    adapter.owed[old] = [10 * 10**18, 0]
    monkeypatch.setattr(v3, 'read_position', lambda *a: NS(liquidity=100))
    monkeypatch.setattr(v3, 'read_pool_state', lambda *a: NS(sqrt_price_x96=1))
    monkeypatch.setattr(v3, 'get_amounts_for_liquidity', lambda *a: (100 * 10**18, 0))
    from settle.extract.transfer_logs import TRANSFER_TOPIC0
    moved, _ = adapter.movements([log(2, 1, v3.NFPM_CANONICAL, TRANSFER_TOPIC0,
                                    [topic(HOLDER), topic(other), '0x' + f'{1:064x}'], [])])
    day = date(2026, 8, 1)
    history = CapitalHistory((
        CapitalBatch('fund', day, 1, 'ethereum', 1,
            (AssetMovement(old, D(0), D(100)), AssetMovement(old + ':collect', D(0), D(10))), D(110)),
        CapitalBatch('transfer', day, 2, 'ethereum', 2, tuple(moved))), {}, {})
    replay = replay_history(history, day, day)
    assert replay.ledger.account(new).borrowed == D(100)
    assert replay.ledger.account(new + ':collect').borrowed == D(10)
    assert replay.ledger.account(old).borrowed == 0
    assert not replay.unmatched_receipts
