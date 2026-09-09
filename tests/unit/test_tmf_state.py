"""Unit tests for extract.tmf_state — decoding and the never-zero guard."""

from __future__ import annotations

from decimal import Decimal

import pytest

from settle.extract import tmf_state as T

WAD, RAD = 10**18, 10**45
_C = {
    "MCD_VAT": "0x" + "a1" * 20, "MCD_VOW": "0x" + "a2" * 20, "MCD_KICK": "0x" + "a3" * 20,
    "MCD_SPLIT": "0x" + "a4" * 20, "MCD_FLAP": "0x" + "a5" * 20,
    "REWARDS_LSSKY_USDS": "0x" + "a6" * 20, "REWARDS_LSSKY_SKY": "0x" + "a7" * 20,
    "REWARDS_DIST_LSSKY_SKY": "0x" + "a8" * 20, "MCD_VEST_SKY_TREASURY": "0x" + "a9" * 20,
    "MCD_PAUSE_PROXY": "0x" + "b1" * 20, "USDS": "0x" + "b2" * 20, "DAI": "0x" + "b3" * 20,
    "SKY": "0x" + "b4" * 20,
}
RECEIVER = "0x" + "be" * 20


def _word(n: int) -> str:
    return "0x" + hex(n)[2:].rjust(64, "0")


def _fake_chain(overrides: dict[str, str] | None = None):
    """eth_call double keyed on the 4-byte selector."""
    base = {
        T._sel("kbump()"): _word(6000 * RAD),
        T._sel("khump()"): _word((1 << 256) - 200_000_000 * RAD),
        T._sel("hop()"): _word(3748), T._sel("burn()"): _word(55 * WAD // 100),
        T._sel("zzz()"): _word(1_788_219_023), T._sel("want()"): _word(98 * WAD // 100),
        T._sel("receiver()"): "0x" + "00" * 12 + RECEIVER[2:],
        T._sel("farm()"): "0x" + "00" * 12 + _C["REWARDS_LSSKY_USDS"][2:],
        T._sel("flapper()"): "0x" + "00" * 12 + _C["MCD_FLAP"][2:],
        T._sel("rewardsDuration()"): _word(3748), T._sel("rewardRate()"): _word(WAD),
        T._sel("periodFinish()"): _word(1), T._sel("vestId()"): _word(16),
        T._sel("lastDistributedAt()"): _word(1), T._sel("usr(uint256)"): "0x" + "00" * 12 + RECEIVER[2:],
        T._sel("bgn(uint256)"): _word(100), T._sel("clf(uint256)"): _word(100),
        T._sel("fin(uint256)"): _word(100 + 90 * 86400), T._sel("tot(uint256)"): _word(96_903_706 * WAD),
        T._sel("rxd(uint256)"): _word(WAD), T._sel("cap()"): _word(70 * WAD),
        T._sel("dai(address)"): _word(600_000_000 * RAD), T._sel("sin(address)"): _word(650_000_000 * RAD),
        T._sel("Sin()"): _word(228_000_000 * RAD), T._sel("Ash()"): _word(0),
    }
    base.update(overrides or {})

    def eth_call(chain, contract, data, block):
        return base[data[:10]]
    return eth_call


@pytest.fixture
def chain(monkeypatch):
    def install(overrides=None, supply=6_366_968_221 * WAD):
        monkeypatch.setattr(T, "eth_call", _fake_chain(overrides))
        monkeypatch.setattr(T, "total_supply_of", lambda chain, token, block: supply)
        monkeypatch.setattr(T, "balance_of", lambda chain, token, holder, block: 33_702_776 * WAD)
        monkeypatch.setattr(T, "block_timestamp", lambda chain, block: 1_788_220_799)
    return install


def test_decodes_levers_in_human_units(chain):
    chain()
    s = T.read_tmf_state(_C, 25878704)
    assert s["kicker_kbump"] == Decimal(6000)
    assert s["kicker_khump"] == Decimal(-200_000_000)
    assert (s["splitter_hop"], s["splitter_burn"]) == (3748, Decimal("0.55"))
    assert s["vest_tot"] == Decimal(96_903_706) and s["dist_vest_id"] == 16
    assert s["flapper_receiver"] == RECEIVER and s["vest_usr"] == RECEIVER
    assert (s["splitter_farm"], s["splitter_flapper"]) == (_C["REWARDS_LSSKY_USDS"], _C["MCD_FLAP"])
    assert s["usds_total_supply"] == Decimal(6_366_968_221)
    assert s["vat_dai_vow"] - s["vat_sin_vow"] == Decimal(-50_000_000)


def test_empty_return_for_a_never_zero_lever_is_loud(chain):
    """A '0x' eth_call decodes to 0 — for burn/hop/supply/vest that is an RPC
    hiccup, not a value, and must not flow into the waterfall (or the cache)."""
    chain({T._sel("burn()"): "0x"})
    with pytest.raises(RuntimeError, match="splitter_burn read as 0"):
        T.read_tmf_state(_C, 25878704)
    chain(supply=0)
    with pytest.raises(RuntimeError, match="usds_total_supply"):
        T.read_tmf_state(_C, 25878704)


def test_empty_return_for_an_address_is_loud(chain):
    chain({T._sel("receiver()"): "0x"})
    with pytest.raises(ValueError, match="ABI word"):
        T.read_tmf_state(_C, 25878704)
