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
class CashDistributionSource:
    """A specific (payer, token, chain) that distributes realized yield to the ALM.

    Used for venues where the position itself has no MtM revenue but the
    issuer periodically sweeps cash yield to the prime's ALM proxy — e.g.
    CLO tranches paying monthly USDC distributions.

    ``chain`` defaults to ``venue.chain`` when ``None``, but can be overridden
    when the yield distribution lands on a *different* chain from the position
    (e.g. GACLO-1 is an Avalanche token but Galaxy's USDC sweep targets the
    Ethereum ALM).
    """

    payer: Address    # address that sends the cash yield to the ALM
    token: Address    # ERC-20 token being distributed (e.g. USDC)
    chain: "Chain | None" = None  # chain of the distribution; defaults to venue.chain


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
    # ``underlying_decimals`` is required when ``kind == "erc4626"`` and ignored
    # by all other oracle kinds. It is the decimal count of the vault's
    # underlying asset (e.g. 6 for USDC, 18 for DAI/USDS) — the divisor applied
    # to the raw ``convertToAssets`` return value to produce a dollar NAV.
    # The share-token decimal count (the ``convertToAssets`` input exponent) is
    # taken from ``venue.token.decimals`` automatically, since for cross-chain
    # RWA vaults the share token on-chain IS the venue token.
    underlying_decimals: int | None = None
    fallback: str | None = None
    fallback_address: Address | None = None
    fallback_underlying_decimals: int | None = None
    fallback2: str | None = None
    fallback2_address: Address | None = None
    fallback2_underlying_decimals: int | None = None
    oracle_chain: "Chain | None" = None


