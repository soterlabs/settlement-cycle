"""Source registry — config-driven dispatch.

Looks up Source implementations by name (e.g. ``'dune'``) for each Protocol.
Adding a new source = register a class here. Phase 5 (subgraph migration)
plugs in via this registry without touching Compute or Load.
"""

from __future__ import annotations

from decimal import Decimal

from .protocols import (
    IBalanceSource,
    IBlockResolver,
    IConvertToAssetsSource,
    IDebtSource,
    INavOracleSource,
    IPositionBalanceSource,
    IPsm3Source,
    ISavingsV2DeployedSource,
    ISSRSource,
)
from .sources.dune_balances import DuneBalanceSource
from .sources.dune_debt import DuneDebtSource
from .sources.dune_savings_v2_deployed import DuneSavingsV2DeployedSource
from .sources.dune_ssr import DuneSSRSource
from .sources.oracles import (
    ChronicleNavSource,
    ConstNavSource,
    ConstOneNavSource,
    ERC4626NavSource,
    PricePerShareNavSource,
    RedstoneNavSource,
)
from .sources.rpc_block_resolver import RPCBlockResolver
from .sources.rpc_position import (
    RPCConvertToAssetsSource,
    RPCPositionBalanceSource,
    RPCPsm3Source,
)

_DEBT_SOURCES: dict[str, type[IDebtSource]] = {
    "dune": DuneDebtSource,
}

_BALANCE_SOURCES: dict[str, type[IBalanceSource]] = {
    "dune": DuneBalanceSource,
}

_SSR_SOURCES: dict[str, type[ISSRSource]] = {
    "dune": DuneSSRSource,
}

_SAVINGS_V2_DEPLOYED_SOURCES: dict[str, type[ISavingsV2DeployedSource]] = {
    "dune": DuneSavingsV2DeployedSource,
}

_POSITION_BALANCE_SOURCES: dict[str, type[IPositionBalanceSource]] = {
    "rpc": RPCPositionBalanceSource,
}

_CONVERT_TO_ASSETS_SOURCES: dict[str, type[IConvertToAssetsSource]] = {
    "rpc": RPCConvertToAssetsSource,
}

_PSM3_SOURCES: dict[str, type[IPsm3Source]] = {
    "rpc": RPCPsm3Source,
}

_BLOCK_RESOLVER_SOURCES: dict[str, type[IBlockResolver]] = {
    "rpc": RPCBlockResolver,
    # ``dune`` requires a date range at construction time — instantiated by
    # the caller with explicit (chain, start_date, end_date, pin_block), not
    # via the no-arg ``get_block_resolver()`` factory. See
    # ``DuneBlockResolver`` docstring.
}

# NAV oracles — keyed by ``Venue.nav_oracle.kind`` from per-prime YAML.
# Note: ``erc4626`` is NOT in this dict — it is dispatched dynamically below
# via the ``erc4626_<asset_decimals>`` encoded kind string.
_NAV_ORACLE_SOURCES: dict[str, type[INavOracleSource]] = {
    "chronicle": ChronicleNavSource,
    "const_one": ConstOneNavSource,
    "price_per_share_feed": PricePerShareNavSource,
    "redstone": RedstoneNavSource,
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


def get_savings_v2_deployed_source(name: str = "dune") -> ISavingsV2DeployedSource:
    if name not in _SAVINGS_V2_DEPLOYED_SOURCES:
        raise UnknownSourceError(
            f"Unknown savings-v2-deployed source {name!r}. "
            f"Available: {sorted(_SAVINGS_V2_DEPLOYED_SOURCES)}"
        )
    return _SAVINGS_V2_DEPLOYED_SOURCES[name]()


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


def get_psm3_source(name: str = "rpc") -> IPsm3Source:
    if name not in _PSM3_SOURCES:
        raise UnknownSourceError(
            f"Unknown PSM3 source {name!r}. "
            f"Available: {sorted(_PSM3_SOURCES)}"
        )
    return _PSM3_SOURCES[name]()


def get_block_resolver(name: str = "rpc") -> IBlockResolver:
    if name not in _BLOCK_RESOLVER_SOURCES:
        raise UnknownSourceError(
            f"Unknown block-resolver source {name!r}. "
            f"Available: {sorted(_BLOCK_RESOLVER_SOURCES)}"
        )
    return _BLOCK_RESOLVER_SOURCES[name]()


def get_nav_oracle_source(kind: str) -> INavOracleSource:
    """Lookup a NAV-oracle Source by ``kind`` (Venue.nav_oracle.kind from YAML).

    In addition to the registered kinds, supports ``const_<value>`` where
    ``<value>`` is any decimal number (e.g. ``const_1000`` returns $1,000.00
    for every block). This lets YAML configs pin a flat fallback NAV without
    registering a new class per value — useful for Cat E venues whose primary
    oracle was not yet deployed at the start of an early settlement period.

    Raises ``UnknownSourceError`` if the kind isn't registered and doesn't
    match the ``const_<value>`` pattern. The Cat E branch in
    ``normalize.prices`` catches this so an unknown fallback kind triggers the
    next candidate in the chain instead of crashing the run.
    """
    if kind in _NAV_ORACLE_SOURCES:
        return _NAV_ORACLE_SOURCES[kind]()

    # Dynamic const_<value> — e.g. "const_1000", "const_1000.50"
    if kind.startswith("const_"):
        suffix = kind[len("const_"):]
        try:
            return ConstNavSource(Decimal(suffix))
        except Exception:
            pass  # fall through to UnknownSourceError

    # Dynamic erc4626_<underlying_decimals>_<share_decimals>
    # e.g. "erc4626_6_18" (USDC underlying, 18-decimal shares).
    # Encoded by ``normalize.prices._nav_oracle_kind`` from the YAML
    # ``[fallback_]underlying_decimals`` field + ``venue.token.decimals``
    # (share decimals are not a separate config field — see NavOracle docstring).
    if kind.startswith("erc4626_"):
        parts = kind[len("erc4626_"):].split("_")
        if len(parts) == 2:
            try:
                return ERC4626NavSource(
                    asset_decimals=int(parts[0]),
                    share_decimals=int(parts[1]),
                )
            except ValueError:
                pass  # fall through to UnknownSourceError

    raise UnknownSourceError(
        f"Unknown NAV-oracle kind {kind!r}. "
        f"Available: {sorted(_NAV_ORACLE_SOURCES)}, const_<decimal_value>, "
        f"or erc4626_<underlying_decimals>_<share_decimals> (e.g. erc4626_6_18)."
    )
