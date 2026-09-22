from datetime import date
from decimal import Decimal as D

from settle.compute.allocation_capital import replay_history
from settle.domain.pricing import PricingCategory
from settle.domain.primes import Address, Chain, Prime, Token, Venue
from settle.domain.sky_tokens import USDS_ETHEREUM
from settle.extract import aave_reconstruct as aave
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize import allocation_async_vaults as async_source
from settle.normalize import allocation_capital as source

HOLDER = Address(bytes.fromhex("11" * 20))
VAULT = Address(bytes.fromhex("22" * 20))
MANAGER = Address(bytes.fromhex("33" * 20))
QUEUE = Address(bytes.fromhex("44" * 20))
ILK = b"TEST".ljust(32, b"\0")
DAY = date(2026, 8, 1)
STAMP = 1785542400


def topic(address):
    return "0x" + address.value.hex().rjust(64, "0")


def log(block, index, address, signature, topics, words, tx=None):
    ts = [signature, *topics]
    ts += [None] * (4 - len(ts))
    return LogRow(block, index, STAMP + block, address.hex, *ts,
                  "0x" + "".join(f"{n:064x}" for n in words), tx or f"0x{block:064x}")


def setup(monkeypatch, logs, category=PricingCategory.ERC4626_VAULT):
    venue = Venue("V1", Chain.ETHEREUM, Token(Chain.ETHEREUM, VAULT, "share", 18),
                  category, underlying=USDS_ETHEREUM)
    prime = Prime("test", ILK, DAY, alm={Chain.ETHEREUM: HOLDER}, venues=[venue])
    monkeypatch.setattr(source.hypersync_store, "fetch_logs", lambda *a, **k: logs)
    monkeypatch.setattr(source.rpc, "ilk_rate", lambda *a: 10**27)
    monkeypatch.setattr(source, "_decode_dart", lambda data: 100 * 10**18)
    monkeypatch.setattr(source, "get_unit_price", lambda *a, **k: D(1))
    return prime


def draw(block=1, index=0):
    return log(block, index, Address.from_str(source._VAT), source._FROB_T0,
               ["0x" + ILK.hex()], [0])


def test_lending_mint_uses_scaled_principal_not_interest_in_transfer(monkeypatch):
    ray = 10**27
    logs = [draw(), log(1, 1, VAULT, aave.MINT_T0, [topic(HOLDER), topic(HOLDER)],
                         [100 * 10**18, 0, ray]),
            # Withdraw 10 after the index reaches 1.1. Aave emits a Mint
            # with zero value because the accrued interest equals withdrawal.
            log(2, 2, VAULT, aave.MINT_T0, [topic(HOLDER), topic(HOLDER)],
                [0, 10 * 10**18, 11 * 10**26]),
            log(2, 3, USDS_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(VAULT), topic(HOLDER)], [10 * 10**18])]
    prime = setup(monkeypatch, logs, PricingCategory.AAVE_ATOKEN)
    history = source.fetch_capital_history(prime, {Chain.ETHEREUM: 2})
    replay = replay_history(history, DAY, DAY)
    account = history.venue_accounts["V1"]
    assert abs(replay.ledger.account(account).borrowed - D(1000) / D(11)) < D("1e-15")
    assert not replay.unmatched_receipts


def test_deposit_uses_actual_cash_amount_not_one_share_quote(monkeypatch):
    logs = [draw(), log(1, 1, VAULT, TRANSFER_TOPIC0,
                       [topic(Address(bytes(20))), topic(HOLDER)], [99 * 10**18]),
            log(1, 2, VAULT, source.DEPOSIT, [topic(HOLDER), topic(HOLDER)],
                [100 * 10**18, 99 * 10**18])]
    prime = setup(monkeypatch, logs)
    history = source.fetch_capital_history(prime, {Chain.ETHEREUM: 1})
    replay = replay_history(history, DAY, DAY)
    assert replay.ledger.account(history.venue_accounts["V1"]).borrowed == D(100)
    assert not replay.unmatched_outflows


def test_queue_links_later_cash_to_original_principal(monkeypatch):
    logs = [draw(), log(1, 1, VAULT, TRANSFER_TOPIC0,
                       [topic(Address(bytes(20))), topic(HOLDER)], [100 * 10**18]),
            log(2, 2, VAULT, TRANSFER_TOPIC0, [topic(HOLDER), topic(MANAGER)], [100 * 10**18]),
            log(2, 3, QUEUE, sorted(source.QUEUE_CREATED)[0],
                ["0x" + "0" * 63 + "1", topic(HOLDER)], [100 * 10**18]),
            log(3, 4, QUEUE, sorted(source.QUEUE_PROCESSED)[0],
                ["0x" + "0" * 63 + "1", topic(HOLDER)], [100 * 10**18, 110 * 10**18]),
            log(3, 5, USDS_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(VAULT), topic(HOLDER)], [110 * 10**18])]
    prime = setup(monkeypatch, logs)
    monkeypatch.setattr(source.rpc, "eth_call", lambda chain, addr, data, block:
                        topic(MANAGER if addr == VAULT else QUEUE))
    history = source.fetch_capital_history(prime, {Chain.ETHEREUM: 3})
    replay = replay_history(history, DAY, DAY)
    cash = source._account(Chain.ETHEREUM, USDS_ETHEREUM.address, HOLDER)
    assert replay.ledger.account(cash).borrowed == D(100)
    assert replay.ledger.account(cash).value == D(110)
    assert not replay.unmatched_receipts
    assert not replay.unmatched_outflows
    assert history.custody_accounts["V1"]