@dataclass(frozen=True, slots=True)
class NotionalScheduleEntry:
    """One step of a venue's off-chain notional-principal schedule.

    Used by ``Venue.notional_principal_usd`` for cash-distribution-only venues
    where the ALM's on-chain balance is $0 but Sky is charging interest on
    a real off-chain principal (loan, tri-party escrow, etc.).
    """
    start_date: date
    amount: Decimal


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
    lp_kind: str | None = None           # Category F only: 'curve_stableswap' | 'uniswap_v3' | 'uniswap_v4'
    nft_position_manager: Address | None = None  # Category F (uniswap_v3 / uniswap_v4) — NFT PositionManager
    # Uniswap V4 only. V4 is a singleton (no per-pool contract): the pool is
    # identified by ``univ4_pool_key`` (→ poolId = keccak256(abi.encode(key)))
    # and the holder's positions are listed explicitly in ``univ4_token_ids``
    # because the v4 PositionManager is not ERC-721-enumerable. See
    # ``extract/uniswap_v4.py`` and ``normalize/sources/uniswap_v4.py``.
    univ4_pool_key: "UniV4PoolKey | None" = None
    univ4_token_ids: tuple[int, ...] = ()
    # When True, this venue's avg_value is excluded from the CoF allocation
    # denominator in post-hoc reporting (build_monthly_report). Two distinct
    # use cases share this flag:
    #   (a) idle-ALM positions (raw USDS/USDC at the ALM proxy — S31, S38,
    #       S44, S48, S52) that are already deducted from `utilized` via
    #       cum_alm_usds. Allocating CoF to them would produce equal-and-
    #       opposite P2S / P2G entries that both should be 0.
    #   (b) Savings V2 vaults (S56/S57/S59/S60) whose capital is depositor-
    #       funded (USDC/USDT/PYUSD held against pp-share liabilities) and
    #       was never drawn from the ilk — so Sky never funded it and Sky's
    #       CoF should not be split against it. These are NOT in
    #       cum_alm_usds; their "utilized deduction" is $0.
    # The grove-sheet renderer distinguishes the two by
    # ``pricing_category`` (S2 ⇒ Savings V2 depositor capital) to set the
    # correct ``deduction_avg`` and the right xlsx note text.
    cof_excluded: bool = False
    # When True, this venue's PnL columns (actual_revenue / revenue /
    # sd_revenue / profit_to_grove) are suppressed in per-venue display
    # surfaces — the settlement summary.md "## Per-venue" table and the
    # xlsx "Venues" tab. Positions (value_som / value_eom) are surfaced
    # separately so the BA Labs balance-sheet reconciliation isn't lost.
    # Set on the Spark Savings V2 vaults (S56/S57/S59/S60), which are
    # position-only (revenue $0 — the VSR depositor liability is outside
    # the MSC/ALM accounting boundary); a per-vault $0 PnL row would be
    # noise next to the real position values.
    hide_per_venue_pnl: bool = False
    # Per-venue minimum transfer threshold (USD-equivalent). Drops transfers
    # below this amount from the cumulative-balance pull so daily
    # yield-distribution mints (BUIDL-style) don't contaminate the
    # capital-inflow stream. Plumbed to
    # ``IBalanceSource.cumulative_balance_timeseries(min_transfer_amount=…)``.
    # ``None`` means no filter (default).
    min_transfer_amount_usd: Decimal | None = None
    # Off-chain administrative fee charged by the issuer on some capital
    # operations. Today used by BlackRock BUIDL-I (E10, Grove): the issuer
    # takes $15K per fee-charged operation, baked into the on-chain mint
    # amount at source.
    #
    # The compute layer detects fee-charged events by the "shaved-amount"
    # signature on each in-period row of ``inflow_timeseries``:
    #
    #   * Shaved (fee charged): ``49,985,000 + 15,000 = 50,000,000``  →
    #     divisible by $1M → counted as a fee event.
    #   * Clean (no fee):       ``50,000,000 + 15,000 = 50,015,000``  →
    #     NOT divisible by $1M → skipped.
    #
    # Detection is direction-agnostic via ``abs(daily_inflow)`` — works for
    # both subscriptions and redemptions when fee-charged. ``fee × n_events``
    # is subtracted from ``actual_revenue`` before the SDE split; for fixed-
    # SDE venues (BUIDL is) the fee flows entirely to Sky. The $1M rounding
    # constant lives in ``compute_venue_revenue`` and matches BlackRock's
    # institutional subscription denomination — re-validate if a future
    # venue adopts a different one.
    #
    # ``None`` means no fee. Setting this without a positive
    # ``min_transfer_amount_usd`` raises — the unfiltered daily yield mints
    # would corrupt the detection.
    fixed_fee_per_capital_event_usd: Decimal | None = None
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
    # Realized cash yield streams paid directly to the ALM by a known payer —
    # e.g. monthly USDC distributions from CLO issuers. The compute layer sums
    # actual on-chain transfers and records the total as ``actual_revenue_override``
    # (prime-only; no sky-revenue or capital-inflow effect).
    cash_distributions: "list[CashDistributionSource]" = field(default_factory=list)
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
    #   prime_revenue = 0  (SSR accrues via share price; no additional credit)
    #   sky_revenue_reduction = value_som × 30bps_daily × n_days  (spread
    #     deducted from Sky Revenue, surfaced as susds_spread_reimbursement)
    # The SSR appreciation is NOT Prime Revenue — the prime already receives
    # SSR through the sUSDS share price, so also crediting it in the settlement
    # model would double-count (total = 2×SSR − BR > 0, overcrediting ~3.7%/yr).
    # Economic intent: prime cost = SSR × V, Sky net = SSR × V.
    # Applies to all direct sUSDS holdings regardless of chain or venue type
    # (raw ALM, LP token, etc.). Set explicitly in the prime YAML config.
    # See docs/METHODOLOGY.md §1 special-case and docs/RULES.md Rule 5.
    sky_savings_token: bool = False
    # When True, subtract the daily Savings V2 deployed_amount from this
    # venue's USD value (value_som, value_eom, tw_avg). Used for S32
    # (sUSDS raw / POL at Spark ETH ALM) where the ALM's sUSDS balance
    # includes shares deployed into Savings V2 that are not truly held
    # at the ALM proxy. Requires sky_savings_token: true.
    deduct_savings_v2_deployed: bool = False
    # When True (and sky_savings_token is also True), the 30 bps spread
    # reimbursement is NOT deducted from Sky Revenue in this settlement report.
    # Use when the spread is accounted for elsewhere — specifically when the
    # sUSDS held here is collateralising demand-side deposits (e.g. Spark
    # Savings / Savings V2 on Ethereum), and the reimbursement will be applied
    # as part of Demand Side Distribution Rewards rather than Supply Side PnL.
    # Sky still charges full BR on utilized; prime_revenue remains 0.
    demand_side_spread: bool = False
    # Additional addresses to treat as "burn destinations" when classifying
    # share Transfers for Cat B inflow accounting. ERC-4626 vaults with a
    # withdrawal-queue pattern (Maple PoolV2 etc.) Transfer the user's
    # shares to a queue contract before the actual share burn happens
    # in-tx — without this list the inflow classifier sees only the gross
    # deposit (Transfer from 0x0 → ALM) and misses the redemption
    # (Transfer from ALM → queue), producing a phantom loss equal to the
    # gross redeem amount. See Q-S26 in QUESTIONS.md for the Maple case
    # (Spark Apr 2026: ~$400M of phantom loss on S14/S15).
    share_burn_destinations: list[Address] = field(default_factory=list)
    # ERC-4626 vault contract address for Centrifuge (and similar) venues where
    # the ALM deposits / withdraws via a vault rather than secondary-market
    # transfers.  When set, the Cat E inflow path uses ``Deposit`` /
    # ``Withdraw`` event ``assets`` amounts (exact USDC in/out) instead of
    # ``net_token_balance × NAV``.  This matches the external-party cash-flow
    # methodology and keeps capital inflows free of intra-day NAV noise.
    #
    # Revenue = EOM − SOM − inflow correctly captures yield accrual as the
    # residual — no separate yield calculation is needed.
    #
    # Sanity check: the pipeline verifies that the share balance implied by the
    # events (SOM shares + Σdeposit_shares − Σwithdraw_shares) matches the
    # actual on-chain EOM balance.  A mismatch signals share movements not
    # emitted as Deposit/Withdraw (e.g. direct ERC-20 transfers), and a warning
    # is logged.
    centrifuge_vault: "Address | None" = None
    # Category EOA only — pairing config for the "principal-out / return-in"
    # roundtrip pattern (see PricingCategory.EOA docstring).
    #
    # ``paired_with`` is the venue id of the *anchor* venue where the return
    # asset lands at the ALM proxy (typically a Cat B / Cat E venue tracking
    # the returned asset). ``paired_source`` is the address that, when seen
    # as the *sender* of a credit to the anchor venue's holder, triggers a
    # paired drain on this venue's balance.
    #
    # Together they implement:
    #   balance(this) = Σ(ALM→holder outflows in venue.token)
    #                 − Σ(paired_source → anchor.holder inflows in anchor.token,
    #                     converted to venue.token units at par)
    #
    # Both fields are required for category EOA and ignored elsewhere.
    paired_with: str | None = None
    paired_source: Address | None = None
    # Off-protocol / "tracked-but-not-counted" flag. When True, the venue's
    # value is computed and surfaced in reports (so off-protocol holdings
    # remain visible in the monthly file) but the venue is EXCLUDED from:
    #   - ``prime_agent_revenue`` (no actual_revenue contribution)
    #   - ``sky_revenue`` (not added to or subtracted from any sky-side stream)
    #   - the cost-basis NAV invariant (Σ value over operating venues only)
    # Realized gains/losses on returns are recognized at the *anchor* venue
    # (see ``paired_with`` / ``paired_source``) via the Cat A paired-principal-
    # cap classifier in ``_cat_a_capital_inflow_timeseries``: inflows from
    # ``paired_source`` up to the cumulative ALM→holder principal-out are
    # classified as capital (principal-return); excess is yield (revenue).
    # Typical use: principal sent to an off-protocol counterparty (e.g.
    # FalconX) for an OOB acquisition; the cash settlement at the ALM is the
    # realization event for any spread captured during the trip.
    display_only: bool = False
    # Force all balance changes to be treated as capital inflow (revenue = 0).
    # Use for Cat A par-stable venues on chains without reliable transfer-event
    # data (e.g. Monad): the pipeline cannot distinguish capital movements from
    # yield, so we conservatively declare revenue = 0 and attribute the full
    # Δvalue to inflow. The venue still participates in CoF allocation via its
    # tw_avg_value_usd. Only valid on PricingCategory.PAR_STABLE venues.
    force_capital_inflow: bool = False
    # Declares that this Cat A par-stable venue receives realized yield from an
    # off-chain / external source (an ``external_alm_sources`` sender), e.g.
    # Spark S26 (Anchorage USDC sweeps) and S28 (PayPal/Paxos PYUSD rewards).
    # Default False: a par-stable holding earns NO yield by itself, so its
    # revenue is $0 by construction and the compute layer attributes the whole
    # Δvalue to capital (any residual is a transfer-capture artifact, never
    # yield — this is what fixed Spark S27 −$194,444 and Grove E13/E31/E32).
    # When True, the Cat A classifier runs and must reconcile the external
    # inflows; if it can't (empty counterparty log with in-period movement) it
    # raises a capture-gap error rather than silently zeroing real yield.
    # A future external source on a currently-idle venue MUST flip this to True.
    external_yield_source: bool = False
    # Off-chain notional principal used by the CoF allocation when on-chain
    # ``tw_avg_value_usd`` doesn't reflect the principal Sky is implicitly
    # charging interest on. Primary use case: cash-distribution-only venues
    # (e.g. Galaxy CLO E21, Anchorage tri-party S23 yield routing through S26)
    # where the ALM's on-chain balance is $0 but the funded USDS principal
    # ($50M loan, $150M tri-party) is still part of utilized.
    #
    # Two forms accepted in YAML:
    #
    #   notional_principal_usd: 50000000              # constant scalar
    #
    # OR (date-ranged schedule for time-varying notional, e.g. partial
    # repayments, drawdowns, term-end retirement):
    #
    #   notional_principal_usd:
    #     - start_date: '2025-12-19'  # initial disbursement
    #       amount: 50000000
    #     - start_date: '2026-06-16'  # loan termination
    #       amount: 0
    #
    # Each entry sets the notional from ``start_date`` onward (step function).
    # The compute layer computes a time-weighted average across the settlement
    # period; the sheet builder uses ``max(tw_avg_value_usd, tw_avg_notional_usd)``
    # as the effective avg for CoF allocation. For venues where on-chain value
    # tracks the real notional (the typical case), this field is None and the
    # effective avg = tw_avg_value_usd unchanged.
    notional_principal_usd: tuple[NotionalScheduleEntry, ...] | None = None

    def __post_init__(self) -> None:
        # ``force_capital_inflow`` short-circuits the Cat A capital-inflow
        # path (see ``compute.monthly_pnl``). It synthesises inflow = Δvalue
        # so revenue collapses to 0, which is ONLY a defensible default for
        # par-stable venues — Cat B/C/D/E venues have legitimate yield in
        # the NAV/price/share that we must not silently zero. Reject misuse
        # at config-load time so a YAML typo can't silently lose revenue.
        if self.force_capital_inflow and self.pricing_category != PricingCategory.PAR_STABLE:
            raise ValueError(
                f"Venue {self.id}: force_capital_inflow is only valid on "
                f"PricingCategory.PAR_STABLE venues (got "
                f"{self.pricing_category.name}). The flag's revenue=0 "
                f"semantics only makes sense for par-stable positions on "
                f"chains without reliable transfer-event coverage."
            )


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
    sky_savings_token: bool = False  # True → 30bps spread deducted from Sky Revenue; no utilized deduction
    sde_coin: "Address | None" = None  # par-stable coin that is the SDE exposure (optional)


