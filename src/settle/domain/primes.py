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
    # Lending pool idle underlying tracking. When True, the compute layer
    # reads the prime's proportional share of the unborrowed underlying sitting
    # in the lending pool contract daily via:
    #   prime_idle = (balanceOf(alm, spToken) / totalSupply(spToken))
    #              × balanceOf(spToken_contract, underlying)
    # and subtracts the USDS-equivalent from ``utilized``
    # (prime-settlement-methodology Step 2 — idle underlying in lending pools).
    # The underlying must be a par-stable (USDS, DAI, USDC at $1).
    # Only meaningful for Cat C/D (Aave aToken / SparkLend spToken) venues.
    lending_idle_usds: bool = False
    # Sky Savings Token flag. When True, the venue token is the Sky Savings
    # vault (sUSDS or a per-chain canonical wrapper) and its revenue treatment
    # differs from normal Cat B:
    #   prime_revenue = value_som × 30bps_daily × n_days  (spread only)
    # The SSR appreciation is NOT Prime Revenue — the prime already receives
    # SSR through the sUSDS share price, so also crediting it in the settlement
    # model would double-count (total = 2×SSR − BR > 0, overcrediting by ~3.7%/yr).
    # Economic intent: net = SSR (token gain) + 30bps (Prime Rev) − BR (Sky Rev) = 0.
    # Applies to all direct sUSDS holdings regardless of chain or venue type
    # (raw ALM, LP token, etc.). Set explicitly in the prime YAML config.
    sky_savings_token: bool = False


@dataclass(frozen=True, slots=True)
class CurveIdleUsdsConfig:
    """Per-venue config for tracking a specific coin inside a Curve LP pool.

    Two behaviours depending on ``sky_savings_token``:

    * **Par-stable coin** (``sky_savings_token=False``, e.g. USDS, USDC):
      prime's proportional share of the pool's coin reserve is computed daily
      and subtracted from ``utilized`` at face value ($1 per unit).

    * **sUSDS / Sky Savings Token** (``sky_savings_token=True``):
      The coin balance is NOT subtracted from ``utilized`` — the yield flows
      back to Sky via the borrow-rate charge. Instead the prime earns only the
      30 bps spread on its sUSDS-equivalent daily value, which is added to
      Prime Revenue. Requires ``convertToAssets`` to price sUSDS→USDS.

    ``sde_coin`` is optional and independent of the above. When set, the named
    coin's balance (par-stable, priced at $1/unit) is used as the SDE asset
    value for ``compute_sky_revenue`` utilisation exclusion, in place of the
    RWA NAV-oracle path. Use when the SDE exposure is a *different* coin from
    ``coin`` (e.g. S24: ``coin``=sUSDS for spread revenue, ``sde_coin``=USDT
    for the Sky Direct exposure). The coin must be in
    ``KNOWN_PAR_STABLES_ETHEREUM``.

    NOTE — mid-period SDE activation not yet pro-rated: if the SDE entry's
    ``start_date`` falls within a settlement month the SDE is either active for
    the full period (start_date ≤ period_start) or skipped entirely (start_date
    > period_start). Daily pro-rating within the first partial month has not
    been implemented. See ``config/sky_direct_exposures.yaml`` for the full
    caveat.
    """

    coin: Address          # address of the target coin in the Curve pool
    sky_savings_token: bool = False  # True → 30bps spread to Prime Revenue; no utilized deduction
    sde_coin: "Address | None" = None  # par-stable coin that is the SDE exposure (optional)


class PsmKind(StrEnum):
    """How USDS-equivalent value at a PSM is computed.

    * ``directed_flow`` — Sky LITE-PSM-USDC pattern (configured for Grove and
      Spark on Ethereum; would also fit OBEX). We track net flow of ``token``
      ``ALM → PSM − PSM → ALM`` and treat the running cumulative as "USDS the
      prime has parked at the PSM". Today (2026-05) mainnet LITE-PSM is
      non-custodial for USDS, so this returns $0; the path is kept live for
      any future custodial behavior. Subproxy flows are NOT tracked — subproxy
      holds treasury / risk capital / realized revenue (PRD §17.7), which is
      NOT part of cum_debt; including it would over-reimburse BR. Empirically
      verified: zero subproxy→PSM USDS flow over Grove + Spark full lifetimes.
    * ``erc4626_shares`` — Spark PSM3 pattern (used on Base/Arbitrum/Optimism
      /Unichain). PSM3 has a non-standard ABI: shares are *internal accounting*
      (no ERC-20 Transfer events) and the rate uses
      ``convertToAssetValue(uint256)`` returning the USDS-equivalent value
      directly. We snapshot ``convertToAssetValue(shares(alm, b), b)`` at each
      day's EoD block. The ``token`` field is unused.

    Note on ``cum_balance`` semantics across kinds (relevant if a new kind is
    ever added): for ``directed_flow`` it is a running cumulative sum of
    daily net flows; for ``erc4626_shares`` it is a daily snapshot of the
    USDS-equivalent valuation. Both are consumed via ``cum_at_or_before`` in
    ``compute_sky_revenue`` which reads "value-as-of-date" — equivalent for
    that consumer, but a future PsmKind must produce something that has the
    same "value-as-of-date" reading semantics on the ``cum_balance`` column.
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
    # Per-chain PSM config. Each chain may have at most one PSM today; if a
    # future prime needs multiple, this becomes ``dict[Chain, list[PsmConfig]]``.
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
