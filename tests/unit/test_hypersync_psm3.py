from settle.extract.hypersync import LogRow
from settle.normalize.sources.hypersync_psm3 import DEPOSIT, WITHDRAW, HyperSyncPsm3Source


def test_deposit_receiver_and_withdraw_user_determine_share_owner(monkeypatch):
    source = HyperSyncPsm3Source()
    owner, other = bytes([1]) * 20, bytes([2]) * 20
    def topic(a):
        return "0x" + "00" * 12 + a.hex()
    def event(block, kind, user, receiver, shares):
        return LogRow(block, 0, 100, "0x" + "03" * 20, kind, topic(other), topic(user), topic(receiver),
                      "0x" + format(1000, "064x") + format(shares, "064x"))
    rows = [event(1, DEPOSIT, other, owner, 100), event(2, WITHDRAW, owner, other, 30),
            event(3, DEPOSIT, owner, other, 50)]
    monkeypatch.setattr(source, "_share_events", lambda *a: rows)
    assert source._load_holder_history("base", owner, pin_block=3) == [(1, 100), (2, 70)]
    assert source._load_pool_history("base", pin_block=3) == [(1, 100), (2, 70), (3, 120)]


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
