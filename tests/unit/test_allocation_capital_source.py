from dataclasses import replace
from datetime import date
from decimal import Decimal as D

from settle.compute.allocation_capital import replay_history
from settle.domain.pricing import PricingCategory
from settle.domain.primes import Address, Chain, Prime, PrincipalReturnOverride, Token, Venue
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


def test_principal_return_override_uses_day_net_without_creating_income(monkeypatch):
    sender = MANAGER
    logs = [log(1, 1, USDS_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(sender), topic(HOLDER)], [60 * 10**18]),
            log(2, 2, USDS_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(sender), topic(HOLDER)], [50 * 10**18]),
            log(3, 3, USDS_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(HOLDER), topic(sender)], [10 * 10**18])]
    prime = replace(setup(monkeypatch, logs),
                    external_alm_sources={Chain.ETHEREUM: [sender]},
                    principal_return_overrides={Chain.ETHEREUM: {
                        sender: [PrincipalReturnOverride(DAY, D(100), 'USDS')]}})
    history = source.fetch_capital_history(prime, {Chain.ETHEREUM: 3})
    assert sum(m.external_income for b in history.batches for m in b.movements) == 0
    replay = replay_history(history, DAY, DAY)
    assert replay.unmatched_receipts  # No invented loan/custody funding link.
    assert all(a.borrowed == 0 for a in replay.ledger.accounts.values())


def test_unmatched_principal_exception_keeps_actual_income(monkeypatch):
    logs = [log(1, 1, USDS_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(MANAGER), topic(HOLDER)], [10 * 10**18])]
    base = replace(setup(monkeypatch, logs),
                   external_alm_sources={Chain.ETHEREUM: [MANAGER]})
    for entry in [PrincipalReturnOverride(DAY, D(100), 'USDS'),
                  PrincipalReturnOverride(DAY, D(10), 'USDC'),
                  PrincipalReturnOverride(date(2026, 8, 2), D(10), 'USDS')]:
        prime = replace(base, principal_return_overrides={Chain.ETHEREUM: {MANAGER: [entry]}})
        history = source.fetch_capital_history(prime, {Chain.ETHEREUM: 1})
        assert sum(m.external_income for b in history.batches for m in b.movements) == D(10)


def test_partial_principal_return_preserves_interest(monkeypatch):
    logs = [log(1, 1, USDS_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(MANAGER), topic(HOLDER)], [110 * 10**18])]
    prime = replace(setup(monkeypatch, logs),
                    external_alm_sources={Chain.ETHEREUM: [MANAGER]},
                    principal_return_overrides={Chain.ETHEREUM: {MANAGER: [
                        PrincipalReturnOverride(DAY, D(110), 'USDS', capital_amount=D(100))]}})
    history = source.fetch_capital_history(prime, {Chain.ETHEREUM: 1})
    assert sum(m.external_income for b in history.batches for m in b.movements) == D(10)


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


