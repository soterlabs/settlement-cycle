import pytest

from settle.extract.hypersync import LogRow
from settle.normalize.sources.hypersync_psm3 import DEPOSIT, WITHDRAW, HyperSyncPsm3Source


def test_deposit_receiver_and_withdraw_user_determine_share_owner(monkeypatch):
    from settle.normalize.sources import hypersync_psm3 as mod
    source = HyperSyncPsm3Source()
    source._register("base", bytes([3]) * 20)
    monkeypatch.setattr(source, "_opening_block", lambda *a: 0)
    monkeypatch.setattr(mod.rpc, "psm3_shares", lambda *a: 10)
    monkeypatch.setattr(mod.rpc, "eth_call", lambda *a: hex(20))
    owner, other = bytes([1]) * 20, bytes([2]) * 20
    def topic(a):
        return "0x" + "00" * 12 + a.hex()
    def event(block, kind, user, receiver, shares):
        return LogRow(block, 0, 100, "0x" + "03" * 20, kind, topic(other), topic(user), topic(receiver),
                      "0x" + format(1000, "064x") + format(shares, "064x"))
    rows = [event(1, DEPOSIT, other, owner, 100), event(2, WITHDRAW, owner, other, 30),
            event(3, DEPOSIT, owner, other, 50)]
    monkeypatch.setattr(source, "_share_events", lambda *a: rows)
    assert source._load_holder_history("base", owner, pin_block=3) == [(0, 10), (1, 110), (2, 80)]
    assert source._load_pool_history("base", pin_block=3) == [(0, 20), (1, 120), (2, 90), (3, 140)]


def test_reserves_seed_opening_and_reject_outside_coverage(monkeypatch):
    from settle.domain.primes import Chain
    from settle.domain.sky_tokens import PSM3_LEG_TOKENS
    from settle.normalize.sources import hypersync_psm3 as mod
    source = HyperSyncPsm3Source()
    psm = bytes([3]) * 20
    who = mod._addr_topic(psm)
    token = PSM3_LEG_TOKENS[Chain.BASE]["USDC"]
    addr = "0x" + token.address.value.hex()
    other = mod._addr_topic(bytes([4]) * 20)
    def row(index, sender, receiver, amount):
        return LogRow(110, index, 1785542500, addr, mod.TRANSFER_TOPIC0, sender, receiver,
                      None, "0x" + format(amount, "064x"))
    incoming, outgoing, self_transfer = row(0, other, who, 50), row(1, who, other, 20), row(2, who, who, 10)
    calls = []
    monkeypatch.setattr(mod.hypersync, "block_timestamp", lambda *a: 1788220799)
    monkeypatch.setattr(mod.hypersync, "find_block_at_or_before", lambda *a: 100)
    monkeypatch.setattr(mod.rpc, "balance_of", lambda *a: 1000)
    def fetch(*args):
        calls.append(args)
        return [self_transfer, outgoing, incoming, incoming]
    monkeypatch.setattr(mod.hypersync_store, "fetch_logs", fetch)
    source._load_reserves_history("base", psm, pin_block=200)
    def balance(block):
        return source.pool_reserve_at("base", token.address.value, psm, block, decimals=6)
    assert balance(99) is None
    assert balance(100) == 1000
    assert balance(109) == 1000
    assert balance(110) == 1030
    assert balance(200) == 1030
    assert balance(201) is None
    assert calls[0][2:] == (101, 200)
    source._load_reserves_history("base", psm, pin_block=180)
    assert len(calls) == 1


def test_share_history_reloads_an_earlier_month_instead_of_returning_zero(monkeypatch):
    from settle.normalize.sources import hypersync_psm3 as mod
    source = HyperSyncPsm3Source()
    psm, owner = bytes([3]) * 20, bytes([1]) * 20
    calls = []
    monkeypatch.setattr(source, "_opening_block", lambda chain, pin: 100 if pin >= 100 else 0)
    monkeypatch.setattr(mod.rpc, "psm3_shares", lambda chain, contract, holder, block: 30 if block == 100 else 10)
    def fetch(*args):
        calls.append(args[2:])
        return []
    monkeypatch.setattr(mod.hypersync_store, "fetch_logs", fetch)
    assert source.shares_of("base", psm, owner, 200) == 30
    assert source.shares_of("base", psm, owner, 150) == 30
    assert source.shares_of("base", psm, owner, 99) == 10
    assert calls == [(101, 200), (1, 99)]


@pytest.mark.parametrize("with_deposit", [False, True])
def test_pool_history_starts_at_zero_before_deployment(monkeypatch, with_deposit):
    from settle.normalize.sources import hypersync_psm3 as mod
    source = HyperSyncPsm3Source()
    source._register("base", bytes([3]) * 20)
    monkeypatch.setattr(source, "_opening_block", lambda *args: 100)
    monkeypatch.setattr(mod.rpc, "eth_call", lambda *args: "0x")
    monkeypatch.setattr(mod.rpc, "is_contract_deployed", lambda *args: False)
    rows = [LogRow(110, 0, 100, "0x" + "03" * 20, DEPOSIT, None, None, None,
                   "0x" + format(1000, "064x") + format(500, "064x"))] if with_deposit else []
    monkeypatch.setattr(source, "_share_events", lambda *args: rows)
    assert source._load_pool_history("base", pin_block=200) == (
        [(100, 0), (110, 500)] if with_deposit else [(100, 0)]
    )


@pytest.mark.parametrize("failure", ["call", "code", "deployed_empty"])
def test_pool_opening_failure_is_not_zero_or_cached(monkeypatch, failure):
    from settle.normalize.sources import hypersync_psm3 as mod
    source = HyperSyncPsm3Source()
    source._register("base", bytes([3]) * 20)
    monkeypatch.setattr(source, "_opening_block", lambda *args: 100)

    def call(*args):
        if failure == "call":
            raise mod.rpc.RPCError("RPC unavailable")
        return "0x"

    def deployed(*args):
        if failure == "code":
            raise mod.rpc.RPCError("Code lookup unavailable")
        return True

    monkeypatch.setattr(mod.rpc, "eth_call", call)
    monkeypatch.setattr(mod.rpc, "is_contract_deployed", deployed)
    with pytest.raises(mod.rpc.RPCError):
        source._load_pool_history("base", pin_block=200)
    assert not source._pool_history