@dataclass(frozen=True, slots=True)
class UniV4PoolKey:
    """A Uniswap V4 ``PoolKey``. ``currency0 < currency1`` (sorted), ``fee`` in
    hundredths of a bip, ``tick_spacing`` per pool, ``hooks`` = 0x0 for an
    unhooked pool. The poolId is ``keccak256(abi.encode(PoolKey))`` — computed
    in the extract layer (``extract.uniswap_v4.V4PoolKey.pool_id``)."""

    currency0: Address
    currency1: Address
    fee: int
    tick_spacing: int
    hooks: Address


class PsmKind(StrEnum):
    """How USDS-equivalent value at a PSM is computed.

    * ``erc4626_shares`` — Spark PSM3 pattern (Base / Arbitrum / Optimism /
      Unichain). PSM3 is custodial: the prime's ALM holds shares against a
      basket of USDC + USDS + sUSDS reserves. PSM3 has a non-standard ABI:
      shares are *internal accounting* (no ERC-20 Transfer events) and the
      rate uses ``convertToAssetValue(uint256)`` returning the USDS-equivalent
      value of N shares directly. We snapshot
      ``convertToAssetValue(shares(alm, b), b)`` at each day's EoD block,
      then decompose into per-leg values (USDC / USDS / sUSDS) for the
      methodology routing in PRD §17.11.

    History note: an earlier ``directed_flow`` kind was deprecated and
    removed (2026-05-11) after the on-chain mechanics for Sky's mainnet
    LITE-PSM stack (DssLitePsm + DaiUsds converter + USDC pocket EOA +
    UsdsPsmWrapper) were traced end-to-end. The mainnet stack is
    non-custodial — no per-prime balances accumulate at any contract or
    pocket; primes only transit through it as atomic swaps. There's
    nothing to "track" on mainnet that the venue/Cat-A paths don't
    already cover. See PRD §17.11.
    """

    ERC4626_SHARES = "erc4626_shares"