def test_morpho_fee_mints_are_income_even_alongside_a_funded_deposit(monkeypatch):
    from dataclasses import replace

    import pytest

    from settle.normalize import allocation_morpho_fees as fees

    zero = topic(Address(bytes(20)))
    logs = [draw(),
            log(1, 1, VAULT, TRANSFER_TOPIC0, [zero, topic(HOLDER)], [2 * 10**18]),
            log(1, 3, VAULT, TRANSFER_TOPIC0, [zero, topic(HOLDER)], [99 * 10**18]),
            log(1, 4, VAULT, source.DEPOSIT, [topic(HOLDER), topic(HOLDER)],
                [100 * 10**18, 99 * 10**18]),
            log(2, 1, VAULT, TRANSFER_TOPIC0, [zero, topic(HOLDER)], [3 * 10**18])]
    accruals = [log(1, 2, VAULT, fees.ACCRUE_INTEREST, [], [102 * 10**18, 2 * 10**18]),
                log(2, 2, VAULT, fees.ACCRUE_INTEREST, [], [105 * 10**18, 3 * 10**18])]
    prime = setup(monkeypatch, logs)
    monkeypatch.setitem(fees.VAULTS, Chain.ETHEREUM, {VAULT.hex})
    monkeypatch.setattr(source.hypersync_store, 'fetch_logs', lambda chain, selections, *a, **k:
                        accruals if selections[0].get('address') == [VAULT.hex] else logs)
    history = source.fetch_capital_history(prime, {Chain.ETHEREUM: 2})
    replay = replay_history(history, DAY, DAY)
    asset = history.venue_accounts['V1']
    assert replay.ledger.account(asset).borrowed == D(100)
    assert sum(m.external_income for b in history.batches for m in b.movements) == D(5)
    assert not replay.unmatched_receipts and not replay.unmatched_outflows
    assert asset not in replay.uncertain_accounts
    # The adjacent ERC4626 deposit mint (99 shares) is not fee income.
    assert fees.fee_mints(Chain.ETHEREUM, [r for r in logs + accruals if r.block_number == 1],
                          {(VAULT.hex, HOLDER.hex)}) == {
        (VAULT.hex, HOLDER.hex): 2 * 10**18}
    with pytest.raises(ValueError, match='disagrees with accrual'):
        fees.fee_mints(Chain.ETHEREUM,
                      [logs[1], replace(accruals[0], data='0x' + f'{102:064x}{1:064x}')],
                      {(VAULT.hex, HOLDER.hex)})
    assert not fees.fee_mints(Chain.OPTIMISM, [logs[1], accruals[0]], {(VAULT.hex, HOLDER.hex)})


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


def test_multiple_queue_redemptions_use_total_cash_and_share_fraction(monkeypatch):
    # Exact raw amounts from Spark's failing Maple transaction. The second
    # redemption's price is slightly higher due to six-decimal rounding.
    from dataclasses import replace

    for remaining in (0, 10**9):
        shares1, shares2 = 3926898847, 2848712244
        cash1, cash2 = 4592589243, 3331627760
        shares = shares1 + shares2 + remaining
        logs = [draw(),
            log(1, 1, VAULT, TRANSFER_TOPIC0, [topic(Address(bytes(20))), topic(HOLDER)], [shares]),
            log(2, 2, VAULT, TRANSFER_TOPIC0, [topic(HOLDER), topic(MANAGER)], [shares]),
            log(2, 3, QUEUE, sorted(source.QUEUE_CREATED)[0], ['0x01', topic(HOLDER)], [shares]),
            log(3, 4, QUEUE, sorted(source.QUEUE_PROCESSED)[0], ['0x01', topic(HOLDER)], [shares1, cash1]),
            log(3, 5, QUEUE, sorted(source.QUEUE_PROCESSED)[0], ['0x02', topic(HOLDER)], [shares2, cash2]),
            log(3, 6, USDS_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(VAULT), topic(HOLDER)], [(cash1 + cash2) * 10**12])]
        prime = setup(monkeypatch, logs)
        prime = replace(prime, venues=[replace(prime.venues[0],
                        token=replace(prime.venues[0].token, decimals=6))])
        monkeypatch.setattr(source, '_decode_dart', lambda _, shares=shares: shares * 10**12)
        # Underlying is 18 decimal in this fixture; queue paid amounts must
        # use that scale, while the share rounding remains at six decimals.
        logs[4] = log(3, 4, QUEUE, sorted(source.QUEUE_PROCESSED)[0],
                      ['0x01', topic(HOLDER)], [shares1, cash1 * 10**12])
        logs[5] = log(3, 5, QUEUE, sorted(source.QUEUE_PROCESSED)[0],
                      ['0x02', topic(HOLDER)], [shares2, cash2 * 10**12])
        monkeypatch.setattr(source.rpc, 'eth_call', lambda chain, addr, data, block:
                            topic(MANAGER if addr == VAULT else QUEUE))
        history = source.fetch_capital_history(prime, {Chain.ETHEREUM: 3})
        replay = replay_history(history, DAY, DAY)
        cash_account = source._account(Chain.ETHEREUM, USDS_ETHEREUM.address, HOLDER)
        assert abs(replay.ledger.account(cash_account).borrowed - D(shares1 + shares2) / 10**6) < D('1e-18')
        queue = history.custody_accounts['V1'][0]
        assert abs(replay.ledger.account(queue).borrowed - D(remaining) / 10**6) < D('1e-18')
        assert not replay.unmatched_receipts
        assert not replay.unmatched_outflows


