"""``INavOracleSource`` implementations — Chronicle (on-chain) + ConstOne (config).

Phase 2.A scope: Chronicle + ConstOne. Redstone / Chainlink / Pyth implementations
follow in Phase 2.B; until then they're absent from the registry and trigger
``UnknownSourceError`` if a YAML lists them.
"""

from __future__ import annotations

from decimal import Decimal

from ...domain.primes import Address, Chain
from ...extract.oracles import chronicle


class ChronicleNavSource:
    """``INavOracleSource`` backed by ``extract.oracles.chronicle.read``."""

    def nav_at(
        self,
        chain: str,
        oracle_address: bytes | None,
        block: int,
    ) -> Decimal:
        if oracle_address is None:
            raise ValueError("ChronicleNavSource requires an oracle address")
        return chronicle.read(Chain(chain), Address(oracle_address), block)


class ConstOneNavSource:
    """``INavOracleSource`` that always returns $1.00. Used by config-pinned
    venues (e.g. BUIDL-I, where issuer-published yield is captured via rewards
    and MtM is pinned). No I/O, never fails."""

    def nav_at(
        self,
        chain: str,                     # ignored
        oracle_address: bytes | None,   # ignored
        block: int,                     # ignored
    ) -> Decimal:
        return Decimal("1.00")
