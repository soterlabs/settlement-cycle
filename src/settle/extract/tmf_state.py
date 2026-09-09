"""Point-in-time reads of the Treasury Management Function contracts.

Everything the TMF report needs from chain STATE (as opposed to events, which
``normalize/sources/hypersync_sbe.py`` extracts): the Kicker / Splitter
levers, both LSSKY farms, the SKY rewards distributor and its DssVest stream,
the Vow surplus components, and the USDS / DAI supplies. All via ``eth_call``
at one pinned block (cached like every other RPC read), so the free-tier
10-block ``eth_getLogs`` limit never matters here.

Values are returned in HUMAN units (USDS, SKY, seconds, fractions) as
``Decimal``/``int`` — the report layer never sees wad/ray/rad.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..domain.primes import Address, Chain
from ._abi import pad_address, pad_uint
from ._keccak import keccak256
from .rpc import block_timestamp, eth_call

__all__ = ["read_tmf_state"]

WAD = Decimal(10) ** 18
RAY = Decimal(10) ** 27
RAD = Decimal(10) ** 45


def _sel(sig: str) -> str:
    return "0x" + keccak256(sig.encode()).hex()[:8]


def _uint(raw: str) -> int:
    if raw in (None, "0x", "0x0"):
        return 0
    return int(raw, 16)


def _int(raw: str) -> int:
    v = _uint(raw)
    return v - (1 << 256) if v >= (1 << 255) else v


def _call(contract: str, sig: str, block: int, *args: str) -> str:
    data = _sel(sig) + "".join(args)
    return eth_call(Chain.ETHEREUM, Address.from_str(contract), data, block)


def read_tmf_state(contracts: dict[str, str], block: int) -> dict[str, Any]:
    """Read every TMF lever + balance at ``block``. Keys are stable — the
    report renderer and the cross-checks index them by name."""
    c = contracts
    vow_word = pad_address(Address.from_str(c["MCD_VOW"]))
    pp_word = pad_address(Address.from_str(c["MCD_PAUSE_PROXY"]))

    vest_id = _uint(_call(c["REWARDS_DIST_LSSKY_SKY"], "vestId()", block))
    vid = pad_uint(vest_id)
    vest = c["MCD_VEST_SKY_TREASURY"]

    def vest_field(name: str) -> int:
        return _uint(_call(vest, f"{name}(uint256)", block, vid))

    state: dict[str, Any] = {
        "block": block,
        "ts": block_timestamp(Chain.ETHEREUM, block),
        # Kicker
        "kicker_kbump": Decimal(_uint(_call(c["MCD_KICK"], "kbump()", block))) / RAD,
        "kicker_khump": Decimal(_int(_call(c["MCD_KICK"], "khump()", block))) / RAD,
        # Splitter
        "splitter_hop": _uint(_call(c["MCD_SPLIT"], "hop()", block)),
        "splitter_burn": Decimal(_uint(_call(c["MCD_SPLIT"], "burn()", block))) / WAD,
        "splitter_zzz": _uint(_call(c["MCD_SPLIT"], "zzz()", block)),
        # Flapper
        "flapper_want": Decimal(_uint(_call(c["MCD_FLAP"], "want()", block))) / WAD,
        "flapper_receiver": "0x" + _call(c["MCD_FLAP"], "receiver()", block)[-40:],
        # USDS farm
        "usds_farm_rewards_duration": _uint(_call(c["REWARDS_LSSKY_USDS"], "rewardsDuration()", block)),
        "usds_farm_reward_rate": Decimal(_uint(_call(c["REWARDS_LSSKY_USDS"], "rewardRate()", block))) / WAD,
        "usds_farm_total_supply": Decimal(_uint(_call(c["REWARDS_LSSKY_USDS"], "totalSupply()", block))) / WAD,
        "usds_farm_period_finish": _uint(_call(c["REWARDS_LSSKY_USDS"], "periodFinish()", block)),
        # SKY farm
        "sky_farm_rewards_duration": _uint(_call(c["REWARDS_LSSKY_SKY"], "rewardsDuration()", block)),
        "sky_farm_reward_rate": Decimal(_uint(_call(c["REWARDS_LSSKY_SKY"], "rewardRate()", block))) / WAD,
        "sky_farm_total_supply": Decimal(_uint(_call(c["REWARDS_LSSKY_SKY"], "totalSupply()", block))) / WAD,
        "sky_farm_period_finish": _uint(_call(c["REWARDS_LSSKY_SKY"], "periodFinish()", block)),
        # Distributor + vest stream
        "dist_vest_id": vest_id,
        "dist_last_distributed_at": _uint(_call(c["REWARDS_DIST_LSSKY_SKY"], "lastDistributedAt()", block)),
        "vest_usr": "0x" + _call(vest, "usr(uint256)", block, vid)[-40:],
        "vest_bgn": vest_field("bgn"),
        "vest_clf": vest_field("clf"),
        "vest_fin": vest_field("fin"),
        "vest_tot": Decimal(vest_field("tot")) / WAD,
        "vest_rxd": Decimal(vest_field("rxd")) / WAD,
        "vest_cap": Decimal(_uint(_call(vest, "cap()", block))) / WAD,
        # Treasury + surplus
        "pause_proxy_sky": Decimal(_uint(_call(c["SKY"], "balanceOf(address)", block, pp_word))) / WAD,
        "vat_dai_vow": Decimal(_uint(_call(c["MCD_VAT"], "dai(address)", block, vow_word))) / RAD,
        "vat_sin_vow": Decimal(_uint(_call(c["MCD_VAT"], "sin(address)", block, vow_word))) / RAD,
        "vow_Sin": Decimal(_uint(_call(c["MCD_VOW"], "Sin()", block))) / RAD,
        "vow_Ash": Decimal(_uint(_call(c["MCD_VOW"], "Ash()", block))) / RAD,
        # Supplies (Target Backstop Capital basis)
        "usds_total_supply": Decimal(_uint(_call(c["USDS"], "totalSupply()", block))) / WAD,
        "dai_total_supply": Decimal(_uint(_call(c["DAI"], "totalSupply()", block))) / WAD,
    }
    return state
