"""Unit tests for the self-verifying HyperSync position-balance hybrid."""

from __future__ import annotations

from settle.extract.hypersync import LogRow
from settle.normalize.sources.hypersync_position_balance import (
    HyperSyncPositionBalanceSource,
)

_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_TOKEN = bytes.fromhex("aa" * 20)
_H = bytes.fromhex("b6dd7ae22c9922afee0642f9ac13e58633f715a2")
_OTHER = bytes.fromhex("11" * 20)


def _topic(b: bytes) -> str:
    return "0x" + b.hex().rjust(64, "0")


def _xfer(block, li, frm, to, value) -> LogRow:
    return LogRow(block, li, 1_700_000_000 + block, "0x" + _TOKEN.hex(),
                  _TRANSFER, _topic(frm), _topic(to), None, "0x" + format(value, "064x"))


class _RPC:
    """Fake RPC IPositionBalanceSource; records calls, returns a scripted balance."""
    def __init__(self, value_fn):
        self._value_fn = value_fn
        self.calls = 0

    def balance_at(self, chain, token, holder, block):
        self.calls += 1
        return self._value_fn(block)


# transfers: +100 in, -30 out  → Σ = 70
_ROWS = [_xfer(10, 0, _OTHER, _H, 100), _xfer(20, 0, _H, _OTHER, 30)]


def test_non_rebasing_probes_once_then_events_only():
    rpc = _RPC(lambda block: 70)                    # RPC agrees with Σtransfers
    src = HyperSyncPositionBalanceSource(
        rpc, fetch_logs=lambda c, s, f, t: list(_ROWS),
        aave_source=_AaveStub(70, is_atoken=False),  # plain ERC-20 → not an aToken
    )
    # First call: probes RPC (+1), classifies "events" (70==70), returns 70
    assert src.balance_at("ethereum", _TOKEN, _H, 25) == 70
    assert rpc.calls == 1
    # Subsequent calls: events only, no more RPC
    assert src.balance_at("ethereum", _TOKEN, _H, 25) == 70
    assert src.balance_at("ethereum", _TOKEN, _H, 25) == 70
    assert rpc.calls == 1


def test_rebasing_classified_rpc_and_always_delegates():
    rpc = _RPC(lambda block: 85)                    # rebased > Σtransfers(70) → rebasing
    src = HyperSyncPositionBalanceSource(rpc, fetch_logs=lambda c, s, f, t: list(_ROWS))
    assert src.balance_at("ethereum", _TOKEN, _H, 25) == 85   # returns RPC (trusted)
    assert src.balance_at("ethereum", _TOKEN, _H, 25) == 85
    assert rpc.calls == 2                            # every call hits RPC (classified rpc)


def test_zero_balance_does_not_classify_prematurely():
    # If RPC balance is 0 at the probe, Σtransfers==0 too — tells us nothing.
    # Must NOT classify (a later non-zero block could reveal rebasing).
    state = {"v": 0}
    rpc = _RPC(lambda block: state["v"])
    src = HyperSyncPositionBalanceSource(rpc, fetch_logs=lambda c, s, f, t: list(_ROWS))
    assert src.balance_at("ethereum", _TOKEN, _H, 5) == 0     # probe, balance 0, no verdict
    # Now RPC reports a rebased 85 (> Σ70) → still unclassified → re-probes → classifies rpc
    state["v"] = 85
    assert src.balance_at("ethereum", _TOKEN, _H, 25) == 85
    assert src.balance_at("ethereum", _TOKEN, _H, 25) == 85
    assert rpc.calls == 3                            # 2 probes + 1 post-classification


class _AaveStub:
    def __init__(self, value, is_atoken=True):
        # ``value`` may be a constant or a per-block callable.
        self._value_fn = value if callable(value) else (lambda b: value)
        self.calls = 0
        self._is_atoken = is_atoken
    def is_atoken(self, chain, token, block):
        return self._is_atoken
    def reconstruct_balance(self, chain, token, holder, block):
        self.calls += 1
        return self._value_fn(block)


def test_rebasing_atoken_uses_aave_reconstruction():
    # RPC (rebased) = 85; Σtransfers = 70 (rebasing); Aave reconstruction = 85 → "aave".
    rpc = _RPC(lambda block: 85)
    aave = _AaveStub(85)
    src = HyperSyncPositionBalanceSource(
        rpc, fetch_logs=lambda c, s, f, t: list(_ROWS), aave_source=aave)
    assert src.balance_at("ethereum", _TOKEN, _H, 25) == 85   # probe: rpc, classify aave
    assert src.balance_at("ethereum", _TOKEN, _H, 25) == 85   # served from aave
    assert src.balance_at("ethereum", _TOKEN, _H, 25) == 85
    assert rpc.calls == 1          # only the probe hit RPC
    assert aave.calls == 3         # 1 verify + 2 served


def test_rebasing_falls_back_to_rpc_if_aave_mismatches():
    # Aave reconstruction disagrees with RPC → do NOT trust it → "rpc".
    rpc = _RPC(lambda block: 85)
    aave = _AaveStub(999)          # wrong → must not be trusted
    src = HyperSyncPositionBalanceSource(
        rpc, fetch_logs=lambda c, s, f, t: list(_ROWS), aave_source=aave)
    assert src.balance_at("ethereum", _TOKEN, _H, 25) == 85
    assert src.balance_at("ethereum", _TOKEN, _H, 25) == 85
    assert rpc.calls == 2          # every call hits RPC (classified rpc)


def test_atoken_matching_at_probe_is_not_pinned_to_events():
    # Regression: a rebasing aToken first probed with NO accrued interest has
    # Σtransfers == balanceOf at the probe (70 == 70). Classifying it "events"
    # would return a stale 70 at every later block; the is_atoken gate must
    # route it to the Aave reconstruction instead.
    rpc = _RPC(lambda block: 70 if block <= 25 else 85)   # accrues after probe
    aave = _AaveStub(lambda block: 70 if block <= 25 else 85, is_atoken=True)
    src = HyperSyncPositionBalanceSource(
        rpc, fetch_logs=lambda c, s, f, t: list(_ROWS), aave_source=aave)
    assert src.balance_at("ethereum", _TOKEN, _H, 25) == 70    # probe → classify aave
    assert src._verdict[("ethereum", _TOKEN.hex())] == "aave"  # NOT "events"
    assert src.balance_at("ethereum", _TOKEN, _H, 100) == 85   # accrued interest, from aave


def test_events_balance_dedups_self_transfer():
    rpc = _RPC(lambda block: 0)
    src = HyperSyncPositionBalanceSource(rpc, fetch_logs=lambda c, s, f, t: [
        _xfer(10, 0, _H, _H, 5), _xfer(10, 0, _H, _H, 5),   # same (block,logIndex) twice
    ])
    # self-transfer nets 0, counted once — internal helper
    assert src._events_balance("ethereum", _TOKEN, _H, 25) == 0
