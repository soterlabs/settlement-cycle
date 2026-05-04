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
