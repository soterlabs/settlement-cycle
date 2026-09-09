"""Unit tests for the HyperSync SBE source — event decoding with a mock transport."""

from __future__ import annotations

from decimal import Decimal

import pytest

from settle.normalize.sources import hypersync_sbe as S

D = Decimal
WAD = 10**18
RAD = 10**45

_C = {
    "MCD_SPLIT": "0xBF7111F13386d23cb2Fba5A538107A73f6872bCF",
    "MCD_FLAP": "0x374D9c3d5134052Bc558F432Afa1df6575f07407",
    "MCD_KICK": "0xD889477102e8C4A857b78Fcc2f134535176Ec1Fc",
    "REWARDS_DIST_LSSKY_SKY": "0x675671A8756dDb69F7254AFB030865388Ef699Ee",
    "MCD_VEST_SKY_TREASURY": "0x67eaDb3288cceDe034cE95b0511DCc65cf630bB6",
    "REWARDS_LSSKY_USDS": "0x38E4254bD82ED5Ee97CD1C4278FAae748d998865",
}


def _w(n: int) -> str:
    return hex(n)[2:].rjust(64, "0")


def _what(s: str) -> str:
    return "0x" + s.encode().ljust(32, b"\0").hex()


def _log(addr: str, topic0: str, data_words: list[int], block: int, idx: int, tx: str,
         topic1: str | None = None) -> dict:
    return {
        "block_number": block, "log_index": idx, "address": addr.lower(),
        "topic0": topic0, "topic1": topic1, "topic2": None, "topic3": None,
        "data": "0x" + "".join(_w(x) for x in data_words), "transaction_hash": tx,
    }


@pytest.fixture(autouse=True)
def _envio_token(monkeypatch):
    """hs.query_logs resolves the bearer token before touching the (mocked)
    transport — every HyperSync unit test needs the variable set."""
    monkeypatch.setenv("ENVIO_API_TOKEN", "test-token")


class _Resp:
    ok = True
    status_code = 200
    text = ""

    def __init__(self, payload: dict):
        self._p = payload

    def json(self) -> dict:
        return self._p


def _transport(logs: list[dict], blocks: list[dict], to_block: int):
    def post(url, json, headers, timeout):
        return _Resp({"archive_height": to_block + 10, "next_block": to_block + 1,
                      "data": [{"logs": logs, "blocks": blocks}]})
    return post


def test_topics_match_dss_flappers_events():
    # Observed on mainnet (Splitter 0xBF71…, 2026-08): Kick(uint256,uint256,uint256)
    assert S._KICK.startswith("0xe6dde59c")
    assert S._KICK == S._evt("Kick(uint256,uint256,uint256)")
    assert S._EXEC == S._evt("Exec(uint256,uint256)")
    assert S._DISTRIBUTE == S._evt("Distribute(uint256)")


