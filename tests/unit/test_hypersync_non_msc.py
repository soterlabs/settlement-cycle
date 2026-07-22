"""Unit tests for the HyperSync non_msc source — decode helpers, the pure
Art×Δr_true integration, and the surplus/PSM/RWA tx-classification."""

from __future__ import annotations

import math
from decimal import Decimal

from settle.normalize.sources import hypersync_non_msc as H


# ── topic0 constants + decode helpers ───────────────────────────────────────

def test_note_selectors_match_signatures():
    # frob/grab constants are hard-coded; the module self-asserts them at import,
    # so a successful import already proves agreement. Re-check the others.
    assert H._FOLD.startswith("0xb65337df")
    assert H._MOVE.startswith("0xbb35783b")
    assert H._SUCK.startswith("0xf24e23eb")
    assert H._FILE3.startswith("0x1a0b287e")


def test_ilk_topic_roundtrip():
    assert H._ilk_topic("ETH-C") == "0x455448" + "2d43" + "00" * 27
    assert H._ilk_from_topic(H._ilk_topic("RWA002-A")) == "RWA002-A"


def test_addr_topic():
    assert H._addr_topic("0xAbCd") == "0x" + "0" * 24 + "abcd"


def test_word_and_note_payload():
    # data = [w0][w1][w2]
    data = "0x" + "00" * 31 + "05" + "00" * 31 + "07" + "ff" * 32
    assert H._word(data, 0) == 5
    assert H._word(data, 1) == 7
    # note payload: [0x20 offset][length][calldata]; value at calldata byte 68
    calldata = "aa" * 4 + "11" * 32 + "22" * 32 + ("00" * 31 + "2a")  # sig,a1,a2,a3=42
    payload = "0x" + "00" * 31 + "20" + "00" * 31 + "64" + calldata
    assert H._note_calldata_word(payload, 68) == 42


def test_sint_twos_complement():
    assert H._sint("0x" + "00" * 31 + "05") == 5
    assert H._sint("0x" + "ff" * 32) == -1


# ── pure accrual integration ────────────────────────────────────────────────

def test_integrate_fee_none_when_never_folded():
    # Art before start but no fold / no duty → no accrual basis.
    arts = [(1, 0, 100, 1000 * 10**18)]
    assert H._integrate_fee(arts, [], [], start_ts=1000, end_ts=2000) is None


def test_integrate_fee_constant_art_and_duty():
    RAY = 10**27
    start, end = 1000, 1100
    # one prior fold (rho0 = 990, rate0 = RAY), one prior duty file.
    duty = int(1.0000001 * RAY)               # per-second rate
    arts = [(1, 0, 500, 2000 * 10**18)]       # 2000 units, before start
    folds = [(2, 0, 990, 0)]                   # rate delta 0 → rate0 = RAY
    duties = [(3, 0, 500, duty)]
    got = H._integrate_fee(arts, folds, duties, start, end)
    # closed form: art_units * rate0/RAY * (g^(end-rho0) - g^(start-rho0))
    g = duty / 1e27
    expected = 2000 * (g ** (end - 990) - g ** (start - 990))
    assert math.isclose(float(got), expected, rel_tol=1e-9)


def test_integrate_fee_weights_in_month_art_change():
    RAY = 10**27
    start, end = 1000, 1200
    duty = int(1.0000002 * RAY)
    # start with 1000 units; at t=1100 draw +500 → 1500 units for the 2nd half.
    arts = [(1, 0, 500, 1000 * 10**18), (10, 0, 1100, 500 * 10**18)]
    folds = [(2, 0, 990, 0)]
    duties = [(3, 0, 500, duty)]
    got = float(H._integrate_fee(arts, folds, duties, start, end))
    g = duty / 1e27
    seg1 = 1000 * (g ** (1100 - 990) - g ** (start - 990))
    seg2 = 1500 * (g ** (end - 990) - g ** (1100 - 990))
    assert math.isclose(got, seg1 + seg2, rel_tol=1e-9)


# ── savings accrual (chi-boundary interpolation) ────────────────────────────

def test_accrue_savings_whole_intervals_sum_exactly():
    # Three drips, all intervals fully inside [1000, 2000): diffs summed as ints.
    ev = [(900, 100, 0), (1200, 110, 5 * 10**18), (1600, 121, 7 * 10**18)]
    # interval (900,1200] straddles start=1000; (1200,1600] fully inside.
    got = H._accrue_savings(ev, start_ts=1200, end_ts=2000)
    assert got == Decimal(7 * 10**18)          # only the fully-inside interval


def test_accrue_savings_geometric_boundary_split():
    RAY = 10**27
    # One interval (0,100] with chi growing geometrically; month starts at t=40.
    # supply implied by diff = shares*(chi_b-chi_a); split by geometric chi.
    chi_a, chi_b = RAY, int(RAY * 1.00001)
    diff = 5000 * 10**18
    ev = [(0, chi_a, 0), (100, chi_b, diff)]
    got = float(H._accrue_savings(ev, start_ts=40, end_ts=1000))
    g = chi_b / chi_a
    chi_40 = chi_a * g ** (40 / 100)
    expected = diff * (chi_b - chi_40) / (chi_b - chi_a)
    assert math.isclose(got, expected, rel_tol=1e-9)


