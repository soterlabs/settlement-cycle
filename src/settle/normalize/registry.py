"""Source registry — config-driven dispatch.

Looks up Source implementations by name (e.g. ``'dune'``) for each Protocol.
Adding a new source = register a class here. Phase 5 (subgraph migration)
plugs in via this registry without touching Compute or Load.
"""

from __future__ import annotations

from .protocols import (
    IBalanceSource,
    IBlockResolver,
    IConvertToAssetsSource,
    IDebtSource,
    IPositionBalanceSource,
    ISSRSource,
)
from .sources.dune_balances import DuneBalanceSource
from .sources.dune_debt import DuneDebtSource
from .sources.dune_ssr import DuneSSRSource
from .sources.rpc_block_resolver import RPCBlockResolver
from .sources.rpc_position import RPCConvertToAssetsSource, RPCPositionBalanceSource

_DEBT_SOURCES: dict[str, type[IDebtSource]] = {
    "dune": DuneDebtSource,
}

_BALANCE_SOURCES: dict[str, type[IBalanceSource]] = {
    "dune": DuneBalanceSource,
}

_SSR_SOURCES: dict[str, type[ISSRSource]] = {
    "dune": DuneSSRSource,
}

_POSITION_BALANCE_SOURCES: dict[str, type[IPositionBalanceSource]] = {
    "rpc": RPCPositionBalanceSource,
}

_CONVERT_TO_ASSETS_SOURCES: dict[str, type[IConvertToAssetsSource]] = {
    "rpc": RPCConvertToAssetsSource,
}

_BLOCK_RESOLVER_SOURCES: dict[str, type[IBlockResolver]] = {
    "rpc": RPCBlockResolver,
}


class UnknownSourceError(KeyError):
    """Raised when a config requests a source name that isn't registered."""


def get_debt_source(name: str = "dune") -> IDebtSource:
    if name not in _DEBT_SOURCES:
        raise UnknownSourceError(
            f"Unknown debt source {name!r}. Available: {sorted(_DEBT_SOURCES)}"
        )
    return _DEBT_SOURCES[name]()


def get_balance_source(name: str = "dune") -> IBalanceSource:
    if name not in _BALANCE_SOURCES:
        raise UnknownSourceError(
            f"Unknown balance source {name!r}. Available: {sorted(_BALANCE_SOURCES)}"
        )
    return _BALANCE_SOURCES[name]()


def get_ssr_source(name: str = "dune") -> ISSRSource:
    if name not in _SSR_SOURCES:
        raise UnknownSourceError(
            f"Unknown SSR source {name!r}. Available: {sorted(_SSR_SOURCES)}"
        )
    return _SSR_SOURCES[name]()


def get_position_balance_source(name: str = "rpc") -> IPositionBalanceSource:
    if name not in _POSITION_BALANCE_SOURCES:
        raise UnknownSourceError(
            f"Unknown position-balance source {name!r}. "
            f"Available: {sorted(_POSITION_BALANCE_SOURCES)}"
        )
    return _POSITION_BALANCE_SOURCES[name]()


def get_convert_to_assets_source(name: str = "rpc") -> IConvertToAssetsSource:
    if name not in _CONVERT_TO_ASSETS_SOURCES:
        raise UnknownSourceError(
            f"Unknown convert-to-assets source {name!r}. "
            f"Available: {sorted(_CONVERT_TO_ASSETS_SOURCES)}"
        )
    return _CONVERT_TO_ASSETS_SOURCES[name]()


def get_block_resolver(name: str = "rpc") -> IBlockResolver:
    if name not in _BLOCK_RESOLVER_SOURCES:
        raise UnknownSourceError(
            f"Unknown block-resolver source {name!r}. "
            f"Available: {sorted(_BLOCK_RESOLVER_SOURCES)}"
        )
    return _BLOCK_RESOLVER_SOURCES[name]()