@dataclass(frozen=True, slots=True)
class PsmConfig:
    """Per-chain PSM configuration. Holdings are subtracted from ``utilized``
    in ``compute_sky_revenue`` so the prime is reimbursed BR on the parked
    capital (prime-settlement-methodology Step 2)."""

    kind: PsmKind
    address: Address
    # Currently unused — historical field from the deprecated ``directed_flow``
    # kind which tracked a specific token's flow in/out of a PSM. Retained as
    # an optional config slot in case a future PsmKind needs to name a
    # specific underlying token.
    token: Address | None = None


@dataclass(frozen=True, slots=True)
class PrincipalReturnOverride:
    """A single (date, amount)-matched transfer-event override used by the
    Cat A classifier. Two consumers, opposite directions:

    * ``Prime.principal_return_overrides`` — an INFLOW from an
      ``external_alm_sources`` address that should NOT be classified as
      yield (principal-return on a tri-party loan or similar).
    * ``Prime.yield_reversal_overrides`` — an OUTFLOW to an
      ``external_alm_sources`` address that returns over-received yield
      (reclassified capital → negative yield).

    Amounts are in whole USD units (par-stable assumption — same scaling
    as ``inflow_by_counterparty.signed_amount``). Matching tolerates ±$1
    of rounding noise.

    **Matching operates on DAY-NET amounts**: ``inflow_by_counterparty``
    aggregates flows per (date, counterparty) into a single net row, so
    an entry only matches when the registered amount equals the whole
    day's net flow for that counterparty (±$1). If the target transfer
    shares its day with other flows to/from the same address, the day-net
    differs from the transfer amount and the entry silently never
    matches — verify the day-net on-chain before registering. Also note:
    addresses are NOT validated against ``external_alm_sources`` at
    config-load time — an override registered for a non-external address
    silently never matches.
    """

    date: date
    amount: Decimal
    token: str = ""    # token symbol — sanity check for human readers
    note: str = ""


