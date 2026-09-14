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
