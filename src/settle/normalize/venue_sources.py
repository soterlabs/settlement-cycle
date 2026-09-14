"""Per-venue cutovers; explicit caller/fixture source injections still win."""

from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..compute.monthly_pnl import Sources
    from ..domain.primes import Venue

from .sources.hypersync_balances import HyperSyncBalanceSource
from .sources.hypersync_lp import HyperSyncV3PositionSource, HyperSyncV4PositionSource


def for_venue(sources: "Sources", venue: "Venue") -> "Sources":
    if venue.event_source != "hypersync":
        return sources
    overrides: dict[str, Any] = {}
    if sources.balance is None:
        overrides["balance"] = HyperSyncBalanceSource()
    if venue.lp_kind == "uniswap_v3" and sources.v3_position is None:
        overrides["v3_position"] = HyperSyncV3PositionSource(
            nfpm_per_chain={venue.chain: venue.nft_position_manager}
            if venue.nft_position_manager else None,
        )
    if venue.lp_kind == "uniswap_v4" and sources.v4_position is None:
        overrides["v4_position"] = HyperSyncV4PositionSource(
            position_manager_per_chain={venue.chain: venue.nft_position_manager}
            if venue.nft_position_manager else None,
        )
    return replace(sources, **overrides)
