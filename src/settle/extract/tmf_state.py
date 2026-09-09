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
from ._abi import decode_address, pad_address, pad_uint
from ._keccak import keccak256
from .rpc import _decode_uint, balance_of, block_timestamp, eth_call, total_supply_of

__all__ = ["read_tmf_state"]

WAD = Decimal(10) ** 18
RAY = Decimal(10) ** 27
RAD = Decimal(10) ** 45


def _sel(sig: str) -> str:
    return "0x" + keccak256(sig.encode()).hex()[:8]


_uint = _decode_uint   # same "0x" → 0 semantics as every other RPC reader


def _int(raw: str) -> int:
    """Two's-complement int256 (Kicker.khump is negative)."""
    v = _decode_uint(raw)
    return v - (1 << 256) if v >= (1 << 255) else v


def _call(contract: str, sig: str, block: int, *args: str) -> str:
    data = _sel(sig) + "".join(args)
    return eth_call(Chain.ETHEREUM, Address.from_str(contract), data, block)


def read_tmf_state(contracts: dict[str, str], block: int) -> dict[str, Any]:
    """Read every TMF lever + balance at ``block``. Keys are stable — the
    report renderer and the cross-checks index them by name."""
    c = contracts
    eth = Chain.ETHEREUM
    vow_word = pad_address(Address.from_str(c["MCD_VOW"]))

    def supply(key: str) -> Decimal:
        return Decimal(total_supply_of(eth, Address.from_str(c[key]), block)) / WAD

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
        "flapper_receiver": decode_address(_call(c["MCD_FLAP"], "receiver()", block)).hex,
        # USDS farm
        "usds_farm_rewards_duration": _uint(_call(c["REWARDS_LSSKY_USDS"], "rewardsDuration()", block)),
        "usds_farm_reward_rate": Decimal(_uint(_call(c["REWARDS_LSSKY_USDS"], "rewardRate()", block))) / WAD,
        "usds_farm_total_supply": supply("REWARDS_LSSKY_USDS"),
        "usds_farm_period_finish": _uint(_call(c["REWARDS_LSSKY_USDS"], "periodFinish()", block)),
        # SKY farm
        "sky_farm_rewards_duration": _uint(_call(c["REWARDS_LSSKY_SKY"], "rewardsDuration()", block)),
        "sky_farm_reward_rate": Decimal(_uint(_call(c["REWARDS_LSSKY_SKY"], "rewardRate()", block))) / WAD,
        "sky_farm_total_supply": supply("REWARDS_LSSKY_SKY"),
        "sky_farm_period_finish": _uint(_call(c["REWARDS_LSSKY_SKY"], "periodFinish()", block)),
        # Distributor + vest stream
        "dist_vest_id": vest_id,
        "dist_last_distributed_at": _uint(_call(c["REWARDS_DIST_LSSKY_SKY"], "lastDistributedAt()", block)),
        "vest_usr": decode_address(_call(vest, "usr(uint256)", block, vid)).hex,
        "vest_bgn": vest_field("bgn"),
        "vest_clf": vest_field("clf"),
        "vest_fin": vest_field("fin"),
        "vest_tot": Decimal(vest_field("tot")) / WAD,
        "vest_rxd": Decimal(vest_field("rxd")) / WAD,
        "vest_cap": Decimal(_uint(_call(vest, "cap()", block))) / WAD,
        # Treasury + surplus
        "pause_proxy_sky": Decimal(balance_of(
            eth, Address.from_str(c["SKY"]), Address.from_str(c["MCD_PAUSE_PROXY"]), block,
        )) / WAD,
        "vat_dai_vow": Decimal(_uint(_call(c["MCD_VAT"], "dai(address)", block, vow_word))) / RAD,
        "vat_sin_vow": Decimal(_uint(_call(c["MCD_VAT"], "sin(address)", block, vow_word))) / RAD,
        "vow_Sin": Decimal(_uint(_call(c["MCD_VOW"], "Sin()", block))) / RAD,
        "vow_Ash": Decimal(_uint(_call(c["MCD_VOW"], "Ash()", block))) / RAD,
        # Supplies (Target Backstop Capital basis)
        "usds_total_supply": supply("USDS"),
        "dai_total_supply": supply("DAI"),
    }
    # ``_decode_uint`` maps an empty / reverted ``0x`` return to 0. For these
    # levers 0 is impossible on a live system, so a 0 is an RPC hiccup that
    # would otherwise flow into the waterfall (TBC = 0, burn = 0) unnoticed —
    # and be cached. Fail loud instead.
    never_zero = (
        "kicker_kbump", "splitter_hop", "splitter_burn", "usds_total_supply",
        "vest_tot", "vest_cap", "dist_vest_id",
    )
    zeros = [k for k in never_zero if not state[k]]
    if zeros:
        raise RuntimeError(
            f"read_tmf_state: {', '.join(zeros)} read as 0 at block {block} — an empty "
            "eth_call return was decoded as zero; the RPC did not serve this block. "
            "Clear the cached value (SETTLE_CACHE_DIR) and retry."
        )
    return state