def test_secondary_custodian_swap_keeps_both_cash_legs(monkeypatch):
    USDC_ETHEREUM = Token(Chain.ETHEREUM, Address.from_str(
        '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'), 'USDC', 6)

    venue = Venue('secondary', Chain.ETHEREUM, USDS_ETHEREUM,
                  PricingCategory.PAR_STABLE, holder_override=MANAGER)
    prime = Prime('test', ILK, DAY, alm={Chain.ETHEREUM: HOLDER}, venues=[venue])
    logs = [draw(),
            log(1, 1, USDS_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(Address(bytes(20))), topic(MANAGER)], [100 * 10**18]),
            log(2, 2, USDS_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(MANAGER), topic(VAULT)], [100 * 10**18]),
            log(2, 3, USDC_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(VAULT), topic(MANAGER)], [100 * 10**6])]
    monkeypatch.setattr(source.hypersync_store, 'fetch_logs', lambda *a, **k: logs)
    monkeypatch.setattr(source.rpc, 'ilk_rate', lambda *a: 10**27)
    monkeypatch.setattr(source, '_decode_dart', lambda data: 100 * 10**18)
    monkeypatch.setattr(source, 'get_unit_price', lambda *a, **k: D(1))
    result = replay_history(source.fetch_capital_history(prime, {Chain.ETHEREUM: 2}), DAY, DAY)
    account = source._account(Chain.ETHEREUM, USDC_ETHEREUM.address, MANAGER)
    assert result.ledger.account(account).borrowed == 100
    assert not result.unmatched_receipts
    assert not result.unmatched_outflows


def test_configured_issuer_yield_mint_does_not_create_unknown_funding(monkeypatch):
    from dataclasses import replace

    logs = [draw(), log(1, 1, VAULT, TRANSFER_TOPIC0,
                [topic(Address(bytes(20))), topic(HOLDER)], [100 * 10**18]),
            log(2, 2, VAULT, TRANSFER_TOPIC0,
                [topic(Address(bytes(20))), topic(HOLDER)], [3 * 10**18]),
            log(2, 3, VAULT, TRANSFER_TOPIC0,
                [topic(Address(bytes(20))), topic(HOLDER)], [3 * 10**18])]
    prime = setup(monkeypatch, logs, PricingCategory.RWA_TRANCHE)
    prime = replace(prime, venues=[replace(prime.venues[0], min_transfer_amount_usd=D(5))])
    history = source.fetch_capital_history(prime, {Chain.ETHEREUM: 2})
    replay = replay_history(history, DAY, DAY)
    account = replay.ledger.account(history.venue_accounts['V1'])
    assert account.borrowed == 100
    assert account.value == 106
    assert not replay.unmatched_receipts
    assert not replay.uncertain_accounts


def test_cross_chain_cash_distributions_are_income_only_for_exact_configured_route(monkeypatch):
    from settle.domain.primes import CashDistributionSource

    logs = [log(1, 1, USDS_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(MANAGER), topic(HOLDER)], [10 * 10**18])]
    base = setup(monkeypatch, logs)
    route = CashDistributionSource(MANAGER, USDS_ETHEREUM.address, Chain.ETHEREUM)
    for distribution, expected in [
        (route, D(10)),
        (replace(route, payer=VAULT), D(0)),
        (replace(route, token=VAULT), D(0)),
        (replace(route, chain=Chain.BASE), D(0)),
        (replace(route, chain=None), D(0)),  # Defaults to investment chain.
    ]:
        offchain = replace(base.venues[0], chain=Chain.AVALANCHE_C,
            notional_principal_usd=D(100), skip=True, cash_distributions=[distribution])
        prime = replace(base, venues=[offchain])
        history = source.fetch_capital_history(prime, {Chain.ETHEREUM: 1})
        assert sum(m.external_income for b in history.batches for m in b.movements) == expected
        replay = replay_history(history, DAY, DAY)
        assert bool(replay.unmatched_receipts) is (expected == 0)
        assert all(a.borrowed == 0 for a in replay.ledger.accounts.values())