@dataclass(frozen=True, slots=True)
class Prime:
    """A Sky prime agent — ilk, addresses per chain, allocation venues."""

    id: str                              # 'obex' | 'grove' | 'spark' | 'skybase' | …
    # 32-byte ilk identifier. ``None`` for agent-rate-only primes (Keel,
    # Skybase): no allocator ilk → no debt, no BR charge, no supply-side
    # venues — settlement reduces to the agent rate on subproxy treasury
    # holdings (``get_debt_timeseries`` returns an all-zero series).
    ilk_bytes32: bytes | None
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
    # Mirror of ``principal_return_overrides`` for the OPPOSITE direction:
    # per-(chain, source) overrides for OUTFLOWS from the ALM to an external
    # ALM source that are a return of over-received yield (e.g. Spark
    # reimbursing Anchorage $5M on 2026-05-19 after the over-sized May 14
    # payment). The Cat A classifier matches by (date, |amount| within $1)
    # and reclassifies matching outflows as NEGATIVE yield (netting against
    # that source's inflows) instead of the default capital classification.
    # The directional default stays capital — principal disbursements to
    # escrows must never read as negative yield — so every reversal is an
    # explicit, auditable entry. Reuses the ``PrincipalReturnOverride``
    # dataclass (same (date, amount, token, note) shape and matching
    # semantics).
    yield_reversal_overrides: dict[
        Chain, dict[Address, list["PrincipalReturnOverride"]]
    ] = field(default_factory=dict)
    # Subsidised borrowing rate config. Default = disabled (legacy behavior:
    # full BR on utilized). When enabled, Sky charges subsidised rate on the
    # first ``cap_usd`` of utilized USDS; any excess at full BR.
    subsidy: SubsidyConfig = field(default_factory=lambda: SubsidyConfig(enabled=False))

    def __post_init__(self) -> None:
        if self.ilk_bytes32 is not None and len(self.ilk_bytes32) != 32:
            raise ValueError(f"ilk_bytes32 must be 32 bytes; got {len(self.ilk_bytes32)}")
        if self.ilk_bytes32 is None and self.venues:
            raise ValueError(
                f"prime {self.id!r}: venues configured without an ilk_bytes32 — "
                "supply-side venues need ilk debt for the BR charge. Either add "
                "the allocator ilk or remove the venues (agent-rate-only prime)."
            )

    @property
    def chains(self) -> set[Chain]:
        return set(self.alm.keys()) | set(self.subproxy.keys())