def test_async_subscription_and_partial_redemption_keep_funding_basis(monkeypatch):
    gateway = Address(bytes.fromhex("55" * 20))
    escrow = Address(bytes.fromhex("66" * 20))
    zero = Address(bytes(20))
    request_topics = [topic(HOLDER), topic(HOLDER), "0x" + "0" * 64]
    logs = [
        draw(),
        log(1, 1, USDS_ETHEREUM.address, TRANSFER_TOPIC0, [topic(zero), topic(HOLDER)], [100 * 10**18]),
        log(1, 2, USDS_ETHEREUM.address, TRANSFER_TOPIC0, [topic(HOLDER), topic(escrow)], [100 * 10**18]),
        log(1, 3, gateway, async_source.DEPOSIT_REQUEST, request_topics, [int(HOLDER.hex, 16), 100 * 10**18]),
        log(2, 4, VAULT, TRANSFER_TOPIC0, [topic(escrow), topic(HOLDER)], [100 * 10**18]),
        log(2, 5, gateway, source.DEPOSIT, [topic(HOLDER), topic(HOLDER)], [100 * 10**18, 100 * 10**18]),
        log(3, 6, VAULT, TRANSFER_TOPIC0, [topic(HOLDER), topic(escrow)], [50 * 10**18]),
        log(3, 7, gateway, async_source.REDEEM_REQUEST, request_topics, [int(HOLDER.hex, 16), 50 * 10**18]),
        log(4, 8, USDS_ETHEREUM.address, TRANSFER_TOPIC0, [topic(escrow), topic(HOLDER)], [55 * 10**18]),
        log(4, 9, gateway, async_source.WITHDRAW, [topic(HOLDER)] * 3, [55 * 10**18, 50 * 10**18]),
    ]
    prime = setup(monkeypatch, logs, PricingCategory.RWA_TRANCHE)
    share_selector = "0x" + source.keccak256(b"share()")[:4].hex()
    monkeypatch.setattr(source.rpc, "eth_call", lambda chain, addr, data, block:
                        topic(VAULT if data == share_selector else USDS_ETHEREUM.address))
    monkeypatch.setattr(source.rpc, "convert_to_assets", lambda chain, addr, shares, block:
                        shares if block < 3 else shares * 11 // 10)
    history = source.fetch_capital_history(prime, {Chain.ETHEREUM: 4})
    replay = replay_history(history, DAY, DAY)
    cash = source._account(Chain.ETHEREUM, USDS_ETHEREUM.address, HOLDER)
    assert replay.ledger.account(history.venue_accounts["V1"]).borrowed == D(50)
    assert replay.ledger.account(cash).borrowed == D(50)
    assert replay.ledger.account(cash).value == D(55)
    assert not replay.unmatched_receipts
    assert not replay.unmatched_outflows


def test_redemption_uses_paid_cash_and_preserves_share_fraction(monkeypatch):
    logs = [draw(), log(1, 1, VAULT, TRANSFER_TOPIC0,
                       [topic(Address(bytes(20))), topic(HOLDER)], [100 * 10**18]),
            log(2, 2, VAULT, TRANSFER_TOPIC0,
                [topic(HOLDER), topic(Address(bytes(20)))], [50 * 10**18]),
            log(2, 3, VAULT, source.WITHDRAW, [topic(HOLDER)] * 3,
                [55 * 10**18, 50 * 10**18]),
            log(2, 4, USDS_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(VAULT), topic(HOLDER)], [55 * 10**18])]
    prime = setup(monkeypatch, logs)
    history = source.fetch_capital_history(prime, {Chain.ETHEREUM: 2})
    replay = replay_history(history, DAY, DAY)
    cash = source._account(Chain.ETHEREUM, USDS_ETHEREUM.address, HOLDER)
    assert replay.ledger.account(cash).borrowed == D(50)
    assert replay.ledger.account(cash).value == D(55)
    assert replay.ledger.account(history.venue_accounts['V1']).borrowed == D(50)
    assert not replay.unmatched_receipts


def test_bridged_susds_uses_origin_vault_at_event_time(monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from settle.domain.config import load_prime_by_id
    from settle.domain.sky_tokens import sUSDS_ETHEREUM

    venue = next(v for v in load_prime_by_id('spark').venues if v.id == 'S37')
    monkeypatch.setattr(source.hypersync, 'block_timestamp', lambda chain, block: STAMP)

    def resolve(chain, stamp):
        assert chain == 'ethereum'
        assert stamp == datetime.fromtimestamp(STAMP, UTC)
        return 999

    def convert(chain, token, shares, block):
        assert (chain, token, shares, block) == (Chain.ETHEREUM, sUSDS_ETHEREUM.address, 10**18, 999)
        return 11 * 10**17

    monkeypatch.setattr(source.rpc, 'convert_to_assets', convert)
    assert source._capital_unit_price(venue, 100, block_resolver=SimpleNamespace(
        block_at_or_before=resolve)) == D('1.1')


def test_history_keeps_draws_separate_by_ilk(monkeypatch):
    from dataclasses import replace

    second = b'SECOND'.ljust(32, b'\0')
    logs = [draw(), log(1, 1, Address.from_str(source._VAT), source._FROB_T0,
                       ['0x' + second.hex()], [0])]
    prime = replace(setup(monkeypatch, logs), extra_ilks=(second,))
    history = source.fetch_capital_history(prime, {Chain.ETHEREUM: 1})
    assert history.batches[0].minted == D(200)
    assert history.batches[0].minted_by_ilk == {'0x' + ILK.hex(): D(100), '0x' + second.hex(): D(100)}