def test_distribution_payer_does_not_classify_cash_at_a_different_holder(monkeypatch):
    from settle.domain.primes import CashDistributionSource

    logs = [log(1, 1, USDS_ETHEREUM.address, TRANSFER_TOPIC0,
                [topic(MANAGER), topic(QUEUE)], [10 * 10**18])]
    base = setup(monkeypatch, logs)
    paying_venue = replace(base.venues[0], cash_distributions=[
        CashDistributionSource(MANAGER, USDS_ETHEREUM.address, Chain.ETHEREUM)])
    other_cash = Venue('Other cash', Chain.ETHEREUM, USDS_ETHEREUM,
                      PricingCategory.PAR_STABLE, holder_override=QUEUE)
    history = source.fetch_capital_history(replace(base, venues=[paying_venue, other_cash]),
                                          {Chain.ETHEREUM: 1})
    assert sum(m.external_income for b in history.batches for m in b.movements) == 0


def test_actual_curve_gain_reinvested_same_tx_never_becomes_borrowed_basis(monkeypatch):
    from settle.normalize.allocation_curve_swaps import COINS, EXCHANGE, POOL
    from settle.normalize.allocation_curve_swaps import HOLDER as GROVE

    alm, pool = Address.from_str(GROVE), Address.from_str(POOL)
    usdc = Token(Chain.ETHEREUM, Address.from_str(COINS[0][0]), 'USDC', 6)
    rlusd = Token(Chain.ETHEREUM, Address.from_str(COINS[1][0]), 'RLUSD', 18)
    logs = [draw(), log(1, 1, usdc.address, TRANSFER_TOPIC0,
                       [topic(Address(bytes(20))), topic(alm)], [100 * 10**6]),
            log(2, 1, usdc.address, TRANSFER_TOPIC0, [topic(alm), topic(pool)], [100 * 10**6]),
            log(2, 2, rlusd.address, TRANSFER_TOPIC0, [topic(pool), topic(alm)], [110 * 10**18]),
            log(2, 3, pool, EXCHANGE, [topic(alm)], [0, 100 * 10**6, 1, 110 * 10**18]),
            log(2, 4, rlusd.address, TRANSFER_TOPIC0, [topic(alm), topic(MANAGER)], [110 * 10**18]),
            log(2, 5, VAULT, aave.MINT_T0, [topic(alm), topic(alm)], [110 * 10**18, 0, 10**27])]
    base = setup(monkeypatch, logs, PricingCategory.AAVE_ATOKEN)
    prime = replace(base, id='grove', alm={Chain.ETHEREUM: alm}, venues=[
        Venue('E13', Chain.ETHEREUM, rlusd, PricingCategory.PAR_STABLE),
        Venue('E15', Chain.ETHEREUM, usdc, PricingCategory.PAR_STABLE),
        replace(base.venues[0], underlying=rlusd)])
    h = source.fetch_capital_history(prime, {Chain.ETHEREUM: 2})
    r = replay_history(h, DAY, DAY)
    a = r.ledger.account(h.venue_accounts['V1'])
    assert a.value == D(110) and a.borrowed == D(100)
    assert r.ledger.drawn == D(100)
    assert not r.unmatched_receipts and not r.unmatched_outflows


