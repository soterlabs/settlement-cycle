"""Domain dataclasses — `Prime`, `Venue`, `Token`, `Address`.

These are immutable value objects. No I/O. Constructed by the config loader at the
top of every settlement run; consumed by Normalize and Compute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from .pricing import PricingCategory
from .subsidy import SubsidyConfig


class Chain(StrEnum):
    """Chains in scope. New chain → add here + RPC config."""

    ETHEREUM = "ethereum"
    BASE = "base"
    ARBITRUM = "arbitrum"
    OPTIMISM = "optimism"
    UNICHAIN = "unichain"
    AVALANCHE_C = "avalanche_c"
    PLUME = "plume"
    MONAD = "monad"


@dataclass(frozen=True, slots=True)
class Address:
    """20-byte EVM address. Always lowercased; normalized via :meth:`from_str`."""

    value: bytes

    def __post_init__(self) -> None:
        if len(self.value) != 20:
            raise ValueError(f"Address must be 20 bytes; got {len(self.value)}")

    @classmethod
    def from_str(cls, s: str) -> Self:
        s = s.lower().removeprefix("0x")
        if len(s) != 40:
            raise ValueError(f"Address hex must be 40 chars; got {len(s)} ({s!r})")
        return cls(bytes.fromhex(s))

    @property
    def hex(self) -> str:
        return "0x" + self.value.hex()

    def __str__(self) -> str:
        return self.hex


@dataclass(frozen=True, slots=True)
class Token:
    """An ERC-20 token (or pseudo-token like native ETH = address 0x0)."""

    chain: Chain
    address: Address
    symbol: str
    decimals: int

    @classmethod
    def from_dict(cls, chain: Chain, d: dict) -> Self:
        return cls(
            chain=chain,
            address=Address.from_str(d["address"]),
            symbol=d["symbol"],
            decimals=int(d["decimals"]),
        )


@dataclass(frozen=True, slots=True)
class NavOracle:
    """Off-/on-chain NAV-feed config for a Category E venue.

    `kind` selects the reader implementation: ``chronicle``, ``chainlink``,
    ``redstone``, ``pyth``, or ``const_one`` (always returns $1.00, used when
    the issuer publishes yield via rewards rather than via NAV — e.g. BUIDL-I).

    ``oracle_chain`` defaults to ``venue.chain`` but can be overridden when the
    same venue exists on multiple chains and the canonical NAV oracle lives on
    only one (e.g., Centrifuge JTRSY/JAAA — the Chronicle feed is on Ethereum
    even though the tranche token is also issued on Avalanche). The compute
    layer translates the venue-chain block to the equivalent oracle-chain
    block via the block resolver before reading.
    """

    kind: str
    address: Address | None = None
    fallback: str | None = None
    fallback_address: Address | None = None
    oracle_chain: "Chain | None" = None


@dataclass(frozen=True, slots=True)
class Venue:
    """One allocation venue for a prime — a position-bearing token, with pricing rules."""

    id: str                              # 'E1', 'V1', etc.
    chain: Chain
    token: Token                         # the venue token (aToken, vault share, LP, raw stable)
    pricing_category: PricingCategory
    underlying: Token | None = None      # for B/C/D/F where price chains via underlying
    label: str = ""                      # human-readable (e.g. 'Maple syrupUSDC')
    nav_oracle: NavOracle | None = None  # Category E only — see NavOracle
    lp_kind: str | None = None           # Category F only: 'curve_stableswap' | 'uniswap_v3'
    nft_position_manager: Address | None = None  # Category F (uniswap_v3) only
    # Per-venue minimum transfer threshold (USD-equivalent). Drops transfers
    # below this amount from the cumulative-balance pull so daily
    # yield-distribution mints (BUIDL-style) don't contaminate the
    # capital-inflow stream. Plumbed to
    # ``IBalanceSource.cumulative_balance_timeseries(min_transfer_amount=…)``.
    # ``None`` means no filter (default).
    min_transfer_amount_usd: Decimal | None = None
    # DEPRECATED 2026-05-02 — superseded by ``config/sky_direct_exposures.yaml``
    # (loaded as ``SDETable`` in ``compute.monthly_pnl``). Retained as a YAML
    # sink for legacy configs but ignored by compute. Will be removed once
    # all {prime}.yaml files have dropped the field.
    sky_direct: bool = False
    # Override for the address that holds this venue's tokens. Default None
    # means use ``prime.alm[venue.chain]`` (the standard case). Set to a
    # specific contract address for venues like Spark Savings V2 vaults
    # where the prime's ALM does NOT custody the position; instead the
    # vault contract custodies underlying tokens on behalf of retail
    # depositors and the prime earns the yield spread.
    holder_override: Address | None = None
    # Skip flag: when True, the venue is excluded from compute (no value, no
    # revenue, no inflow tracking). Use for venues whose underlying is too
    # volatile or whose oracle isn't trustworthy to include in MSC. The venue
    # stays in YAML for documentation and historical reproducibility.
    skip: bool = False
    # Curve pool USDS-idle tracking. When set, the compute layer reads the
    # prime's proportional share of the named coin's reserve daily (via RPC
    # ``read_pool`` + ``balanceOf`` + optionally ``convertToAssets`` for 4626
    # underlyings) and subtracts it from ``utilized`` in ``compute_sky_revenue``
    # (prime-settlement-methodology Step 2 — idle USDS in AMM pools).
    # Only meaningful for ``lp_kind=curve_stableswap`` venues.
    curve_idle_usds: CurveIdleUsdsConfig | None = None


@dataclass(frozen=True, slots=True)
class CurveIdleUsdsConfig:
    """Track the prime's proportional USDS-equivalent inside a Curve pool and
    subtract it from ``utilized`` in ``compute_sky_revenue``.

    ``coin`` is the address of the pool coin whose balance represents idle
    USDS (or USDS-convertible) capital:

    * **Par-stable** (e.g. USDS, USDC) — balance used at face value ($1 per
      unit), scaled by the token's decimals.
    * **Yield-bearing ERC-4626** (e.g. sUSDS) — balance converted to USDS via
      ``convertToAssets(balance, block)`` so the deduction is in USDS principal,
      consistent with the rest of the utilized formula.

    The prime's share of the pool's idle USDS is computed daily as::

        prime_usds_d = (alm_lp_balance_d / pool_total_supply_d) × coin_usds_d

    where ``coin_usds_d`` is the above par-stable or 4626 value of the target
    coin's reserve at day ``d``'s EoD block.
    """

    coin: Address  # address of the target coin in the Curve pool


class PsmKind(StrEnum):
    """How USDS-equivalent value at a PSM is computed.

    * ``directed_flow`` — Sky LITE-PSM-USDC pattern (used by Grove/OBEX/Spark
      on Ethereum). PSM is a swap conduit holding USDS at par; we track net
      USDS flow ``(subproxy + ALM) → PSM − PSM → (subproxy + ALM)``. The
      ``token`` field names what's tracked (USDS).
    * ``erc4626_shares`` — Spark PSM3 pattern (used on Base/Arbitrum/Optimism
      /Unichain). PSM3 has a non-standard ABI: shares are *internal accounting*
      (no ERC-20 Transfer events) and the rate uses
      ``convertToAssetValue(uint256)`` returning the USDS-equivalent value
      directly. We snapshot ``convertToAssetValue(shares(alm, b), b)`` at each
      day's EoD block. The ``token`` field is unused.
    """

    DIRECTED_FLOW = "directed_flow"
    ERC4626_SHARES = "erc4626_shares"


@dataclass(frozen=True, slots=True)
class PsmConfig:
    """Per-chain PSM configuration. Holdings are subtracted from ``utilized``
    in ``compute_sky_revenue`` so the prime is reimbursed BR on the parked
    capital (prime-settlement-methodology Step 2)."""

    kind: PsmKind
    address: Address
    # Only meaningful for ``kind=directed_flow`` — names the underlying token
    # whose flows we track (e.g. USDS for Sky LITE-PSM). Ignored when shares-
    # based since the share token IS the PSM contract address.
    token: Address | None = None


@dataclass(frozen=True, slots=True)
class PrincipalReturnOverride:
    """A single inflow that arrives FROM an `external_alm_sources` address
    but should NOT be classified as yield (it's a principal-return event
    on a tri-party loan or similar instrument). Matched by (date, amount)
    against on-chain Transfer rows during Cat A inflow classification.

    Amounts are in whole USD units (par-stable assumption — same scaling
    as ``inflow_by_counterparty.signed_amount``). Matching tolerates ±$1
    of rounding noise.
    """

    date: date
    amount: Decimal
    token: str = ""    # token symbol — sanity check for human readers
    note: str = ""


@dataclass(frozen=True, slots=True)
class Prime:
    """A Sky prime agent — ilk, addresses per chain, allocation venues."""

    id: str                              # 'obex' | 'grove' | 'spark' | 'skybase' | …
    ilk_bytes32: bytes                   # 32-byte ilk identifier
    start_date: date                     # first frob date (calendar start)
    subproxy: dict[Chain, Address] = field(default_factory=dict)
    alm: dict[Chain, Address] = field(default_factory=dict)
    venues: list[Venue] = field(default_factory=list)
    # Per-chain PSM config (replaces the old hardcoded ``compute._psm.PSM_BY_CHAIN``
    # dict). Each chain may have at most one PSM today; if a future prime needs
    # multiple, this becomes ``dict[Chain, list[PsmConfig]]``.
    psm: dict[Chain, PsmConfig] = field(default_factory=dict)
    # Addresses whose transfers TO the ALM count as Cat A revenue (off-chain
    # custodian distributions, e.g. Anchorage sending realized yield directly
    # to the ALM). Anything NOT in this list is treated as value-preserving
    # capital flow (PSM swap legs, venue contract allocations/withdrawals,
    # AllocatorBuffer top-ups, mint/burn). Empty by default — flag a counterparty
    # only after confirming it sends true off-chain yield, since misclassification
    # inflates revenue.
    external_alm_sources: dict[Chain, list[Address]] = field(default_factory=dict)
    # Per-(chain, source) overrides for inflows that arrive from an external
    # ALM source but should NOT be counted as yield (e.g., a tri-party loan
    # principal correction or final principal return at maturity). The Cat A
    # classifier matches by (date, amount within $1) and reclassifies matching
    # inflows as capital.  See ``PrincipalReturnOverride`` and
    # ``_cat_a_capital_inflow_timeseries``.
    principal_return_overrides: dict[
        Chain, dict[Address, list["PrincipalReturnOverride"]]
    ] = field(default_factory=dict)
    # Subsidised borrowing rate config. Default = disabled (legacy behavior:
    # full BR on utilized). When enabled, Sky charges subsidised rate on the
    # first ``cap_usd`` of utilized USDS; any excess at full BR.
    subsidy: SubsidyConfig = field(default_factory=lambda: SubsidyConfig(enabled=False))

    def __post_init__(self) -> None:
        if len(self.ilk_bytes32) != 32:
            raise ValueError(f"ilk_bytes32 must be 32 bytes; got {len(self.ilk_bytes32)}")

    @property
    def chains(self) -> set[Chain]:
        return set(self.alm.keys()) | set(self.subproxy.keys())