def test_decodes_kicks_param_changes_and_distributions():
    burn_old = 1 * WAD
    burn_new = 55 * WAD // 100
    logs = [
        # legacy kick: 6,000 USDS all to the flapper, 100k SKY bought
        _log(_C["MCD_FLAP"], S._EXEC, [6000 * WAD, 100_000 * WAD], 10, 1, "0xk1"),
        _log(_C["MCD_SPLIT"], S._KICK, [6000 * RAD, 6000 * WAD, 0], 10, 2, "0xk1"),
        # the spell: burn → 55%, hop → 3748, vest yank 15 / init 16, dist vestId 16
        _log(_C["MCD_VEST_SKY_TREASURY"], S._VEST_YANK, [1_787_000_000], 20, 1, "0xspell", topic1=_w(15)),
        _log(_C["MCD_VEST_SKY_TREASURY"], S._VEST_INIT, [], 20, 2, "0xspell", topic1=_w(16)),
        _log(_C["REWARDS_DIST_LSSKY_SKY"], S._FILE_U, [16], 20, 3, "0xspell", topic1=_what("vestId")),
        _log(_C["MCD_SPLIT"], S._FILE_U, [3748], 20, 4, "0xspell", topic1=_what("hop")),
        _log(_C["REWARDS_LSSKY_USDS"], S._REWARDS_DURATION, [3748], 20, 5, "0xspell"),
        _log(_C["MCD_SPLIT"], S._FILE_U, [burn_new], 20, 6, "0xspell", topic1=_what("burn")),
        _log(_C["MCD_KICK"], S._FILE_I, [(1 << 256) - 200_000_000 * RAD], 20, 7, "0xspell", topic1=_what("khump")),
        _log(_C["REWARDS_DIST_LSSKY_SKY"], S._DISTRIBUTE, [467_238 * WAD], 20, 8, "0xspell"),
        # new-regime kick: 3,300 to the flapper, 2,700 to the farm, 50k SKY bought
        _log(_C["MCD_FLAP"], S._EXEC, [3300 * WAD, 50_000 * WAD], 30, 1, "0xk2"),
        _log(_C["MCD_SPLIT"], S._KICK, [6000 * RAD, 3300 * WAD, 2700 * WAD], 30, 2, "0xk2"),
    ]
    blocks = [{"number": b, "timestamp": hex(1_000 * b)} for b in (10, 20, 30)]
    src = S.HyperSyncSbeSource(_C, post=_transport(logs, blocks, to_block=40))
    act = src.activity(
        "2026-08", 1, 40, from_ts=0, to_ts=40_000,
        burn_at_start=D(burn_old) / D(WAD), hop_at_start=13787,
    )

    assert len(act.kicks) == 2
    k1, k2 = act.kicks
    assert (k1.tot, k1.lot, k1.pay, k1.bought, k1.burn, k1.hop) == (D(6000), D(6000), D(0), D(100_000), D(1), 13787)
    assert (k2.tot, k2.lot, k2.pay, k2.bought, k2.burn, k2.hop) == (D(6000), D(3300), D(2700), D(50_000), D("0.55"), 3748)
    assert k2.ts == 30_000

    changes = {(c.contract, c.what): c.value for c in act.param_changes}
    assert changes[("MCD_SPLIT", "hop")] == 3748
    assert changes[("MCD_SPLIT", "burn")] == D("0.55")
    assert changes[("REWARDS_LSSKY_USDS", "rewardsDuration")] == 3748
    assert changes[("REWARDS_DIST_LSSKY_SKY", "vestId")] == 16
    assert changes[("MCD_VEST_SKY_TREASURY", "vest.init (id)")] == 16
    assert changes[("MCD_VEST_SKY_TREASURY", "vest.yank (id)")] == 15
    assert changes[("MCD_KICK", "khump")] == D(-200_000_000)

    assert [d.amount for d in act.distributions] == [D(467_238)]


def test_exec_kick_join_is_positional_within_a_tx():
    """Two kicks in one tx (hop = 0 / multicall keeper): each Kick takes the
    Exec that precedes it (Splitter.kick emits Kick last), never the tx-wide sum."""
    logs = [
        _log(_C["MCD_FLAP"], S._EXEC, [3300 * WAD, 50_000 * WAD], 10, 1, "0xmulti"),
        _log(_C["MCD_SPLIT"], S._KICK, [6000 * RAD, 3300 * WAD, 2700 * WAD], 10, 2, "0xmulti"),
        _log(_C["MCD_FLAP"], S._EXEC, [3300 * WAD, 40_000 * WAD], 10, 5, "0xmulti"),
        _log(_C["MCD_SPLIT"], S._KICK, [6000 * RAD, 3300 * WAD, 2700 * WAD], 10, 6, "0xmulti"),
    ]
    blocks = [{"number": 10, "timestamp": hex(10_000)}]
    src = S.HyperSyncSbeSource(_C, post=_transport(logs, blocks, to_block=20))
    act = src.activity("2026-08", 1, 20, from_ts=0, to_ts=1, burn_at_start=D("0.55"), hop_at_start=0)
    assert [k.bought for k in act.kicks] == [D(50_000), D(40_000)]


