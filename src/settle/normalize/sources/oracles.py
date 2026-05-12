"""``INavOracleSource`` implementations — Chronicle, PricePerShare, ConstOne."""

from __future__ import annotations

from decimal import Decimal

from ...domain.primes import Address, Chain
from ...extract.oracles import chronicle, price_per_share


class ChronicleNavSource:
    """``INavOracleSource`` backed by ``extract.oracles.chronicle.read``.

    NB (2026-05-02): for Centrifuge tranche tokens (JAAA, JTRSY, ACRDX),
    the canonical NAV feed has moved to ``price_per_share_feed`` per Grove
    team's PnL workbook. Chronicle is kept for legacy venues and as a
    fallback.
    """

    def nav_at(
        self,
        chain: str,
        oracle_address: bytes | None,
        block: int,
    ) -> Decimal:
        if oracle_address is None:
            raise ValueError("ChronicleNavSource requires an oracle address")
        return chronicle.read(Chain(chain), Address(oracle_address), block)


class PricePerShareNavSource:
    """``INavOracleSource`` backed by ``convertToAssets(1e18)`` on a feed
    contract. Canonical NAV source for Centrifuge tranche tokens (per Grove
    team's Feb 2026 PnL workbook). The feed's underlying is USDC at $1, so
    the returned value is dollar-denominated NAV directly.
    """

    def nav_at(
        self,
        chain: str,
        oracle_address: bytes | None,
        block: int,
    ) -> Decimal:
        if oracle_address is None:
            raise ValueError("PricePerShareNavSource requires an oracle address")
        return price_per_share.read(Chain(chain), Address(oracle_address), block)


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


class ConstNavSource:
    """``INavOracleSource`` that always returns a fixed USD NAV. Instantiated
    by ``get_nav_oracle_source`` when the kind string matches ``const_<value>``
    (e.g. ``const_1000`` returns $1,000.00).

    Use as a fallback for Cat E venues whose primary oracle (Chronicle, etc.)
    was not yet deployed or funded at the SoM block of an early settlement
    period. The constant should approximate the token's NAV at that time so
    that revenue = (eom_price − fallback_price) × balance tracks the actual
    in-period appreciation rather than the full value from inception.

    Example: E7 (STAC) was issued at $1,000/token in Dec 2025; the Chronicle
    oracle was deployed Jan 9 2026. Setting ``fallback: const_1000`` avoids
    the phantom $100M revenue that would arise from a $1 const_one fallback
    at SoM producing a near-zero starting value against a $1,006 EoM price.
    """

    def __init__(self, value: Decimal) -> None:
        self._value = value

    def nav_at(
        self,
        chain: str,                     # ignored
        oracle_address: bytes | None,   # ignored
        block: int,                     # ignored
    ) -> Decimal:
        return self._value
