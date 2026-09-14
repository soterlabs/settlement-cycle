from dataclasses import replace
from datetime import date

import pytest

from settle.extract.hypersync import LogRow
from settle.normalize.sources import hypersync_venue_events as events

DIST, TOKEN, HOLDER, WRAPPER = (bytes([n]) * 20 for n in range(1, 5))


def log(index, address, topic, topic1, topic2, words, tx="0xabc"):
    return LogRow(10, index, 1785542400, "0x" + address.hex(), topic,
                  events._addr_topic(topic1), events._addr_topic(topic2), None,
                  "0x" + "".join(format(w, "064x") for w in words), tx)


def test_merkl_wrapper_and_direct_receipts_are_disjoint(monkeypatch):
    rows = [
        log(0, DIST, events.CLAIMED, HOLDER, WRAPPER, [50]),
        log(1, TOKEN, events.MINT, WRAPPER, HOLDER, [50, 0, 10**27]),
        log(2, DIST, events.CLAIMED, HOLDER, TOKEN, [999]),
        log(3, DIST, events.CLAIMED, HOLDER, TOKEN, [999]),
        log(4, TOKEN, events.TRANSFER_TOPIC0, DIST, HOLDER, [70]),
        # Claim routed elsewhere: no matching receipt in this tx.
        log(5, DIST, events.CLAIMED, HOLDER, TOKEN, [300], tx="0xother"),
    ]
    monkeypatch.setattr(events, "window_logs", lambda *a, **k: rows)
    assert events.merkl_raw("ethereum", DIST, TOKEN, HOLDER,
                            date(2026, 8, 1), date(2026, 8, 31), 10) == 120


def test_window_requires_transaction_hash_and_deduplicates(monkeypatch):
    row = log(0, DIST, events.CLAIMED, HOLDER, TOKEN, [1])
    monkeypatch.setattr(events, "_default_start_block", lambda *a: 0)
    monkeypatch.setattr(events.hypersync_store, "fetch_logs", lambda *a, **k: [row, row])
    assert events.window_logs("ethereum", [], date(2026, 8, 1), 10, transactions=True) == [row]
    monkeypatch.setattr(events.hypersync_store, "fetch_logs",
                        lambda *a, **k: [replace(row, transaction_hash=None)])
    with pytest.raises(ValueError, match="Transaction hash"):
        events.window_logs("ethereum", [], date(2026, 8, 1), 10, transactions=True)


def test_centrifuge_keeps_gross_assets_and_shares_integers(monkeypatch):
    rows = [log(0, TOKEN, events.DEPOSIT, HOLDER, HOLDER, [10**30, 10**28]),
            log(1, TOKEN, events.WITHDRAW, DIST, HOLDER, [10**29, 10**27])]
    monkeypatch.setattr(events, "window_logs", lambda *a, **k: rows)
    frame = events.centrifuge_flows("ethereum", TOKEN, HOLDER, date(2026, 8, 1), 10)
    assert frame.iloc[0].to_dict() == {
        "block_date": date(2026, 8, 1), "assets_in_raw": 10**30, "assets_out_raw": 10**29,
        "shares_in_raw": 10**28, "shares_out_raw": 10**27,
    }


def test_bad_abi_data_fails():
    with pytest.raises(ValueError, match="ABI words"):
        events.uint_words("0x01", 2)
