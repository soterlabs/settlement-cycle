from datetime import UTC, date, datetime
from decimal import Decimal as D

import pytest

from settle.compute.allocation_capital import replay_history
from settle.domain.primes import Address, Chain, Prime
from settle.extract.hypersync import LogRow
from settle.normalize import allocation_bridges as bridges
from settle.normalize.allocation_capital import AssetMovement, CapitalBatch, CapitalHistory

DAY = date(2026, 8, 1)
TS = int(datetime(2026, 8, 1, tzinfo=UTC).timestamp())
HOLDER = Address(bytes.fromhex("11" * 20))
RECIPIENT = Address(bytes.fromhex("22" * 20))
MESSENGER = Address(bytes.fromhex("33" * 20))
TRANSMITTER = Address(bytes.fromhex("44" * 20))
USDC = Address(bytes.fromhex("55" * 20))


def word(n):
    return n.to_bytes(32, "big")


def atopic(a):
    return "0x" + a.value.hex().rjust(64, "0")


def fixture(monkeypatch, *, receive_amount=100_000_000, receive_domain=0):
    prime = Prime("test", b"test".ljust(32, b"\0"), DAY,
                  alm={Chain.ETHEREUM: HOLDER, Chain.BASE: RECIPIENT})
    burn = LogRow(2, 2, TS + 10, MESSENGER.hex, bridges.DEPOSIT_FOR_BURN,
                  "0x" + word(7).hex(), atopic(USDC), atopic(HOLDER),
                  "0x" + b"".join([word(100_000_000), word(int(RECIPIENT.hex, 16)),
                                      word(6), word(int(MESSENGER.hex, 16)), word(0)]).hex(), "0xburn")
    body = (bytes(4) + bytes.fromhex(atopic(USDC)[2:]) + bytes.fromhex(atopic(RECIPIENT)[2:])
            + word(receive_amount) + bytes.fromhex(atopic(HOLDER)[2:]))
    received = LogRow(3, 3, TS + 20, TRANSMITTER.hex, bridges.MESSAGE_RECEIVED,
                      atopic(HOLDER), "0x" + word(7).hex(), None,
                      "0x" + (word(receive_domain) + word(int(MESSENGER.hex, 16)) + word(96)
                               + word(len(body)) + body + bytes(28)).hex(), "0xreceive")
    monkeypatch.setattr(bridges, "_view", lambda chain, address, signature, block:
                        atopic(TRANSMITTER) if signature == "localMessageTransmitter()"
                        else "0x" + word(0 if chain == Chain.ETHEREUM else 6).hex())
    monkeypatch.setattr(bridges.hypersync_store, "fetch_logs", lambda chain, *a, **kw:
                        [received] if chain == "base" else [])
    batches = [
        CapitalBatch("draw", DAY, TS, "ethereum", 1,
                     (AssetMovement("source_cash", D(0), D(100)),), D(100)),
        CapitalBatch("ethereum:0xburn", DAY, TS + 10, "ethereum", 2,
                     (AssetMovement("source_cash", D(100), D(-100)),)),
        CapitalBatch("base:0xreceive", DAY, TS + 20, "base", 3,
                     (AssetMovement("destination_cash", D(0), D(100)),)),
    ]
    return prime, batches, [(Chain.ETHEREUM, burn)]


def test_cctp_moves_basis_without_creating_new_borrowing(monkeypatch):
    prime, batches, burns = fixture(monkeypatch)
    linked = bridges.link_cctp(prime, {Chain.ETHEREUM: 10, Chain.BASE: 10}, batches, burns)
    replay = replay_history(CapitalHistory(tuple(linked), {}, {}), DAY, DAY)
    assert replay.ledger.account("destination_cash").borrowed == D(100)
    assert replay.ledger.drawn == D(100)
    assert not replay.unmatched_receipts
    assert not replay.unmatched_outflows


def test_cctp_rejects_a_different_message_amount(monkeypatch):
    prime, batches, burns = fixture(monkeypatch, receive_amount=99_000_000)
    with pytest.raises(ValueError, match="body mismatch"):
        bridges.link_cctp(prime, {Chain.ETHEREUM: 10, Chain.BASE: 10}, batches, burns)


def test_equal_nonce_and_amount_from_other_domain_are_not_matched(monkeypatch):
    prime, batches, burns = fixture(monkeypatch, receive_domain=1)
    linked = bridges.link_cctp(prime, {Chain.ETHEREUM: 10, Chain.BASE: 10}, batches, burns)
    replay = replay_history(CapitalHistory(tuple(linked), {}, {}), DAY, DAY)
    assert replay.ledger.account("destination_cash").borrowed == 0
    assert "destination_cash" in replay.uncertain_accounts
    assert sum((a.borrowed for a in replay.ledger.accounts.values()), D(0)) == D(100)