def test_exec_without_kick_is_loud():
    logs = [_log(_C["MCD_FLAP"], S._EXEC, [3300 * WAD, 50_000 * WAD], 10, 2, "0xstray")]
    blocks = [{"number": 10, "timestamp": hex(10_000)}]
    src = S.HyperSyncSbeSource(_C, post=_transport(logs, blocks, to_block=20))
    with pytest.raises(ValueError, match="no matching Splitter Kick"):
        src.activity("2026-08", 1, 20, from_ts=0, to_ts=1, burn_at_start=D("0.55"), hop_at_start=3748)


def test_file_events_are_scaled_or_flagged_raw_and_addresses_decoded():
    logs = [
        _log(_C["MCD_FLAP"], S._FILE_U, [98 * WAD // 100], 10, 1, "0xf", topic1=_what("want")),
        _log(_C["MCD_VEST_SKY_TREASURY"], S._FILE_U, [3 * WAD], 10, 2, "0xf", topic1=_what("cap")),
        _log(_C["MCD_SPLIT"], S._FILE_U, [12345], 10, 3, "0xf", topic1=_what("mystery")),
        _log(_C["MCD_SPLIT"], S._FILE_A, [int(_C["MCD_FLAP"], 16)], 10, 4, "0xf", topic1=_what("flapper")),
    ]
    blocks = [{"number": 10, "timestamp": hex(10_000)}]
    src = S.HyperSyncSbeSource(_C, post=_transport(logs, blocks, to_block=20))
    act = src.activity("2026-08", 1, 20, from_ts=0, to_ts=1, burn_at_start=D("0.55"), hop_at_start=3748)
    got = {(c.contract, c.what): c.value for c in act.param_changes}
    assert got[("MCD_FLAP", "want")] == D("0.98")
    assert got[("MCD_VEST_SKY_TREASURY", "cap")] == D(3)
    assert got[("MCD_SPLIT", "mystery (raw)")] == D(12345)
    assert got[("MCD_SPLIT", "flapper")] == _C["MCD_FLAP"].lower()


def test_kick_without_exec_is_loud():
    logs = [_log(_C["MCD_SPLIT"], S._KICK, [6000 * RAD, 3300 * WAD, 2700 * WAD], 10, 1, "0xk")]
    blocks = [{"number": 10, "timestamp": hex(10_000)}]
    src = S.HyperSyncSbeSource(_C, post=_transport(logs, blocks, to_block=20))
    with pytest.raises(ValueError, match="no Exec event precedes"):
        src.activity("2026-08", 1, 20, from_ts=0, to_ts=1, burn_at_start=D("0.55"), hop_at_start=3748)


def test_month_block_range_uses_prior_eod_plus_one(monkeypatch):
    calls: list[int] = []

    def fake_find(chain: str, ts: int) -> int:
        calls.append(ts)
        return {1_785_542_399: 25656292, 1_788_220_799: 25878704}[ts]

    monkeypatch.setattr(S.hs, "find_block_at_or_before", fake_find)

    class M:
        year, month = 2026, 8

    # "now" is 2026-09-09: August is closed -> exact EoD, partial=False
    assert S.month_block_range(M(), now=1_789_000_000) == (
        25656293, 25878704, 1_785_542_400, 1_788_220_799, False,
    )
    assert calls == [1_785_542_399, 1_788_220_799]


def test_month_block_range_refuses_an_open_month_unless_allowed(monkeypatch):
    monkeypatch.setattr(S.hs, "find_block_at_or_before",
                        lambda chain, ts: {1_788_220_799: 25878704, 1_790_812_799: 25_900_000}[ts])
    monkeypatch.setattr(S.hs, "block_timestamp", lambda chain, b: 1_789_000_000)

    class M:
        year, month = 2026, 9

    with pytest.raises(S.MonthNotClosedError, match="--allow-partial"):
        S.month_block_range(M(), now=1_789_000_000)
    got = S.month_block_range(M(), allow_partial=True, now=1_789_000_000)
    # to_block is the head clamp, to_ts its REAL timestamp, partial flagged
    assert got == (25878705, 25_900_000, 1_788_220_800, 1_789_000_000, True)