def test_v2_fees_and_withdrawal_use_the_same_execution_price(monkeypatch):
    from settle.normalize import allocation_morpho_fees as fees

    zero = topic(Address(bytes(20)))
    # Run both a net burn and a net mint: either still contains a cash exit.
    for fee_units in (2, 20):
        logs = [draw(),
                log(1, 1, VAULT, TRANSFER_TOPIC0, [zero, topic(HOLDER)], [100 * 10**18]),
                log(2, 2, VAULT, TRANSFER_TOPIC0, [zero, topic(HOLDER)], [fee_units * 10**18]),
                log(2, 3, VAULT, TRANSFER_TOPIC0, [topic(HOLDER), zero], [10 * 10**18]),
                log(2, 4, VAULT, source.WITHDRAW, [topic(HOLDER), topic(HOLDER), topic(HOLDER)],
                    [11 * 10**18, 10 * 10**18]),
                log(2, 5, USDS_ETHEREUM.address, TRANSFER_TOPIC0, [topic(VAULT), topic(HOLDER)], [11 * 10**18])]
        accrual = log(2, 1, VAULT, fees.ACCRUE_INTEREST_V2, [],
                      [100 * 10**18, 110 * 10**18, fee_units * 10**18, 0])
        prime = setup(monkeypatch, logs)
        monkeypatch.setitem(fees.V2_VAULTS, Chain.ETHEREUM, {VAULT.hex})
        monkeypatch.setattr(source.hypersync_store, 'fetch_logs', lambda chain, selections, *a, **k:
                            [accrual] if selections[0].get('address') == [VAULT.hex] else logs)
        h = source.fetch_capital_history(prime, {Chain.ETHEREUM: 2})
        asset = h.venue_accounts['V1']
        m = next(m for m in h.batches[-1].movements if m.account == asset)
        assert m.external_income == D(fee_units) * D('1.1')
        assert m.change - m.external_income == -11
        assert m.value_before == 110
        r = replay_history(h, DAY, DAY)
        cash = source._account(Chain.ETHEREUM, USDS_ETHEREUM.address, HOLDER)
        assert r.ledger.account(cash).value == 11
        assert abs(r.ledger.account(cash).borrowed - D(1000)/D(100+fee_units)) < D('1e-20')
        assert abs(sum(a.borrowed for a in r.ledger.accounts.values()) - 100) < D('1e-20')
        assert not r.unmatched_receipts and not r.unmatched_outflows


def test_v2_fee_event_order_does_not_capture_deposit_or_other_holder_mints(monkeypatch):
    import pytest

    from settle.normalize import allocation_morpho_fees as fees

    zero = topic(Address(bytes(20)))
    monkeypatch.setitem(fees.V2_VAULTS, Chain.ETHEREUM, {VAULT.hex})
    accrual = log(1, 0, VAULT, fees.ACCRUE_INTEREST_V2, [], [100, 110, 2, 3])
    perf_other = log(1, 1, VAULT, TRANSFER_TOPIC0, [zero, topic(MANAGER)], [2])
    mgmt = log(1, 2, VAULT, TRANSFER_TOPIC0, [zero, topic(HOLDER)], [3])
    deposit = log(1, 3, VAULT, TRANSFER_TOPIC0, [zero, topic(HOLDER)], [99])
    tracked = {(VAULT.hex, HOLDER.hex)}
    assert fees.fee_mints(Chain.ETHEREUM, [accrual, perf_other, mgmt, deposit], tracked) == {(VAULT.hex, HOLDER.hex): 3}
    assert fees.fee_mints(Chain.ETHEREUM, [accrual, mgmt, deposit], tracked) == {(VAULT.hex, HOLDER.hex): 3}
    with pytest.raises(ValueError, match='disagrees with accrual'):
        fees.fee_mints(Chain.ETHEREUM, [accrual, replace(mgmt, data='0x' + f'{42:064x}')], tracked)
    with pytest.raises(ValueError, match='Duplicate'):
        fees.fee_mints(Chain.ETHEREUM, [accrual, accrual], tracked)
