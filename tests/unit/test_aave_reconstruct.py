"""Unit tests for wei-exact Aave/SparkLend aToken reconstruction."""

from __future__ import annotations

from settle.extract import aave_reconstruct as ar
from settle.extract.aave_reconstruct import BT_T0, BURN_T0, MINT_T0, RAY, RDU_T0
from settle.extract.hypersync import LogRow

_ATOKEN = bytes.fromhex("cc" * 20)
_POOL = bytes.fromhex("dd" * 20)
_RESERVE = bytes.fromhex("ee" * 20)
_H = bytes.fromhex("b6dd7ae22c9922afee0642f9ac13e58633f715a2")
_OTHER = bytes.fromhex("11" * 20)


def _topic(b: bytes) -> str:
    return "0x" + b.hex().rjust(64, "0")


def _data(*words: int) -> str:
    return "0x" + "".join(format(w, "064x") for w in words)


def _log(block, li, t0, t1, t2, data):
    return LogRow(block, li, 0, "0x" + _ATOKEN.hex(), t0, _topic(t1), _topic(t2), None, data)


def test_ray_math_half_up():
    assert ar.ray_div(1000, RAY) == 1000
    assert ar.ray_mul(1000, 2 * RAY) == 2000
    # half-up rounding
    assert ar.ray_mul(1, RAY // 2) == 1          # (1*5e26 + 5e26)//1e27 = 1
    assert ar.ray_mul(1, RAY // 2 - 1) == 0


def test_scaled_balance_from_events():
    # Mint 1000 @ index RAY (scaledΔ=+1000); Burn 300 @ index RAY (scaledΔ=-300);
    # BalanceTransfer 50 scaled in (to==holder).
    rows = [
        _log(10, 0, MINT_T0, _OTHER, _H, _data(1000, 0, RAY)),        # value,balInc,index
        _log(20, 0, BURN_T0, _H, _OTHER, _data(300, 0, RAY)),
        _log(30, 0, BT_T0, _OTHER, _H, _data(50, RAY)),              # value(scaled),index
    ]
    scaled = ar.scaled_balance_at("ethereum", _ATOKEN, _H, 100,
                                  fetch_logs=lambda c, s, f, t: list(rows))
    assert scaled == 1000 - 300 + 50


def test_scaled_dedups_and_ignores_out_transfers():
    rows = [
        _log(10, 0, MINT_T0, _OTHER, _H, _data(1000, 0, RAY)),
        _log(10, 0, MINT_T0, _OTHER, _H, _data(1000, 0, RAY)),        # dup (same block,logIndex)
        _log(40, 0, BT_T0, _H, _OTHER, _data(200, RAY)),             # from==holder → -200
    ]
    scaled = ar.scaled_balance_at("ethereum", _ATOKEN, _H, 100,
                                  fetch_logs=lambda c, s, f, t: list(rows))
    assert scaled == 1000 - 200


def test_normalized_income_linear_accrual():
    # last RDU: index=RAY, rate=RAY (100%/yr). dt = 1 year → linear = 2·RAY → NI = 2·RAY.
    rdu = _log(50, 0, RDU_T0, _RESERVE, bytes(20),
               _data(RAY, 0, 0, RAY, 0))  # liqRate, stable, var, liqIndex, varIndex
    ts = {50: 1_000_000, 100: 1_000_000 + ar.SECONDS_PER_YEAR}
    ni = ar.normalized_income("ethereum", _POOL, _RESERVE, 100,
                              fetch_logs=lambda c, s, f, t: [rdu],
                              block_ts=lambda c, b: ts[b])
    assert ni == 2 * RAY


def test_normalized_income_no_accrual_when_same_block_ts():
    rdu = _log(50, 0, RDU_T0, _RESERVE, bytes(20), _data(RAY, 0, 0, 3 * RAY, 0))
    ni = ar.normalized_income("ethereum", _POOL, _RESERVE, 50,
                              fetch_logs=lambda c, s, f, t: [rdu],
                              block_ts=lambda c, b: 1_000_000)
    assert ni == 3 * RAY          # dt==0 → returns stored index


def test_rebased_balance_end_to_end():
    # scaled 1000 (one Mint @ RAY); NI = 2·RAY → rebased 2000.
    mint = _log(10, 0, MINT_T0, _OTHER, _H, _data(1000, 0, RAY))
    rdu = _log(50, 0, RDU_T0, _RESERVE, bytes(20), _data(0, 0, 0, 2 * RAY, 0))  # rate 0
    def fetch(chain, sel, frm, to):
        # crude router: RDU selection targets the pool address
        if sel[0]["address"][0] == "0x" + _POOL.hex():
            return [rdu]
        return [mint]
    bal = ar.rebased_balance_at("ethereum", _ATOKEN, _H, 100,
                                pool=_POOL, reserve=_RESERVE,
                                fetch_logs=fetch, block_ts=lambda c, b: 1_000_000)
    assert bal == 2000
