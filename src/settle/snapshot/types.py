"""Snapshot result types — mirror BA labs `/stars/{prime}/` and
`/allocations/?star={prime}` field shapes for direct parity comparison."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..domain.primes import Address, Chain


@dataclass(frozen=True, slots=True)
class VenueSnapshot:
    """One venue's USD value at a specific block. Mirrors BA's
    /allocations/?star={prime} per-entry shape (subset of fields we can
    reconstruct from on-chain primitives)."""
    venue_id: str
    label: str
    chain: Chain
    token_address: Address
    holder_address: Address
    pricing_category: str
    block: int
    shares: Decimal           # token-unit balance (decimal-adjusted)
    value_usd: Decimal        # USD value at this block
    pps: Decimal | None       # price-per-share (None for non-share tokens like LP NFTs)
    note: str = ""            # diagnostic (skip / error / methodology)


@dataclass(frozen=True, slots=True)
class IdleHolding:
    """A non-venue token balance at a known holder address (treasury / idle).
    BA splits these into ``treasury_balance`` (USDS at subproxy) and
    ``idle_assets`` (everything else). We capture them all here and the
    aggregator fields below sum them by category."""
    label: str                # 'subproxy_USDS' | 'subproxy_sUSDS' | 'alm_USDS' | ...
    chain: Chain
    holder_address: Address
    token_address: Address
    token_symbol: str
    block: int
    shares: Decimal
    value_usd: Decimal
    category: str             # 'treasury' | 'idle' | 'in_transit'


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Top-level snapshot. Top-line aggregates mirror BA's `/stars/{prime}/`."""
    prime_id: str
    generated_at_utc: datetime
    pin_blocks: dict[Chain, int]

    venues: list[VenueSnapshot]
    idle: list[IdleHolding]

    # Aggregates — derived from venues + idle.
    venues_total_usd: Decimal
    treasury_balance_usd: Decimal     # USDS held at the prime's "treasury" (subproxy on Eth)
    idle_assets_usd: Decimal          # non-USDS idle (sUSDS / USDC / etc.)
    in_transit_assets_usd: Decimal    # cross-chain bridge in-flight (=0 for now)

    # Debt = Vat.ilks(ilk).Art × Vat.ilks(ilk).rate / 1e45 — single read.
    # (Art is wad ×1e18, rate is ray ×1e27; product is rad ×1e45 → USD whole units.)
    debt_usd: Decimal | None

    @property
    def assets_usd(self) -> Decimal:
        """Total assets = venues + treasury + idle + in_transit. Mirrors BA."""
        return (self.venues_total_usd + self.treasury_balance_usd
                + self.idle_assets_usd + self.in_transit_assets_usd)

    @property
    def liabilities_usd(self) -> Decimal:
        """For prime agents on Sky's allocator system, liabilities = debt
        (single ilk per prime; no other off-balance-sheet exposures)."""
        return self.debt_usd if self.debt_usd is not None else Decimal("0")

    @property
    def nav_usd(self) -> Decimal:
        """Net asset value = assets − liabilities. Surplus the prime owns
        beyond its USDS borrowing obligation."""
        return self.assets_usd - self.liabilities_usd