def test_accrue_savings_time_fraction_when_no_chi():
    # acc=None (pot suck) → straddle split by elapsed-time fraction.
    diff = 3000 * 10**45
    ev = [(0, None, 0), (100, None, diff)]
    got = float(H._accrue_savings(ev, start_ts=25, end_ts=1000))
    assert math.isclose(got, diff * (100 - 25) / 100, rel_tol=1e-12)


def test_accrue_savings_end_straddle_adds_in_month_portion():
    # Interval (900,1100] straddles end=1000: only the [900,1000] portion counts.
    diff = 2000 * 10**18
    ev = [(900, None, 0), (1100, None, diff)]
    got = float(H._accrue_savings(ev, start_ts=0, end_ts=1000))
    assert math.isclose(got, diff * (1000 - 900) / (1100 - 900), rel_tol=1e-12)


# ── surplus / PSM / RWA classification (fake transport) ─────────────────────

def _resp(logs, to_block):
    """HyperSync-shaped response: all logs in one group, complete range."""
    blocks = {(l["block_number"]): l["_ts"] for l in logs}
    return {
        "archive_height": to_block + 10_000,
        "next_block": to_block + 1,
        "data": [{
            "blocks": [{"number": bn, "timestamp": ts} for bn, ts in blocks.items()],
            "logs": [{k: v for k, v in l.items() if k != "_ts"} for l in logs],
        }],
    }


class _FakePost:
    """Routes HyperSync queries by (address, topic0) to canned rows."""

    def __init__(self, moves, psm_burn_tx, rwa_burn_tx, ts):
        self.moves, self.psm, self.rwa, self.ts = moves, psm_burn_tx, rwa_burn_tx, ts

    def __call__(self, url, json, headers, timeout):
        sel = json["logs"][0]
        addr = [a.lower() for a in sel["address"]]
        t0 = sel["topics"][0][0]
        tb = json["to_block"] - 1
        logs = []
        if H._VAT in addr and t0 == H._MOVE:
            for i, (tx, rad) in enumerate(self.moves):
                logs.append({
                    "block_number": 100 + i, "log_index": i, "_ts": self.ts,
                    "address": H._VAT, "topic0": H._MOVE,
                    "topic1": H._addr_topic(H._DAI_JOIN), "topic2": H._addr_topic(H._VOW),
                    "topic3": "0x" + f"{rad:064x}", "data": "0x", "transaction_hash": tx,
                })
        elif H._DAI in addr and t0 == H._TRANSFER:
            # which jar? topic1 is the sender set
            senders = sel["topics"][1]
            is_psm = H._addr_topic(H._LITE_PSM_JAR) in senders
            tx = self.psm if is_psm else self.rwa
            if tx is not None:
                logs.append({
                    "block_number": 100, "log_index": 0, "_ts": self.ts,
                    "address": H._DAI, "topic0": H._TRANSFER,
                    "topic1": senders[0], "topic2": H._addr_topic(H._ZERO),
                    "topic3": None, "data": "0x" + f"{10**18:064x}", "transaction_hash": tx,
                })
        return _FakeResp(_resp(logs, tb))


class _FakeResp:
    def __init__(self, payload):
        self.payload, self.ok, self.status_code, self.text = payload, True, 200, ""

    def json(self):
        return self.payload


def test_surplus_classification(monkeypatch):
    # hypersync.query_logs reads ENVIO_API_TOKEN before calling `post`; set it so
    # the token guard passes while the injected mock transport is what actually
    # answers the request.
    monkeypatch.setenv("ENVIO_API_TOKEN", "test")
    RAD = 10**45
    ts = 1_781_000_000  # inside the window below
    moves = [
        ("0xpsm", 10_000_000 * RAD),    # coincides with PSM jar burn → excluded
        ("0xrwa", 5_000 * RAD),         # coincides with RWA jar → rwa_void
        ("0xsurplus", 157_000 * RAD),   # neither → surplus return
    ]
    post = _FakePost(moves, psm_burn_tx="0xpsm", rwa_burn_tx="0xrwa", ts=ts)
    src = H.HyperSyncNonMscSource(post=post)
    rows = src._surplus_and_rwa(start_ts=ts - 100, end_ts=ts + 100, fb=1, tb=200)
    by = {r["stream"]: r for r in rows}
    surplus = [r for r in rows if r["stream"] == "income:surplus_return"]
    assert len(surplus) == 1
    assert surplus[0]["amount"] == Decimal("157000")
    assert by["income:rwa_void"]["amount"] == Decimal("5000")
    # the PSM-coincident move must NOT appear as a surplus return
    assert all(r["amount"] != Decimal("10000000") for r in surplus)
