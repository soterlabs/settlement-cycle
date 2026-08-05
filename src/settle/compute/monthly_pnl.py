"""Top-level orchestrator: gather Normalize inputs, run Compute, return MonthlyPnL.

The only place where Normalize and Compute meet. Resolves SoM / EoM blocks via
RPC unless overridden, then walks every venue for value snapshots + inflow
timeseries before composing the three revenue components.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pandas as pd

from ..domain.monthly_pnl import MonthlyPnL, VenueRevenue
from ..domain.period import Month, Period
from ..domain.pricing import PricingCategory
from ..domain.primes import Chain, Prime, PsmKind
from ..domain.sde import load_sde_table
from ..domain.sky_tokens import USDS_ETHEREUM, sUSDS_ETHEREUM
from ..domain.subsidy import load_reference_rates_for
from ..normalize import (
    get_debt_timeseries,
    get_position_value,
    get_ssr_history,
    get_subproxy_balance_timeseries,
    get_venue_inflow_timeseries,
)
from ..normalize.prices import _resolve_rwa_nav
from ..normalize.protocols import (
    IBalanceSource,
    IBlockResolver,
    IConvertToAssetsSource,
    ICurvePoolSource,
    IDebtSource,
    IPositionBalanceSource,
    IPsm3Source,
    ISavingsV2DeployedSource,
    ISSRSource,
    IV3PositionSource,
    IV4PositionSource,
)
from ..normalize.registry import (
    get_balance_source,
    get_block_resolver,
    get_convert_to_assets_source,
    get_savings_v2_deployed_source,
)
from ._helpers import cum_at_or_before
from .agent_rate import compute_agent_rate
from .prime_agent_revenue import VenueRevenueInputs, compute_prime_agent_revenue
from .sky_revenue import compute_sky_revenue_daily

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Sources:
    """Optional source overrides — pass for tests, leave None to use registry defaults."""

    debt: IDebtSource | None = None
    balance: IBalanceSource | None = None
    ssr: ISSRSource | None = None
    position_balance: IPositionBalanceSource | None = None
    convert_to_assets: IConvertToAssetsSource | None = None
    psm3: IPsm3Source | None = None
    block_resolver: IBlockResolver | None = None
    v3_position: IV3PositionSource | None = None
    v4_position: IV4PositionSource | None = None
    curve_pool: ICurvePoolSource | None = None
    savings_v2_deployed: ISavingsV2DeployedSource | None = None
    # Optional NAV-oracle resolver: ``Callable[[str], INavOracleSource]`` that
    # overrides the registry lookup. Used by acceptance scripts to inject
    # historical-NAV overrides without monkey-patching ``_NAV_ORACLE_SOURCES``.
    nav_oracle_resolver: object = None
    # Optional aToken Transfer-event log lookup. Signature:
    #   ``(chain: str, token: bytes, holder: bytes, som: int, eom: int) -> list[int]``
    # Returns the SORTED list of block numbers where Transfer events involving
    # ``holder`` occurred within ``(som, eom]``. When set, the Cat C
    # per-segment yield path uses sub-day-resolution event boundaries
    # (one boundary per event block) instead of falling back to
    # end-of-day boundaries derived from daily mint/burn aggregates.
    atoken_event_blocks: object = None


def _sources_from_prime(prime: "Prime", base: "Sources | None" = None) -> Sources:
    """Merge ``prime.sources`` config overrides (registry-resolved) into ``base``.

    Fill semantics: a field the CALLER set explicitly on ``base`` always wins;
    a field left ``None`` is filled from the prime's YAML ``sources:`` block if
    declared there; otherwise it stays ``None`` (→ registry defaults at each
    call site, identical to the prior behaviour). This is the per-prime
    migration hook: a prime's YAML flips individual backends (e.g.
    ``debt: hypersync``) without touching any other prime — and it must apply
    on EVERY entry point (CLI, production runners, snapshot), not only when
    ``compute_monthly_pnl`` is called with ``sources=None``; two entry points
    silently settling the same prime on different backends is the failure
    this merge exists to prevent.
    """
    import dataclasses

    from ..normalize.registry import SOURCE_FAMILIES

    ov = getattr(prime, "sources", None) or {}
    base = base if base is not None else Sources()
    kw: dict[str, object] = {}
    # Table-driven over the SAME family map config validation uses — a family
    # present in one but not the other can't silently no-op.
    for family, (_registry, getter) in SOURCE_FAMILIES.items():
        if family in ov and getattr(base, family) is None:
            kw[family] = getter(ov[family])
    return dataclasses.replace(base, **kw) if kw else base  # type: ignore[arg-type]


def _previous_day_eod_utc(d) -> datetime:
    return datetime.combine(d - timedelta(days=1), time.max, tzinfo=timezone.utc)


def _merge_cap_series(df1, df2):
    """Sum two cumulative ``[block_date, cum_inflow]`` series into one.

    Used when multiple display-only EOA venues share the same
    ``paired_source`` — the pooled cap consumed by anchor inflows is the sum
    of each leg's cumulative principal-out at every date, not just one
    leg's. See the auto-wiring loop in ``compute_monthly_pnl`` (Cat A
    branch) for the collision handling.
    """
    import pandas as _pd
    from ._helpers import cum_at_or_before as _cum
    if df1 is None or df1.empty:
        return df2
    if df2 is None or df2.empty:
        return df1
    all_dates = sorted(set(df1["block_date"]) | set(df2["block_date"]))
    rows = [
        {"block_date": d,
         "cum_inflow": _cum(df1, "cum_inflow", d) + _cum(df2, "cum_inflow", d)}
        for d in all_dates
    ]
    return _pd.DataFrame(rows)


def _build_paired_principal_caps(prime, venue, period, balance_src) -> dict:
    """Build the ``paired_principal_caps`` map for a Cat A anchor venue.

    For each display-only venue whose ``paired_with`` matches ``venue.id``,
    fetch its cumulative ALM→holder outflow series and key it by the
    venue's ``paired_source`` address. Multiple display-only venues sharing
    the same ``paired_source`` get their cap series summed via
    ``_merge_cap_series``. The result is the ``paired_principal_caps``
    argument to ``_cat_a_capital_inflow_timeseries``.

    Filter order is documented inline below — kept identical to the prior
    inline loop so the refactor is behaviour-preserving. Extracted into a
    standalone helper for unit-test coverage (see
    ``tests/unit/test_paired_principal_caps.py``).
    """
    paired_principal_caps: dict = {}
    for eoa_v in prime.venues:
        if not eoa_v.display_only:
            continue
        if eoa_v.skip:
            # ``skip`` takes precedence over ``display_only`` for
            # compute purposes: a wound-down venue should not drive
            # the anchor's cap, even if it's still listed for
            # reporting. Without this guard the skipped venue's
            # principal-out series would silently reclassify real
            # anchor inflows as capital, mis-attributing revenue.
            continue
        if eoa_v.paired_with != venue.id:
            continue
        if eoa_v.paired_source is None or eoa_v.holder_override is None:
            continue
        if eoa_v.chain != venue.chain:
            # Cross-chain paired-cap isn't supported by the current
            # directed_inflow_timeseries (which is single-chain). The
            # display-only venue setup helper in normalize.positions
            # already enforces this; skip silently here.
            continue
        if eoa_v.chain not in prime.alm or eoa_v.chain not in period.pin_blocks:
            # Defensive: a display-only venue configured for a chain
            # where the prime has no ALM address (or where the period
            # has no pin_block) would otherwise KeyError mid-loop
            # with a hard-to-diagnose stack trace. Skip with a
            # warning so the operator sees which venue tripped it.
            _log.warning(
                "  paired-cap: skipping display-only venue %s — "
                "chain %s missing from prime.alm or period.pin_blocks.",
                eoa_v.id, eoa_v.chain.value,
            )
            continue
        cap_df = balance_src.directed_inflow_timeseries(
            chain=eoa_v.chain.value,
            token=eoa_v.token.address.value,
            from_addr=prime.alm[eoa_v.chain].value,
            to_addr=eoa_v.holder_override.value,
            start=prime.start_date,
            pin_block=period.pin_blocks[eoa_v.chain],
        )
        src_key = eoa_v.paired_source.value
        if src_key in paired_principal_caps:
            # Two or more display-only EOAs share the same
            # ``paired_source``. The cap is the SUM of their
            # principal-out series — return inflows from the shared
            # counterparty consume the pooled cap, not just one
            # leg's. Without this, the second insert would silently
            # overwrite the first and reclassify legitimate
            # principal-returns as yield.
            paired_principal_caps[src_key] = _merge_cap_series(
                paired_principal_caps[src_key], cap_df,
            )
        else:
            paired_principal_caps[src_key] = cap_df
    return paired_principal_caps


def _check_centrifuge_in_flight(
    prime: "Prime",
    pin_blocks_som: dict["Chain", int],
    pin_blocks_eom: dict["Chain", int],
) -> None:
    """Warn if any Centrifuge vault has pending/claimable deposit or redeem
    requests in-flight at the SoM or EoM pin block.

    ERC-7540 async vaults split every deposit or redemption into two steps:
    *request* → epoch processing → *claim*.  If a request is in-flight at a
    settlement boundary the SoM/EoM share balance is incorrect — shares or
    assets are held in the vault's escrow rather than in the prime ALM wallet,
    and the pipeline cannot automatically account for them.  This check logs a
    loud WARNING so the operator can manually verify and correct the numbers.

    Contracts not yet deployed at a given block are silently skipped to handle
    venues that were onboarded mid-period.
    """
    from ..extract.rpc import (
        eth_call as _eth_call,
        is_contract_deployed as _is_deployed,
        SEL_PENDING_DEPOSIT_REQUEST as _SEL_PD,
        SEL_CLAIMABLE_DEPOSIT_REQUEST as _SEL_CD,
        SEL_PENDING_REDEEM_REQUEST as _SEL_PR,
        SEL_CLAIMABLE_REDEEM_REQUEST as _SEL_CR,
    )

    _selectors: list[tuple[str, str]] = [
        ("PENDING DEPOSIT",   _SEL_PD),
        ("CLAIMABLE DEPOSIT", _SEL_CD),
        ("PENDING REDEEM",    _SEL_PR),
        ("CLAIMABLE REDEEM",  _SEL_CR),
    ]

    # ERC-7540 uses a single request-ID per (vault, controller).
    _REQUEST_ID = 0

    def _query_uint(vault: "Address", holder: "Address", chain: "Chain",
                    selector: str, block: int) -> int:
        from ..extract._abi import pad_uint as _pu, pad_address as _pa
        data = selector + _pu(_REQUEST_ID) + _pa(holder)
        try:
            raw = _eth_call(chain, vault, data, block)
            return int(raw, 16) if raw and raw not in ("0x", "0x0") else 0
        except Exception:  # noqa: BLE001
            return 0

    for venue in prime.venues:
        if venue.centrifuge_vault is None or venue.skip:
            continue
        chain = venue.chain
        if chain not in pin_blocks_som or chain not in pin_blocks_eom:
            continue
        holder = venue.holder_override or prime.alm.get(chain)
        if holder is None:
            continue

        vault = venue.centrifuge_vault

        for block_label, block in [("SoM", pin_blocks_som[chain]),
                                    ("EoM", pin_blocks_eom[chain])]:
            if not _is_deployed(chain, vault, block):
                _log.info(
                    "  centrifuge check: %s vault %s not yet deployed at %s block %d — skipping",
                    venue.id, vault.hex, block_label, block,
                )
                continue

            for label, selector in _selectors:
                amount = _query_uint(vault, holder, chain, selector, block)
                if amount:
                    # Use a dedicated asset decimals field if available; default
                    # to 6 (USDC) which is correct for all current Centrifuge
                    # venues (JAAA, JTRSY) — update if 18-decimal underlyings
                    # are added.
                    decimals = getattr(getattr(venue, "underlying", None), "decimals", None) or 6
                    amount_fmt = f"{amount / 10**decimals:,.6f}"
                    _log.warning(
                        "IN-FLIGHT CENTRIFUGE REQUEST — MANUAL REVIEW REQUIRED: "
                        "venue %s (%s) has a %s of %s at the %s pin block (%d). "
                        "THE %s VALUE AND INFLOW FOR THIS VENUE MAY BE INCORRECT "
                        "AND MAY REQUIRE MANUAL CORRECTION.",
                        venue.id, venue.token.symbol,
                        label.upper(), amount_fmt, block_label, block,
                        block_label.upper(),
                    )


def _resolve_pin_blocks(
    anchor_utc: datetime,
    chains: set[Chain],
    resolver: IBlockResolver,
) -> dict[Chain, int]:
    """Resolve ``last block_number with timestamp ≤ anchor_utc`` per chain.

    Chains are resolved in parallel (ThreadPoolExecutor) to amortise the
    ~25 binary-search RPC calls each one requires. For Spark (6 chains) this
    cuts wall-clock time from ~6× to ~1× the single-chain cost.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if not chains:
        return {}

    def _one(chain: Chain) -> tuple[Chain, int]:
        block = resolver.block_at_or_before(chain.value, anchor_utc)
        _log.info("  pin block resolved: %s → %d", chain.value, block)
        return chain, block

    with ThreadPoolExecutor(max_workers=min(len(chains), 8)) as pool:
        futures = [pool.submit(_one, c) for c in chains]
        return dict(f.result() for f in as_completed(futures))


def _curve_sde_asset_value_timeseries(
    prime: Prime,
    venue,
    period: Period,
    *,
    sde_coin,
    curve_pool_source,
    block_resolver,
    cap_usd: Decimal | None = None,
) -> "pd.DataFrame":
    """Daily SDE asset value for a Curve LP pool venue (par-stable SDE coin).

    Computes prime's proportional share of the named par-stable coin's reserve:

        value_d = (alm_lp_d / pool_total_d) × coin_reserve_d   (at $1/unit)

    Used instead of the RWA NAV-oracle path for Cat F venues where the SDE
    exposure is a par-stable coin (e.g. USDT in the sUSDS/USDT pool, S24).
    ``sde_coin`` must be in ``KNOWN_PAR_STABLES_ETHEREUM``.
    """
    from datetime import time
    from ..extract.rpc import balance_of as _balance_of
    from ..domain.primes import Address as _Addr
    from ..domain.sky_tokens import KNOWN_PAR_STABLES_ETHEREUM

    sde_coin_bytes = sde_coin.value
    par = KNOWN_PAR_STABLES_ETHEREUM.get(sde_coin_bytes)
    if par is None:
        raise ValueError(
            f"_curve_sde_asset_value_timeseries: SDE coin {sde_coin.hex} for venue "
            f"{venue.id} is not in KNOWN_PAR_STABLES_ETHEREUM — add it to sky_tokens.py."
        )
    _sym, coin_decimals = par

    pool_addr = venue.token.address.value
    holder = (venue.holder_override or prime.alm[venue.chain]).value
    chain_str = venue.chain.value

    rows = []
    current = period.start
    while current <= period.end:
        eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
        raw_value = Decimal("0")
        capped_value = Decimal("0")
        try:
            block = block_resolver.block_at_or_before(chain_str, eod)
            pool_state = curve_pool_source.read_pool(chain_str, pool_addr, block)
            total_supply = pool_state.total_supply
            if total_supply > 0:
                coin_idx = next(
                    (i for i, c in enumerate(pool_state.coins) if c.value == sde_coin_bytes),
                    None,
                )
                if coin_idx is not None:
                    raw_coin = pool_state.balances[coin_idx]
                    alm_lp_raw = _balance_of(
                        venue.chain, venue.token.address, _Addr(holder), block,
                    )
                    alm_lp = Decimal(alm_lp_raw) / Decimal(10 ** venue.token.decimals)
                    pool_total = Decimal(total_supply) / Decimal(10 ** venue.token.decimals)
                    coin_usds = Decimal(raw_coin) / Decimal(10 ** coin_decimals)
                    raw_value = (alm_lp / pool_total) * coin_usds
                    capped_value = min(cap_usd, raw_value) if cap_usd is not None else raw_value
                else:
                    _log.warning(
                        "curve SDE: venue %s pool %s does not contain SDE coin %s "
                        "at block %d — $0 for day %s.",
                        venue.id, pool_addr.hex(), sde_coin.hex, block, current,
                    )
        except Exception as exc:
            _log.warning(
                "curve SDE: RPC error for venue %s on %s; using $0 (error: %s).",
                venue.id, current, type(exc).__name__,
            )
        # Emit both columns so the schema matches ``_sde_asset_value_timeseries``;
        # `_capped_sd_revenue_daily_resolved` requires both. `uncapped_value` is
        # the pre-cap raw position; `cum_value` is the post-cap deduction value.
        rows.append({
            "block_date": current,
            "cum_value": capped_value,
            "uncapped_value": raw_value,
        })
        current = current + timedelta(days=1)
    return pd.DataFrame(rows)


def _univ4_default_source(venue):
    """Build the default v4 position source honoring the venue's
    ``nft_position_manager`` override (the v4 PositionManager).

    Dune-backed flows + RPC valuations — see
    ``normalize.sources.uniswap_v4.default_v4_source`` (the single
    construction point for all v4 paths).
    """
    from ..normalize.sources.uniswap_v4 import default_v4_source
    return default_v4_source(venue)


def _univ4_pool_key_for(venue):
    from ..normalize.positions import _venue_v4_pool_key
    return _venue_v4_pool_key(venue)


def _univ4_position_legs(venue, positions) -> tuple[Decimal, Decimal]:
    """Sum a v4 venue's positions into ``(idle_usds_value, sde_coin_value)`` USD.

    ``curve_idle_usds.coin`` selects the idle (USDS) leg; ``curve_idle_usds
    .sde_coin`` selects the SDE leg. Both are par-stable ($1/unit). Either may
    be absent from the pool (returns $0 for that leg).
    """
    from ..domain.sky_tokens import KNOWN_PAR_STABLES_ETHEREUM
    from ..normalize.prices import UnsupportedPricingError

    cfg = venue.curve_idle_usds
    idle_coin = cfg.coin.value if cfg is not None else None
    sde_coin = cfg.sde_coin.value if (cfg is not None and cfg.sde_coin is not None) else None

    idle_total = Decimal("0")
    sde_total = Decimal("0")
    for p in positions:
        for coin, amount_raw in ((p.currency0, p.amount0), (p.currency1, p.amount1)):
            if amount_raw == 0:
                continue
            par = KNOWN_PAR_STABLES_ETHEREUM.get(coin.value)
            if par is None:
                # Mirror the Curve pool's ``_par_stable_usds`` and the V4 value
                # path (``_uniswap_v4_value``): a non-zero position leg in a
                # coin we can't price is a config/scope error, not a silently-
                # droppable $0. Failing loud stops a mis-scoped pool from
                # under-reporting the idle/SDE legs.
                raise UnsupportedPricingError(
                    f"univ4 position {p.token_id}: coin {coin.hex} is not in "
                    "KNOWN_PAR_STABLES_ETHEREUM — add it to sky_tokens.py or "
                    "correct the venue's pool scope."
                )
            _sym, dec = par
            usd = Decimal(amount_raw) / Decimal(10 ** dec)
            if idle_coin is not None and coin.value == idle_coin:
                idle_total += usd
            elif sde_coin is not None and coin.value == sde_coin:
                sde_total += usd
    return idle_total, sde_total


def _aggregate_univ4_idle_usds(
    prime: Prime,
    period: Period,
    *,
    v4_source,
    block_resolver,
) -> "pd.DataFrame":
    """Daily USDS-leg value held inside Uniswap V4 LP positions, summed across
    all ``lp_kind=uniswap_v4`` venues — the v4 analog of
    ``_aggregate_curve_idle_usds`` (par-stable USDS leg only; no sUSDS spread
    in scope). Returns ``[block_date, daily_net, cum_balance]`` where
    ``cum_balance`` is the day's USDS snapshot (PSM convention)."""
    from datetime import time

    venues = [
        v for v in prime.venues
        if v.lp_kind == "uniswap_v4" and v.curve_idle_usds is not None and not v.skip
    ]
    if not venues:
        return _empty_psm_df()

    daily_by_date: dict = {}
    for venue in venues:
        src = v4_source if v4_source is not None else _univ4_default_source(venue)
        pool_key = _univ4_pool_key_for(venue)
        holder = (venue.holder_override or prime.alm[venue.chain]).value
        chain_str = venue.chain.value
        n_days = (period.end - period.start).days + 1
        _log.info(
            "univ4_idle_usds: %s — resolving %d days × %d token ids (first run: RPC, "
            "subsequent: cache)...", venue.id, n_days, len(venue.univ4_token_ids),
        )
        # Per-venue last-known carry-forward, mirroring
        # ``_aggregate_curve_idle_usds`` / ``_aggregate_lending_idle_usds``.
        # ``None`` = no successful read yet, so an early transport failure
        # re-raises rather than silently seeding a month of $0. Only transient
        # transport errors are caught; config errors (e.g.
        # ``UnsupportedPricingError`` from ``_univ4_position_legs``) propagate.
        from ..extract.rpc import RPCError as _RPCError
        import requests as _requests
        venue_last_idle: Decimal | None = None
        current = period.start
        while current <= period.end:
            eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
            try:
                block = block_resolver.block_at_or_before(chain_str, eod)
                positions = src.positions_in_pool(
                    chain=chain_str, owner=holder,
                    token_ids=list(venue.univ4_token_ids),
                    pool_key=pool_key, block=block,
                )
                idle, _sde = _univ4_position_legs(venue, positions)
            except (_RPCError, _requests.HTTPError, _requests.ConnectionError,
                    _requests.Timeout) as exc:
                if venue_last_idle is None:
                    # Nothing to carry forward — fail loud instead of booking a
                    # cache-of-zeros for the rest of the period.
                    raise
                _log.warning(
                    "univ4_idle_usds: RPC error for venue %s on %s; carrying "
                    "forward prior value $%s (error: %s).",
                    venue.id, current, venue_last_idle, type(exc).__name__,
                )
                idle = venue_last_idle
            else:
                venue_last_idle = idle
            daily_by_date[current] = daily_by_date.get(current, Decimal("0")) + idle
            current = current + timedelta(days=1)
        _log.info("univ4_idle_usds: %s done.", venue.id)

    rows = []
    prev = Decimal("0")
    for d in sorted(daily_by_date):
        snap = daily_by_date[d]
        rows.append({
            "block_date": d, "daily_net": snap - prev, "cum_balance": snap,
            "cum_usdc": Decimal("0"), "cum_usds_leg": snap, "cum_susds": Decimal("0"),
        })
        prev = snap
    return pd.DataFrame(rows)


def _univ4_sde_asset_value_timeseries(
    prime: Prime,
    venue,
    period: Period,
    *,
    v4_source,
    block_resolver,
    cap_usd: Decimal | None = None,
) -> "pd.DataFrame":
    """Daily SDE asset value (USDT/PYUSD leg) for a v4 LP venue — the v4 analog
    of ``_curve_sde_asset_value_timeseries``. Emits ``[block_date, cum_value,
    uncapped_value]`` so it feeds both the utilized exclusion (via
    ``sde_av_total``) and the per-venue SDE revenue split."""
    from datetime import time

    src = v4_source if v4_source is not None else _univ4_default_source(venue)
    pool_key = _univ4_pool_key_for(venue)
    holder = (venue.holder_override or prime.alm[venue.chain]).value
    chain_str = venue.chain.value

    n_days = (period.end - period.start).days + 1
    _log.info(
        "univ4 SDE: %s — resolving %d days × %d token ids (first run: RPC, "
        "subsequent: cache)...", venue.id, n_days, len(venue.univ4_token_ids),
    )
    # Per-venue carry-forward (see ``_aggregate_univ4_idle_usds``): carry the
    # last good raw SDE value on transient RPC failure rather than booking $0,
    # which would understate SDE asset value (→ mis-split SDE revenue /
    # utilized). Only transport errors are caught; config errors propagate.
    from ..extract.rpc import RPCError as _RPCError
    import requests as _requests
    venue_last_raw: Decimal | None = None
    rows = []
    current = period.start
    while current <= period.end:
        eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
        try:
            block = block_resolver.block_at_or_before(chain_str, eod)
            positions = src.positions_in_pool(
                chain=chain_str, owner=holder,
                token_ids=list(venue.univ4_token_ids),
                pool_key=pool_key, block=block,
            )
            _idle, raw_value = _univ4_position_legs(venue, positions)
        except (_RPCError, _requests.HTTPError, _requests.ConnectionError,
                _requests.Timeout) as exc:
            if venue_last_raw is None:
                # Nothing to carry forward — fail loud rather than seeding $0.
                raise
            _log.warning(
                "univ4 SDE: RPC error for venue %s on %s; carrying forward "
                "prior value $%s (error: %s).",
                venue.id, current, venue_last_raw, type(exc).__name__,
            )
            raw_value = venue_last_raw
        else:
            venue_last_raw = raw_value
        capped_value = min(cap_usd, raw_value) if cap_usd is not None else raw_value
        rows.append({
            "block_date": current,
            "cum_value": capped_value,
            "uncapped_value": raw_value,
        })
        current = current + timedelta(days=1)
    _log.info("univ4 SDE: %s done.", venue.id)
    return pd.DataFrame(rows)


def _sde_asset_value_timeseries(
    prime: Prime,
    venue,
    period: Period,
    *,
    balance_source,
    block_resolver,
    nav_at_block,
    cap_usd: Decimal | None = None,
    start_date: "date | None" = None,
    burn_date: "date | None" = None,
    usdc_settlement_date: "date | None" = None,
    end_date: "date | None" = None,
) -> pd.DataFrame:
    """Daily SDE asset value (USD) per venue. Returns a level series with
    columns ``[block_date, cum_value, uncapped_value]``.

    ``cum_value`` is the capped value (≤ ``cap_usd`` for ``kind=capped`` SDE)
    consumed by ``compute_sky_revenue`` for utilized exclusion.

    ``uncapped_value`` is the raw on-chain position value (before cap), kept
    on the frame for diagnostics — not consumed by the EoM-locked compute
    path. Always reflects the actual balance × NAV.

    Daily branching — first matching rule wins:

    * ``cap_usd = None``: ``cum_value = raw_value`` every day (no cap, no
      in-flight handling).
    * ``cap_usd`` set, ``burn_date = None``, ``end_date = None``: standard
      daily capping ``cum_value = min(raw_value, cap_usd)`` — entry treated
      as open-ended.
    * ``cap_usd`` set, ``end_date`` set, ``burn_date = None`` (clean expiry):
      capped through ``end_date``; days strictly after ``end_date`` return
      ``cum_value = 0`` (entry inactive).
    * ``cap_usd`` set, ``burn_date`` and ``end_date`` both set: in-flight
      window (see below). ``burn_date <= end_date`` is required. The in-
      flight upper bound is ``usdc_settlement_date`` if set, else
      ``end_date`` (legacy fallback).
    * ``burn_date`` set without ``end_date``, or without ``cap_usd``, or
      with ``burn_date > end_date`` → ``ValueError``.
    * ``usdc_settlement_date`` set without ``burn_date``, or with
      ``burn_date > usdc_settlement_date`` or
      ``usdc_settlement_date > end_date`` → ``ValueError``.
    * ``start_date`` / ``end_date`` (when set): days strictly before
      ``start_date`` or strictly after ``end_date`` are SDE-inactive and
      return ``cum_value = 0`` regardless of on-chain balance. The orchestrator
      attaches an SDE entry to a venue if it's active for ANY day of the
      period; this per-day gate ensures cum_value is zero on days the entry
      isn't actually live. ``uncapped_value`` keeps tracking on-chain
      balance × NAV throughout for diagnostics.
    * ``start_date > end_date`` (both set) → ``ValueError``.

    **In-flight redemption window (capped SDE with ``burn_date`` set).** When
    a capped tranche is destroyed on-chain on ``burn_date`` but the USDC
    redemption is still settling, the raw on-chain value drops sharply (e.g.
    Grove E8 Mar 9: $325M cap → $128M residual) even though Sky's economic
    exposure persists until the USDC actually lands at the prime's ALM
    (``usdc_settlement_date``, Mar 11 for Grove E8). Letting ``cum_value``
    follow that drop would inflate ``utilized`` for the in-flight days and
    route phantom BR to Sky (~$22K/day on Grove E8 Mar 9-11). For days in
    ``[burn_date, in_flight_end]`` we keep ``cum_value = cap_usd`` so the
    cap-coverage persists through the in-flight window, where
    ``in_flight_end = usdc_settlement_date if set else end_date``. Days
    strictly after ``in_flight_end`` (up to and including ``end_date``)
    return ``cum_value = 0`` — the redemption has settled, so the SDE-
    capped slice no longer ties up prime capital.
    """
    if burn_date is not None and end_date is None:
        raise ValueError(
            f"_sde_asset_value_timeseries({venue.id}): burn_date set "
            f"({burn_date.isoformat()}) but end_date is None — cannot bound "
            "the in-flight window."
        )
    if burn_date is not None and cap_usd is None:
        raise ValueError(
            f"_sde_asset_value_timeseries({venue.id}): burn_date set "
            f"({burn_date.isoformat()}) but cap_usd is None — in-flight "
            "cap-preservation requires a cap to pin."
        )
    if burn_date is not None and end_date is not None and burn_date > end_date:
        raise ValueError(
            f"_sde_asset_value_timeseries({venue.id}): burn_date "
            f"({burn_date.isoformat()}) is after end_date "
            f"({end_date.isoformat()}) — inverted in-flight window."
        )
    if usdc_settlement_date is not None and burn_date is None:
        raise ValueError(
            f"_sde_asset_value_timeseries({venue.id}): usdc_settlement_date "
            f"set ({usdc_settlement_date.isoformat()}) but burn_date is None — "
            "the settlement date only matters when an on-chain burn precedes it."
        )
    if (
        usdc_settlement_date is not None and burn_date is not None
        and burn_date > usdc_settlement_date
    ):
        raise ValueError(
            f"_sde_asset_value_timeseries({venue.id}): burn_date "
            f"({burn_date.isoformat()}) is after usdc_settlement_date "
            f"({usdc_settlement_date.isoformat()}) — inverted in-flight window."
        )
    if (
        usdc_settlement_date is not None and end_date is not None
        and usdc_settlement_date > end_date
    ):
        raise ValueError(
            f"_sde_asset_value_timeseries({venue.id}): usdc_settlement_date "
            f"({usdc_settlement_date.isoformat()}) is after end_date "
            f"({end_date.isoformat()}) — settlement cannot post-date the "
            "Atlas record date."
        )
    if (
        start_date is not None and end_date is not None
        and start_date > end_date
    ):
        # Inverted active window — a YAML misconfiguration where the SDE
        # entry's start_date and end_date are swapped would silently zero
        # out cum_value for every day in the period. Refuse loudly so the
        # operator gets a clear error instead of wrong sky_revenue numbers.
        raise ValueError(
            f"_sde_asset_value_timeseries({venue.id}): start_date "
            f"({start_date.isoformat()}) is after end_date "
            f"({end_date.isoformat()}) — inverted active window."
        )
    # In-flight upper bound: real USDC-settlement date when known, else fall
    # back to end_date (legacy behaviour for entries that don't distinguish
    # the two — pre-Option-C the in-flight window ran through end_date).
    in_flight_end = usdc_settlement_date if usdc_settlement_date is not None else end_date
    holder = venue.holder_override or prime.alm[venue.chain]
    pin_block = period.pin_blocks[venue.chain]
    # SDE asset-value deduction needs the RAW on-chain balance (i.e. what
    # ``balanceOf(holder)`` would return), not the filtered capital-inflow
    # series. The venue's ``min_transfer_amount_usd`` filter exists for the
    # inflow path (``_cat_e_inflow_timeseries``) — there it correctly drops
    # sub-threshold yield-distribution mints that would otherwise corrupt
    # cost-basis tracking. But for the utilized exclusion we need every
    # token the ALM actually holds, including those filtered yield mints.
    # Passing ``min_transfer_amount=0`` here keeps the SDE deduction aligned
    # with BA Labs's ``balanceOf``-based asset value (verified Apr 2026:
    # +$11M BUIDL alignment closing a ~$45K/mo CoF over-charge).
    bal_df = balance_source.cumulative_balance_timeseries(
        chain=venue.chain.value,
        token=venue.token.address.value,
        holder=holder.value,
        start=prime.start_date,
        pin_block=pin_block,
        min_transfer_amount=Decimal(0),
    )

    rows = []
    current = period.start
    while current <= period.end:
        bal = cum_at_or_before(bal_df, "cum_balance", current)
        if bal == 0:
            raw_value = Decimal("0")
        else:
            eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
            block = block_resolver.block_at_or_before(venue.chain.value, eod)
            raw_value = bal * nav_at_block(block)
        if (start_date is not None and current < start_date) or (
            end_date is not None and current > end_date
        ):
            # SDE not active on this day — entry is either pre-start or
            # post-end. cum_value is 0 (no utilized exclusion); uncapped_value
            # still tracks the on-chain residual for diagnostics.
            capped_value = Decimal("0")
        elif (
            cap_usd is not None
            and burn_date is not None
            and burn_date <= current <= in_flight_end
        ):
            # In-flight redemption window — see docstring. Cap-coverage
            # persists until ``in_flight_end`` (= usdc_settlement_date when
            # set, else end_date), regardless of the on-chain drop.
            capped_value = cap_usd
        elif (
            burn_date is not None
            and in_flight_end is not None
            and current > in_flight_end
        ):
            # Post-settlement, pre-end-date: USDC has landed at the ALM, so
            # the SDE-capped slice no longer ties up prime capital. Note this
            # branch only fires when usdc_settlement_date < end_date — when
            # the two coincide (legacy entries) the post-end gate above fires
            # first and this branch is unreachable.
            capped_value = Decimal("0")
        elif cap_usd is not None and raw_value > cap_usd:
            capped_value = cap_usd
        else:
            capped_value = raw_value
        rows.append({
            "block_date": current,
            "cum_value": capped_value,
            "uncapped_value": raw_value,
        })
        current = current + timedelta(days=1)
    return pd.DataFrame(rows)


def _susds_shares_to_principal(
    sub_susds_shares,
    *,
    sources: "Sources",
    block_resolver: IBlockResolver,
    chain: Chain,
):
    """Convert a sUSDS-shares timeseries to USDS-denominated cost-basis
    principal (``Σ shares × entry_pps``).

    Each day's signed share-flow is priced at that day's EoD pps (one
    ``convertToAssets`` RPC per active day). This is the deposit-time value,
    NOT the current value (``shares × current_pps``, which double-counts SSR).
    """
    if sub_susds_shares is None or sub_susds_shares.empty:
        return sub_susds_shares
    if not (sub_susds_shares["daily_net"] != 0).any():
        return sub_susds_shares  # no activity → all-zero is the same in shares/USDS

    # The vault address is hardcoded to Ethereum's sUSDS. Multi-chain sUSDS
    # would need a per-chain vault map; fail loudly rather than silently
    # reading the wrong contract.
    if chain != Chain.ETHEREUM:
        raise NotImplementedError(
            f"sUSDS principal conversion only registered for Ethereum; got {chain}"
        )

    c2a = (
        sources.convert_to_assets
        if sources.convert_to_assets is not None
        else get_convert_to_assets_source()
    )

    def _pps_for_day(d) -> Decimal:
        eod = datetime.combine(d, time.max, tzinfo=timezone.utc)
        block = block_resolver.block_at_or_before(chain.value, eod)
        raw = c2a.convert_to_assets(
            chain=chain.value,
            vault=sUSDS_ETHEREUM.address.value,
            shares=10**18,
            block=block,
        )
        return Decimal(raw) / Decimal(10**18)

    # Build daily principal flow + cumulative in pure-Python Decimals —
    # pandas' cumsum on object-dtype Decimal silently falls back to Python
    # reduction; explicit running-sum keeps the dtype contract intact.
    out = sub_susds_shares.copy()
    daily_usds: list[Decimal] = []
    cum_usds: list[Decimal] = []
    running = Decimal("0")
    for _, row in out.iterrows():
        shares_flow = row["daily_net"]
        if shares_flow == 0:
            d_usds = Decimal("0")
        else:
            d_usds = _to_decimal(shares_flow) * _pps_for_day(row["block_date"])
        running += d_usds
        daily_usds.append(d_usds)
        cum_usds.append(running)
    out["daily_net"] = daily_usds
    out["cum_balance"] = cum_usds
    return out


def _empty_psm_df() -> pd.DataFrame:
    """Empty 6-column PSM timeseries frame.

    ``cum_balance`` = USDS-equivalent total = ``cum_usdc + cum_usds_leg +
    cum_susds`` (kept on the frame for at-a-glance reads / any external
    consumer that doesn't need the per-leg breakdown).
    """
    return pd.DataFrame({
        "block_date": [], "daily_net": [], "cum_balance": [],
        "cum_usdc": [], "cum_usds_leg": [], "cum_susds": [],
    })


def _to_decimal(v) -> Decimal:
    """Coerce a numpy/pandas scalar to ``Decimal`` without round-tripping
    Decimals through ``str``."""
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _df_from_daily_dict(daily_by_date: dict) -> pd.DataFrame:
    """``[block_date, daily_net, cum_balance]`` DataFrame from a ``{date: Decimal}``
    map. Returns the empty-shape frame if the map is empty."""
    if not daily_by_date:
        return _empty_psm_df()
    rows = sorted(daily_by_date.items(), key=lambda kv: kv[0])
    df = pd.DataFrame({
        "block_date": [r[0] for r in rows],
        "daily_net":  [r[1] for r in rows],
    })
    df["cum_balance"] = df["daily_net"].cumsum()
    return df


def _susds_cat_b_spread_reimb(
    value_som: Decimal, inflow_ts: pd.DataFrame | None, period: Period,
) -> Decimal:
    """BR−SSR spread daily-compounded Sky Revenue reduction for a Cat B
    ``sky_savings_token`` venue (S32 / S37 / S43 / S47 / S51).

    Sky charges full BR on the underlying USDS (sUSDS is NOT subtracted from
    utilized), then refunds the BR−SSR spread as a sky_revenue reduction so
    the prime's net cost is SSR × V (economic neutrality, Rule 5). The
    spread is date-resolved (30bps; 20bps from 2026-07-23).

    Formula: ``Σ_d V_d × daily_compounding_factor(base_rate_spread_at(d))``
    where ``V_d = value_som + (cum_inflow_d − cum_inflow_{som-1})`` is the
    daily sUSDS position value across ``[period.start, period.end]``.

    Mirrors the daily-integration pattern used by ``_psm3_susds_spread`` and
    ``_aggregate_curve_idle_usds``. The previous flat-SoM approximation
    (``value_som × n_days × spread_daily``) misstated the reimbursement by
    ~``(V_avg − V_som) × n_days × spread_daily`` for venues with material
    mid-period inflows/outflows; daily integration is exact (Rule 5
    precision parity across all three sUSDS paths).

    Degenerate case: returns ``Decimal(0)`` when both ``value_som`` is zero
    AND ``inflow_ts`` is empty (no position throughout the period). Negative
    daily positions (transient if e.g. an external arrival is excluded from
    the timeseries while the subsequent on-chain outflow is included) are
    clamped to zero per day so the spread reimbursement stays non-negative.
    """
    from ._helpers import daily_compounding_factor
    from .sky_revenue import base_rate_spread_at
    if inflow_ts is None or inflow_ts.empty:
        # Stable position throughout — V_d = value_som for every day. Still
        # integrate daily: the spread itself can step mid-period.
        if value_som <= 0:
            return Decimal("0")
        total = Decimal("0")
        current = period.start
        while current <= period.end:
            total += value_som * daily_compounding_factor(base_rate_spread_at(current))
            current = current + timedelta(days=1)
        return total
    cum_baseline = cum_at_or_before(
        inflow_ts, "cum_inflow", period.start - timedelta(days=1),
    )
    total = Decimal("0")
    current = period.start
    while current <= period.end:
        cum_d = cum_at_or_before(inflow_ts, "cum_inflow", current)
        v_d = value_som + (cum_d - cum_baseline)
        if v_d > 0:
            total += v_d * daily_compounding_factor(base_rate_spread_at(current))
        current = current + timedelta(days=1)
    return total


def _savings_v2_depositor_ssr(
    sv2_src, resolver, ssr_history: pd.DataFrame, period: Period,
) -> Decimal:
    """Daily-integrated SSR accrual on the depositor-sourced spUSDC slice.

    Returns ``Σ_d spUSDC_AO_d × ssr_daily_factor_d`` over
    ``[period.start, period.end]``, where ``spUSDC_AO_d`` is
    ``spUSDC_V2.assetsOutstanding`` (the deployed-to-ALM portion —
    excludes the vault's ~$10M USDC withdrawal buffer, which never
    enters S32's sUSDS reading) read at day ``d``'s EoD block and
    ``ssr_daily_factor_d = (1 + SSR_d)^(1/365) − 1``. The caller negates
    the total and books it via
    ``VenueRevenueInputs.actual_revenue_adjustment`` for S32, removing
    the depositors' SSR from the raw sUSDS MtM.

    **Day-boundary convention — right-Riemann on EoD-d positions.** Day
    ``d``'s accrual is computed on the position INCLUDING day-d flows,
    matching ``_susds_cat_b_spread_reimb`` (``V_d = value_som +
    cum_inflows_through_d``) and ``compute_sky_revenue_daily``
    (``art_at_or_before(sparse, d)``). Consistency across the three
    integrals matters more than the half-day absolute bias they share.

    **RPC-failure degradation — carry-forward.** ``sv2_src.at_block``
    returns 0 on revert/outage. A genuine pre-deployment 0 follows other
    0s (carry-forward of 0 = 0, correct); a transient outage mid-series
    would silently shrink the carve-out and over-credit the prime, so a
    0 read AFTER a non-zero day carries the previous day's value forward
    with a warning (the series is slow-moving; conservative for the
    prime).

    ``resolver`` is the orchestrator's block resolver — Dune-backed and
    cached when ``DUNE_API_KEY`` is set (the upgrade happens before the
    venue loop), so the ~31 daily lookups are cheap on re-runs.
    """
    from ._helpers import daily_compounding_factor, ssr_at_or_before

    total = Decimal("0")
    prev_ta = Decimal("0")
    current = period.start
    while current <= period.end:
        eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
        block = resolver.block_at_or_before(Chain.ETHEREUM.value, eod)
        ta = sv2_src.at_block(block)
        if ta == 0 and prev_ta > 0:
            _log.warning(
                "_savings_v2_depositor_ssr: spUSDC assetsOutstanding read 0 at "
                "block %d (day %s) after $%.0f the previous day — treating "
                "as a transient read failure and carrying the previous "
                "day's value forward.",
                block, current.isoformat(), float(prev_ta),
            )
            ta = prev_ta
        if ta > 0:
            total += ta * daily_compounding_factor(
                ssr_at_or_before(ssr_history, current),
            )
        prev_ta = ta
        current = current + timedelta(days=1)
    return total


def _psm3_susds_spread(psm_usds: pd.DataFrame | None, period: Period) -> Decimal:
    """BR−SSR spread daily-compounded Prime Revenue credit on the sUSDS
    slice of PSM3 holdings.

    The sUSDS leg of PSM3 is yield-bearing — the prime captures SSR
    automatically via ``convertToAssetValue`` growth of its PSM3 share. To
    keep the prime economically neutral on idle sUSDS (it shouldn't earn
    money for just parking capital), ``compute_sky_revenue`` charges full
    BR on this slice (no ``utilized`` reduction for the sUSDS leg), and
    this function credits back the BR−SSR spread so the
    SSR-+-BR-charge-+-spread-credit composite nets to zero. See PRD §17.11.
    The spread is date-resolved (30bps; 20bps from 2026-07-23).

    Formula: ``Σ_d cum_susds_d × daily_compounding_factor(base_rate_spread_at(d))``
    where d ranges over days in ``[period.start, period.end]``.
    """
    if psm_usds is None or psm_usds.empty or "cum_susds" not in psm_usds.columns:
        return Decimal("0")
    # Lazy import to avoid module-cycle (sky_revenue imports from _helpers).
    from ._helpers import daily_compounding_factor
    from .sky_revenue import base_rate_spread_at
    total = Decimal("0")
    current = period.start
    while current <= period.end:
        cum_susds = cum_at_or_before(psm_usds, "cum_susds", current)
        if cum_susds > 0:
            total += cum_susds * daily_compounding_factor(base_rate_spread_at(current))
        current = current + timedelta(days=1)
    return total


def _psm3_susds_appreciation(
    psm_usds: pd.DataFrame | None, period: Period, ssr_history: pd.DataFrame,
) -> Decimal:
    """Daily-integrated SSR appreciation on the PSM3 sUSDS slice — Case 3a
    of the sUSDS attribution fix (PRD ``docs/PRD_revenue_gross_net_audit.md``
    §10).

    The prime physically receives the SSR growth of the sUSDS reserve
    inside PSM3 (via ``convertToAssetValue`` growth of its PSM3 share),
    pays full BR on the underlying debt (no ``utilized`` reduction for
    the sUSDS leg) and gets the 30 bps spread back via
    ``_psm3_susds_spread``: ``+SSR − BR + 30bps = 0``. Before this fix
    only the two Sky-side legs were booked, so the headline showed
    ``−SSR × V`` on this slice when the true PnL is ``0``. This function
    returns the missing prime-side leg::

        Σ_d  cum_susds_d × ((1 + SSR_d)^(1/365) − 1)

    booked directly into ``prime_agent_revenue`` (PSM3 is not a venue —
    no per-venue row, no SDE interaction). Same daily integration and
    right-Riemann EoD-d convention as ``_psm3_susds_spread`` — only the
    rate differs (per-day SSR instead of the 30 bps constant).
    """
    if psm_usds is None or psm_usds.empty or "cum_susds" not in psm_usds.columns:
        return Decimal("0")
    from ._helpers import daily_compounding_factor, ssr_at_or_before
    total = Decimal("0")
    current = period.start
    while current <= period.end:
        cum_susds = cum_at_or_before(psm_usds, "cum_susds", current)
        if cum_susds > 0:
            total += cum_susds * daily_compounding_factor(
                ssr_at_or_before(ssr_history, current),
            )
        current = current + timedelta(days=1)
    return total


def _aggregate_alm_usds(
    prime: Prime,
    period: Period,
    *,
    balance_source=None,
) -> pd.DataFrame:
    """Sum idle USDS balances held directly at each chain's ALM proxy.

    Loops over every chain in ``prime.alm``, fetches the USDS ERC-20 balance
    timeseries at the ALM address, and aggregates the daily nets into a single
    cross-chain series (same ``[block_date, daily_net, cum_balance]`` shape as
    ``_aggregate_psm_usds``).

    Chains where USDS is not yet registered in ``USDS_BY_CHAIN`` are skipped
    with a warning — treat this as a signal to add the address to sky_tokens.py.
    """
    from ..domain.sky_tokens import USDS_BY_CHAIN
    from ..normalize.balances import get_alm_balance_timeseries

    daily_by_date: dict = {}
    for chain in prime.alm:
        if chain not in period.pin_blocks:
            continue
        usds_token = USDS_BY_CHAIN.get(chain)
        if usds_token is None:
            _log.warning(
                "_aggregate_alm_usds: no USDS address known for chain %s "
                "(add it to USDS_BY_CHAIN in sky_tokens.py); skipping.",
                chain.value,
            )
            continue
        per_chain = get_alm_balance_timeseries(
            prime, chain, usds_token, period, source=balance_source,
        )
        for _, row in per_chain.iterrows():
            d = row["block_date"]
            daily_by_date[d] = daily_by_date.get(d, Decimal(0)) + _to_decimal(row["daily_net"])

    return _df_from_daily_dict(daily_by_date)


def _aggregate_psm_usds(
    prime: Prime,
    period: Period,
    *,
    balance_source,
    psm3_source=None,
    block_resolver=None,
    position_balance_source=None,
    convert_to_assets_source=None,
):
    """Sum PSM USDS-equivalent timeseries across every chain in
    ``prime.psm``, per leg. Per-chain timeseries are produced by
    ``get_psm_usds_timeseries``; this aggregates them into a single 6-column
    daily series (``[block_date, daily_net, cum_balance, cum_usdc,
    cum_usds_leg, cum_susds]``) consumable by ``compute_sky_revenue``.

    Each per-leg cumulative is forward-filled per chain across the full date
    set, so a chain that has no row on a given date contributes its last
    known value — preserving the "value-as-of-date" reading semantics that
    ``cum_at_or_before`` relies on.

    Returns an empty DataFrame if the prime has no PSM configured anywhere.

    Aggregation uses ``cum_balance`` (absolute snapshot/position) per chain,
    not ``daily_net``. For ``erc4626_shares``, cum_balance is the actual
    USDS-equivalent at each day's EoD block; daily_net is just the in-period
    delta. Summing daily_net and rebuilding a cumsum would omit the pre-period
    baseline balance (which can be $100M+) and produce a near-zero result.
    """
    if not prime.psm:
        return _empty_psm_df()

    # Coverage assertion (PRD §17.10): if a PSM is configured for a chain that
    # the orchestrator didn't resolve a pin_block for, the prior behavior was
    # to silently `continue` — which would silently inflate ``utilized`` (the
    # PSM holdings on that chain would be ignored, so the prime would be
    # charged BR on capital that's actually parked). Fail fast instead — a
    # missing pin_block here means the orchestrator's chain set is out of sync
    # with the YAML, and the only correct action is to fix the config.
    missing = [c.value for c in prime.psm if c not in period.pin_blocks]
    if missing:
        raise ValueError(
            f"PSM configured for chain(s) {missing} but no pin_block resolved "
            f"for them in this Period. Add the chain to the orchestrator's "
            f"chain set or remove the `psm:` block from the prime YAML."
        )

    per_chain_frames = []
    for chain in prime.psm:
        per_chain = get_psm_usds_timeseries(
            prime, chain, period,
            balance_source=balance_source,
            psm3_source=psm3_source,
            block_resolver=block_resolver,
            position_balance_source=position_balance_source,
            convert_to_assets_source=convert_to_assets_source,
        )
        if not per_chain.empty:
            per_chain_frames.append(per_chain[
                ["block_date", "cum_usdc", "cum_usds_leg", "cum_susds"]
            ])

    if not per_chain_frames:
        return _empty_psm_df()

    # Build the union of dates, forward-fill each chain's per-leg cum_X across
    # the full set, then sum across chains.
    all_dates = sorted({d for f in per_chain_frames for d in f["block_date"]})
    summed: dict = {
        d: {"cum_usdc": Decimal(0), "cum_usds_leg": Decimal(0), "cum_susds": Decimal(0)}
        for d in all_dates
    }
    for chain_df in per_chain_frames:
        chain_rows = {
            row["block_date"]: {
                "cum_usdc":     _to_decimal(row["cum_usdc"]),
                "cum_usds_leg": _to_decimal(row["cum_usds_leg"]),
                "cum_susds":    _to_decimal(row["cum_susds"]),
            }
            for _, row in chain_df.iterrows()
        }
        last = {"cum_usdc": Decimal(0), "cum_usds_leg": Decimal(0), "cum_susds": Decimal(0)}
        for d in all_dates:
            if d in chain_rows:
                last = chain_rows[d]
            for k in last:
                summed[d][k] += last[k]

    rows = sorted(summed.items())
    df = pd.DataFrame({
        "block_date":   [d for d, _ in rows],
        "cum_usdc":     [v["cum_usdc"]     for _, v in rows],
        "cum_usds_leg": [v["cum_usds_leg"] for _, v in rows],
        "cum_susds":    [v["cum_susds"]    for _, v in rows],
    })
    df["cum_balance"] = df["cum_usdc"] + df["cum_usds_leg"] + df["cum_susds"]
    if len(df) > 0:
        df["daily_net"] = df["cum_balance"].diff().fillna(df["cum_balance"].iloc[0])
    else:
        df["daily_net"] = Decimal(0)
    return df[["block_date", "daily_net", "cum_balance", "cum_usdc", "cum_usds_leg", "cum_susds"]]


def get_psm_usds_timeseries(
    prime: Prime, chain: Chain, period: Period,
    *,
    balance_source,
    psm3_source=None,
    block_resolver=None,
    position_balance_source=None,
    convert_to_assets_source=None,
):
    """USDS-equivalent the prime has parked at the PSM on ``chain``, per day.

    Returns a 6-column DataFrame ``[block_date, daily_net, cum_balance,
    cum_usdc, cum_usds_leg, cum_susds]``. Returns empty DataFrame if the
    prime has no PSM configured on this chain.

    Single supported mechanic today (``PsmKind.ERC4626_SHARES``, Spark PSM3):
    the ALM holds PSM3 shares which are *internal accounting* (no ERC-20
    Transfer events) and the rate uses a non-standard
    ``convertToAssetValue(uint256)``. We snapshot
    ``convertToAssetValue(shares(alm, b), b)`` at each day's EoD block,
    decompose into per-leg values (USDC / USDS / sUSDS) by reading reserve
    balances at the PSM3 contract and applying mainnet sUSDS pps for the
    sUSDS leg, then route each leg per PRD §17.11.
    """

    cfg = prime.psm.get(chain)
    if cfg is None or chain not in prime.alm:
        return _empty_psm_df()

    if cfg.kind == PsmKind.ERC4626_SHARES:
        # Spark PSM3. The pool holds three reserves — USDC, USDS, sUSDS — that
        # are treated differently by the settlement methodology (PRD §17.11):
        #
        #   USDS leg  → BR-reimbursed (subtracted from utilized) — the prime
        #               borrowed USDS, parked it at PSM3 ⇒ this slice is idle
        #   USDC leg  → Sky Direct Exposure (Atlas §A.2.3.2.2.3) — Sky takes
        #               the actual yield (≈ $0 for passive USDC reserves);
        #               utilized NOT reduced for this slice
        #   sUSDS leg → utilized NOT reduced; prime earns 30 bps spread
        #               (= BR − SSR) on its USDS-equivalent value — the sUSDS
        #               share price already returns SSR to the prime, so
        #               crediting full BR-reimbursement on top would double-
        #               count (same rule as sUSDS in regular allocations,
        #               PRD §17.7 + RULES §5).
        #
        # To apply these rules we decompose Spark's claim per day into the 3
        # legs by reading ``balanceOf(token, psm3)`` for each leg and using
        # mainnet sUSDS pps (the L2 sUSDS is a 1:1 bridge — verified to 4
        # decimals against Ethereum sUSDS ``convertToAssets(1e18)``):
        #
        #   pool_total_usds_eq = USDC_face + USDS_face + sUSDS_face × sUSDS_pps
        #   spark_share        = convertToAssetValue(spark_shares) / pool_total
        #   spark_per_leg      = spark_share × leg_value
        from ..normalize.registry import (
            get_psm3_source as _get_psm3,
            get_position_balance_source as _get_pos_bal,
            get_convert_to_assets_source as _get_c2a,
        )
        from ..domain.sky_tokens import PSM3_LEG_TOKENS, sUSDS_ETHEREUM
        if block_resolver is None:
            raise ValueError(
                "get_psm_usds_timeseries(kind=erc4626_shares) requires a "
                "block_resolver to read PSM3 shares at each day's EoD block"
            )
        if chain not in PSM3_LEG_TOKENS:
            raise ValueError(
                f"PSM3 leg-token registry has no entry for {chain.value}. "
                f"Add USDC/USDS/sUSDS addresses for the chain to "
                f"settle.domain.sky_tokens.PSM3_LEG_TOKENS."
            )
        # Prefer the Dune-backed PSM3 source when DUNE_API_KEY is set —
        # ``RPCPsm3Source`` issues two RPC calls per (chain, day) for the
        # non-standard ``shares()`` + ``convertToAssetValue()`` on the PSM3
        # contract; Alchemy / drpc Arbitrum + Unichain return intermittent
        # 500s on these, triggering 10-retry chains that dominate Spark
        # cell wall time. Dune backs both calls from decoded
        # ``spark_protocol_multichain.psm3_evt_{deposit,withdraw}`` events.
        if psm3_source is not None:
            psm3 = psm3_source
        else:
            import os as _os
            if _os.environ.get("DUNE_API_KEY"):
                from ..normalize.sources.dune_psm3 import DunePsm3Source
                psm3 = DunePsm3Source(
                    position_balance_source=position_balance_source,
                    convert_to_assets_source=convert_to_assets_source,
                    block_resolver=block_resolver,
                )
                # Bulk-load the entire settlement period in one query per
                # (chain, holder) — otherwise each day's ``shares_of`` would
                # re-fetch with its own pin_block (~31× redundant Dune calls
                # per chain per cell). See ``DunePsm3Source.preload`` docstring.
                # Preload everything from Dune: ALM shares, pool totals,
                # and per-token reserves (USDC/USDS/sUSDS). Per-day calls in
                # ``_legs_at`` then become constant-time dict bisects, with
                # zero RPC reads for the PSM3 stack.
                #
                # Wrapped in try/except so that a Dune credit exhaustion
                # (HTTP 402 on community-tier monthly cap) gracefully
                # degrades to the per-day RPC carry-forward path — the run
                # continues with slightly stale leg values rather than
                # crashing. Add credits and re-run for clean numbers.
                from ..extract.dune import DuneError as _DuneError
                import requests as _requests
                try:
                    psm3.preload(
                        chain=chain.value,
                        holder=prime.alm[chain].value,
                        pin_block=period.pin_blocks[chain],
                        psm3=cfg.address.value,
                    )
                except (
                    _DuneError,
                    _requests.HTTPError,
                    _requests.ConnectionError,
                    _requests.Timeout,
                ) as _e:
                    _log.warning(
                        "DunePsm3Source.preload failed on %s (%s) — falling "
                        "back to per-day RPC for PSM3 reads (carry-forward on "
                        "failure). Common causes: Dune credits exhausted "
                        "(402), throttling (429), or transient network / DNS.",
                        chain.value, _e,
                    )
            else:
                psm3 = _get_psm3()
        pos_bal = position_balance_source if position_balance_source is not None else _get_pos_bal()
        c2a = convert_to_assets_source if convert_to_assets_source is not None else _get_c2a()
        scale = Decimal(10**18)
        usdc_scale = Decimal(10**6)
        holder = prime.alm[chain].value
        leg_tokens = PSM3_LEG_TOKENS[chain]
        usdc_addr = leg_tokens["USDC"].address.value
        usds_addr = leg_tokens["USDS"].address.value
        susds_addr = leg_tokens["sUSDS"].address.value
        psm3_addr = cfg.address.value

        # Per-day RPC failures (drpc upstream flake, contract-not-yet-deployed
        # at very early blocks) shouldn't kill the whole chain's PSM3
        # timeseries — that would silently inflate sky_revenue by the missing
        # PSM holdings. Treat a failed day as "carry forward yesterday's
        # value" (no movement) and log the gap so it's auditable.
        # ``DuneError`` is also caught: when the orchestrator-level
        # ``preload()`` fully succeeded but a later Dune call inside
        # ``convert_to_asset_value`` fails (e.g., a mid-run 402 / 429),
        # the carry-forward path keeps the day going instead of crashing
        # the whole chain's PSM3 timeseries.
        from ..extract.dune import DuneError
        from ..extract.rpc import RPCError
        import requests as _requests

        _ZERO_LEGS = (Decimal(0), Decimal(0), Decimal(0))

        def _legs_at(
            day,
            fallback: tuple[Decimal, Decimal, Decimal] | None = None,
        ) -> tuple[Decimal, Decimal, Decimal]:
            """Spark's USDS-equivalent claim split into (usdc, usds, susds)
            at this day's EoD block."""
            eod = datetime.combine(day, time.max, tzinfo=timezone.utc)
            try:
                block = block_resolver.block_at_or_before(chain.value, eod)
                shares = psm3.shares_of(
                    chain=chain.value, psm3=psm3_addr,
                    holder=holder, block=block,
                )
                if shares <= 0:
                    return _ZERO_LEGS
                spark_claim_raw = psm3.convert_to_asset_value(
                    chain=chain.value, psm3=psm3_addr,
                    num_shares=shares, block=block,
                )
                spark_claim = Decimal(spark_claim_raw) / scale

                # PSM3 leg reserves at this block. USDC is 6-decimal; USDS,
                # sUSDS are 18-decimal. Prefer the Dune-preloaded reserves
                # (constant-time dict lookup) over per-day RPC ``balanceOf``
                # — Alchemy / drpc on Arbitrum + Unichain intermittently
                # fails balanceOf calls and the carry-forward fallback
                # produces stale leg values on failed days.
                def _reserve_at(addr: bytes, dec: int) -> int:
                    pra = getattr(psm3, "pool_reserve_at", None)
                    if pra is not None:
                        r = pra(chain.value, addr, psm3_addr, block, decimals=dec)
                        if r is not None:
                            return r
                    return pos_bal.balance_at(chain.value, addr, psm3_addr, block)
                usdc_raw  = _reserve_at(usdc_addr,  6)
                usds_raw  = _reserve_at(usds_addr,  18)
                susds_raw = _reserve_at(susds_addr, 18)
                usdc_val   = Decimal(usdc_raw)  / usdc_scale
                usds_val   = Decimal(usds_raw)  / scale
                susds_face = Decimal(susds_raw) / scale

                # sUSDS pps from Ethereum (L2 sUSDS is a 1:1 bridge of mainnet
                # sUSDS — only mainnet exposes ``convertToAssets``).
                eth_block = block_resolver.block_at_or_before(Chain.ETHEREUM.value, eod)
                susds_pps_raw = c2a.convert_to_assets(
                    chain=Chain.ETHEREUM.value, vault=sUSDS_ETHEREUM.address.value,
                    shares=10**18, block=eth_block,
                )
                susds_pps = Decimal(susds_pps_raw) / scale
                susds_val = susds_face * susds_pps

                pool_total = usdc_val + usds_val + susds_val
                if pool_total <= 0:
                    # Pool has zero reserves but Spark has a non-zero claim
                    # → impossible. Fall back to assigning the entire claim
                    # to the USDS leg (safest classification: pretend it's
                    # plain idle USDS).
                    _log.warning(
                        "PSM3 pool_total=0 but spark_claim=$%s on %s @ %s; "
                        "classifying full claim as USDS leg.",
                        f"{spark_claim:,.2f}", chain.value, day,
                    )
                    return (Decimal(0), spark_claim, Decimal(0))

                spark_share = spark_claim / pool_total
                return (
                    spark_share * usdc_val,
                    spark_share * usds_val,
                    spark_share * susds_val,
                )
            except (RPCError, DuneError, _requests.HTTPError, _requests.ConnectionError, _requests.Timeout) as e:
                if fallback is None:
                    raise
                _log.warning(
                    "PSM3 read failed on %s for %s @ %s; carrying forward "
                    "legs=(usdc=$%s, usds=$%s, susds=$%s) (error: %s). PSM "
                    "USDS-equiv may be slightly stale for this day.",
                    chain.value, psm3_addr.hex(), day,
                    f"{fallback[0]:,.2f}", f"{fallback[1]:,.2f}", f"{fallback[2]:,.2f}",
                    type(e).__name__,
                )
                return fallback

        # One snapshot per day across [period.start, period.end]. The init
        # read (period.start - 1) cannot fall back — a missing baseline
        # means we can't compute period flows correctly, so let it raise.
        days = [period.start + timedelta(days=i) for i in range((period.end - period.start).days + 1)]
        cur_legs = _legs_at(period.start - timedelta(days=1))
        block_dates: list = []
        daily_net: list[Decimal] = []
        cum_usdc: list[Decimal] = []
        cum_usds_leg: list[Decimal] = []
        cum_susds: list[Decimal] = []
        for day in days:
            legs = _legs_at(day, fallback=cur_legs)
            block_dates.append(day)
            cum_balance_today = sum(legs)
            cum_balance_yday = sum(cur_legs)
            daily_net.append(cum_balance_today - cum_balance_yday)
            cum_usdc.append(legs[0])
            cum_usds_leg.append(legs[1])
            cum_susds.append(legs[2])
            cur_legs = legs

        if all(u == 0 and s == 0 and z == 0 for u, s, z in zip(cum_usdc, cum_usds_leg, cum_susds)):
            return _empty_psm_df()
        df = pd.DataFrame({
            "block_date":   block_dates,
            "daily_net":    daily_net,
            "cum_usdc":     cum_usdc,
            "cum_usds_leg": cum_usds_leg,
            "cum_susds":    cum_susds,
        })
        # ``cum_balance`` = total USDS-equivalent (sum of the 3 legs). Kept for
        # backward compatibility with any consumer that doesn't yet know about
        # the legs; new consumers should read the per-leg columns directly.
        df["cum_balance"] = df["cum_usdc"] + df["cum_usds_leg"] + df["cum_susds"]
        return df

    raise ValueError(f"Unknown PSM kind: {cfg.kind!r}")


def _aggregate_curve_idle_usds(
    prime: Prime,
    period: Period,
    *,
    curve_pool_source,
    block_resolver,
    convert_to_assets_source=None,
    ssr_history: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, Decimal, dict[str, Decimal]]:
    """Daily data for Curve pool coins configured via ``curve_idle_usds``.

    Returns a **tuple** ``(utilized_deduction_df, susds_spread,
    susds_ssr_by_venue)``:

    * ``utilized_deduction_df`` — ``[block_date, daily_net, cum_balance]`` frame
      of the daily USDS deduction from ``utilized`` (par-stable coin venues only).
    * ``susds_spread`` — total Decimal 30 bps spread on sUSDS inside LP pools
      (``sky_savings_token=True`` venues only); folded into the sky-revenue
      reduction by the orchestrator (d255ed2).
    * ``susds_ssr_by_venue`` — per-venue-id Decimal of the FULL-SSR daily
      integral on the same prime-proportional sUSDS values (Case 3b of the
      sUSDS attribution fix, PRD §10). The LP unit price's recursive
      ``convertToAssets`` embeds this appreciation in the venue's MtM
      ``actual_revenue``; for SDE venues (S24, sd_share ≈ 100%) the redirect
      would hand it to Sky a SECOND time on top of Sky's BR − 30bps = SSR
      collection. The orchestrator removes it from the SDE-eligible pot
      (``actual_revenue_adjustment``) and re-books it 100% to the prime
      (``external_revenue``). Empty when ``ssr_history`` is None.

    **Par-stable coin venues** (``sky_savings_token=False``):
    Prime's proportional share of the coin reserve is deducted from ``utilized``::

        prime_usds_d = (alm_lp_d / pool_total_d) × coin_reserve_d

    **sUSDS / sky-savings-token venues** (``sky_savings_token=True``):
    No ``utilized`` deduction. Instead the prime earns the BR−SSR spread
    (date-resolved: 30bps; 20bps from 2026-07-23)::

        spread_d = (alm_lp_d / pool_total_d) × (sUSDS_reserve_d × pps_d) × spread_daily_d

    where ``pps_d = convertToAssets(1 share, block_d) / 10**underlying_decimals``,
    plus the full-SSR integral on the same daily values for ``susds_ssr_by_venue``.
    """
    from datetime import time
    from ..domain.sky_tokens import KNOWN_PAR_STABLES_ETHEREUM, KNOWN_YIELD_BEARING_ETHEREUM
    from ..extract.rpc import balance_of as _balance_of
    from ..normalize.sources.curve_pool import CurvePoolSource
    from .sky_revenue import base_rate_spread_at
    from ._helpers import daily_compounding_factor, ssr_at_or_before

    # Exclude uniswap_v4 venues — they reuse the curve_idle_usds schema but
    # their daily idle/SDE values are computed by _aggregate_univ4_idle_usds
    # and _univ4_sde_asset_value_timeseries, which read V4 position data rather
    # than Curve pool reserves. Including them here would attempt to call
    # pool_src.read_pool() on the v4 PositionManager address (not a Curve pool).
    venues_with_config = [
        v for v in prime.venues
        if v.curve_idle_usds is not None and v.lp_kind != "uniswap_v4"
    ]
    if not venues_with_config:
        return _empty_psm_df(), Decimal("0"), {}

    pool_src = curve_pool_source if curve_pool_source is not None else CurvePoolSource()

    # c2a is only needed for sky_savings_token venues.
    has_sky_savings = any(v.curve_idle_usds.sky_savings_token for v in venues_with_config)
    if has_sky_savings and ssr_history is None:
        _log.warning(
            "_aggregate_curve_idle_usds: sky_savings_token Curve venues "
            "present but ssr_history is None — susds_ssr_by_venue will be "
            "empty and the Case 3b sUSDS-leg SSR re-attribution (PRD §10) "
            "will silently not fire. Pass ssr_history for canonical numbers.",
        )
    c2a = None
    if has_sky_savings:
        from ..normalize.registry import get_convert_to_assets_source as _get_c2a
        c2a = convert_to_assets_source if convert_to_assets_source is not None else _get_c2a()

    # Spread = BR − SSR, date-resolved per day (30bps; 20bps from 2026-07-23)
    # — resolved inside the day loop, NOT hoisted, so a mid-period step is
    # applied only to the days it covers.

    def _par_stable_usds(coin_addr: bytes, raw_balance: int) -> Decimal:
        par = KNOWN_PAR_STABLES_ETHEREUM.get(coin_addr)
        if par is None:
            raise ValueError(
                f"_aggregate_curve_idle_usds: coin {coin_addr.hex()} is not in "
                "KNOWN_PAR_STABLES_ETHEREUM. Use sky_savings_token=True for "
                "yield-bearing coins."
            )
        _sym, decimals = par
        return Decimal(raw_balance) / Decimal(10 ** decimals)

    # Separate accumulators for par-stable (utilized deduction) and
    # sky-savings-token (spread revenue + full-SSR appreciation).
    daily_util: dict = {}
    daily_spread: dict = {}
    susds_ssr_by_venue: dict[str, Decimal] = {}

    for venue in venues_with_config:
        cfg = venue.curve_idle_usds
        target_coin = cfg.coin.value
        pool_addr = venue.token.address.value
        holder = (venue.holder_override or prime.alm[venue.chain]).value
        chain_str = venue.chain.value

        # Per-VENUE last-known carry-forward state. Reading from the shared
        # ``daily_util`` / ``daily_spread`` dicts would (a) carry the
        # cross-venue aggregate, not this venue's prior value, and (b)
        # compound on consecutive failures into a zero or doubled value.
        # Keep per-venue state local to the venue's day loop.
        venue_last_usds = Decimal(0)
        venue_last_spread = Decimal(0)
        venue_last_susds_value = Decimal(0)

        current = period.start
        while current <= period.end:
            eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
            prime_usds = Decimal(0)
            prime_spread = Decimal(0)
            prime_susds_value = Decimal(0)
            # Sentinel: when True, the day's computation produced no data
            # (pool not yet seeded, configured coin absent), so we treat it
            # as $0 deduction AND reset the venue carry-forward state to 0.
            # Falling through to the accumulator at the bottom of the loop
            # ensures the day is recorded in ``daily_util`` and not later
            # filled by ``cum_at_or_before`` with a stale prior value.
            zero_day = False
            try:
                block = block_resolver.block_at_or_before(chain_str, eod)
                pool_state = pool_src.read_pool(chain_str, pool_addr, block)
                total_supply = pool_state.total_supply
                coin_idx = None
                if total_supply == 0:
                    zero_day = True
                else:
                    coin_idx = next(
                        (i for i, c in enumerate(pool_state.coins) if c.value == target_coin),
                        None,
                    )
                    if coin_idx is None:
                        _log.warning(
                            "curve_idle_usds: venue %s pool %s does not contain coin %s "
                            "at block %d — recording $0 deduction for day %s.",
                            venue.id, pool_addr.hex(), target_coin.hex(), block, current,
                        )
                        zero_day = True

                if not zero_day:
                    # Full success path: pool is seeded AND the configured
                    # coin is in it. Compute the prime's share of the coin
                    # reserve (par-stable) or the 30bps spread (sUSDS).
                    raw_coin_balance = pool_state.balances[coin_idx]
                    from ..domain.primes import Address as _Addr
                    alm_lp_raw = _balance_of(
                        venue.chain, venue.token.address, _Addr(holder), block,
                    )
                    alm_lp = Decimal(alm_lp_raw) / Decimal(10 ** venue.token.decimals)
                    pool_total = Decimal(total_supply) / Decimal(10 ** venue.token.decimals)

                    if cfg.sky_savings_token:
                        # sUSDS in LP: earn 30bps spread on prime's USDS-equivalent share.
                        # No utilized deduction.
                        yb = KNOWN_YIELD_BEARING_ETHEREUM.get(target_coin)
                        if yb is None:
                            raise ValueError(
                                f"curve_idle_usds sky_savings_token=True for coin "
                                f"{target_coin.hex()} but it is not in "
                                "KNOWN_YIELD_BEARING_ETHEREUM — add it to sky_tokens.py."
                            )
                        _sym, share_decimals, _und, underlying_decimals = yb
                        pps_raw = c2a.convert_to_assets(
                            chain=chain_str, vault=target_coin,
                            shares=10 ** share_decimals, block=block,
                        )
                        pps = Decimal(pps_raw) / Decimal(10 ** underlying_decimals)
                        prime_susds_value = (
                            (alm_lp / pool_total)
                            * (Decimal(raw_coin_balance) / Decimal(10 ** share_decimals))
                            * pps
                        ) if pool_total > 0 else Decimal(0)
                        prime_spread = prime_susds_value * daily_compounding_factor(
                            base_rate_spread_at(current),
                        )
                    elif target_coin in KNOWN_YIELD_BEARING_ETHEREUM:
                        # Yield-bearing but not sky_savings_token: data fetched for
                        # future use; no utilized deduction and no spread revenue yet.
                        pass
                    else:
                        coin_usds = _par_stable_usds(target_coin, raw_coin_balance)
                        prime_usds = (
                            (alm_lp / pool_total) * coin_usds if pool_total > 0 else Decimal(0)
                        )

            except Exception as exc:
                _log.warning(
                    "curve_idle_usds: RPC error for venue %s on %s; carrying forward "
                    "prior value (error: %s).",
                    venue.id, current, type(exc).__name__,
                )
                # Use the THIS-VENUE's last-known successful value, not the
                # cross-venue aggregate in ``daily_util`` — that was the
                # source of the consecutive-failure → zero-out bug.
                prime_usds = venue_last_usds
                prime_spread = venue_last_spread
                prime_susds_value = venue_last_susds_value
            else:
                # On success, update this venue's running carry-forward.
                venue_last_usds = prime_usds
                venue_last_spread = prime_spread
                venue_last_susds_value = prime_susds_value

            daily_util[current] = daily_util.get(current, Decimal(0)) + prime_usds
            daily_spread[current] = daily_spread.get(current, Decimal(0)) + prime_spread
            # Case 3b: full-SSR integral on the same daily sUSDS values,
            # attributed per venue (consumed by the orchestrator to
            # re-book the sUSDS-leg appreciation out of the SDE pot).
            if ssr_history is not None and prime_susds_value > 0:
                susds_ssr_by_venue[venue.id] = (
                    susds_ssr_by_venue.get(venue.id, Decimal(0))
                    + prime_susds_value * daily_compounding_factor(
                        ssr_at_or_before(ssr_history, current),
                    )
                )
            current = current + timedelta(days=1)

    # Build utilized deduction DataFrame (par-stable coins).
    if daily_util:
        days_sorted = sorted(daily_util)
        cum_balance = [daily_util[d] for d in days_sorted]
        daily_net = [cum_balance[0]] + [
            cum_balance[i] - cum_balance[i - 1] for i in range(1, len(cum_balance))
        ]
        util_df = pd.DataFrame({
            "block_date": days_sorted,
            "daily_net": daily_net,
            "cum_balance": cum_balance,
        })
    else:
        util_df = _empty_psm_df()

    susds_spread = sum(daily_spread.values(), Decimal("0"))
    return util_df, susds_spread, susds_ssr_by_venue


def _aggregate_lending_idle_usds(
    prime: Prime,
    period: Period,
    *,
    block_resolver,
) -> tuple[pd.DataFrame, dict[str, Decimal]]:
    """Daily USDS-equivalent of unborrowed underlying inside lending pools,
    summed across all venues with ``lending_idle_usds=True``.

    For each such venue (Cat C/D — Aave aToken / SparkLend spToken) the
    prime's proportional share of the pool's idle underlying is::

        prime_idle_d = (balanceOf(alm, spToken_d) / totalSupply(spToken_d))
                     × balanceOf(spToken_contract, underlying_d)

    ``balanceOf(alm, spToken)`` is the rebased balance (includes accrued
    interest), and ``totalSupply(spToken)`` is the rebased total — so the
    ratio is equivalent to using the scaled (un-rebased) values, which avoids
    needing a separate ``scaledTotalSupply`` call.

    ``balanceOf(spToken_contract, underlying)`` is the balance of the raw
    underlying token (USDS, DAI) sitting idle in the spToken contract — i.e.
    the unborrowed portion of the pool. Deposited capital that has been
    borrowed out is NOT reflected here, so this is exactly the prime-
    settlement-methodology Step 2 "idle underlying in lending pool" deduction.

    The underlying must be a par-stable (USDS, DAI, USDC at $1 per unit).
    Deduction is in USDS-equivalent at face value (divided by underlying decimals).

    Returns a 2-tuple:
    - ``aggregate_df``: ``[block_date, daily_net, cum_balance]`` DataFrame
      where ``cum_balance`` is a daily snapshot matching the PSM3 convention.
      Empty DataFrame if no venue has ``lending_idle_usds=True``.
    - ``per_venue_tw_avg``: ``{venue_id: Decimal}`` — time-weighted mean of
      each venue's daily lending-idle contribution across the period, useful
      for post-hoc CoF re-attribution in the monthly report. Venues not
      contributing return 0.
    """
    from datetime import time
    from ..extract.rpc import balance_of as _balance_of, total_supply_of as _total_supply_of

    venues = [v for v in prime.venues if v.lending_idle_usds]
    if not venues:
        return _empty_psm_df(), {}

    daily_by_date: dict = {}
    # Per-venue daily idle values for tw_avg computation (post-hoc CoF re-attribution).
    per_venue_daily: dict[str, list[Decimal]] = {}

    for venue in venues:
        if venue.underlying is None:
            _log.warning(
                "lending_idle_usds: venue %s has no `underlying` configured — skipping.",
                venue.id,
            )
            continue

        alm_addr = venue.holder_override or prime.alm[venue.chain]
        sptoken_addr = venue.token.address
        underlying_addr = venue.underlying.address
        underlying_decimals = venue.underlying.decimals
        chain = venue.chain

        # Per-venue carry-forward state (mirrors the fix in
        # ``_aggregate_curve_idle_usds``): the shared ``daily_by_date`` is a
        # cross-venue aggregate, so reading from it on failure would either
        # zero-out (consecutive failures) or over-count (cross-venue pickup).
        # ``venue_last_idle = None`` means we haven't observed a successful
        # read yet — a failure at this point can't be "carried forward" from
        # anything real and must propagate, otherwise we'd silently restore
        # the cache-of-zeros antipattern (RPC down on day 1 → 0 → "carry"
        # → 0 for the whole month).
        venue_last_idle: Decimal | None = None
        venue_daily: list[Decimal] = []

        from ..extract.rpc import RPCError as _RPCError
        import requests as _requests

        current = period.start
        while current <= period.end:
            eod = datetime.combine(current, time.max, tzinfo=timezone.utc)
            try:
                block = block_resolver.block_at_or_before(chain.value, eod)

                alm_sptoken_raw = _balance_of(chain, sptoken_addr, alm_addr, block)
                total_supply_raw = _total_supply_of(chain, sptoken_addr, block)

                if total_supply_raw == 0:
                    prime_idle = Decimal(0)
                else:
                    # Pool's idle underlying = underlying balance in the spToken contract
                    pool_idle_raw = _balance_of(chain, underlying_addr, sptoken_addr, block)
                    alm_share = Decimal(alm_sptoken_raw) / Decimal(total_supply_raw)
                    pool_idle_usds = Decimal(pool_idle_raw) / Decimal(10 ** underlying_decimals)
                    prime_idle = alm_share * pool_idle_usds

            except (_RPCError, _requests.HTTPError,
                    _requests.ConnectionError, _requests.Timeout) as exc:
                if venue_last_idle is None:
                    # No successful read to carry forward — fail loud rather
                    # than silently fall back to 0 for the rest of the period.
                    raise
                _log.warning(
                    "lending_idle_usds: RPC error for venue %s on %s; carrying forward "
                    "prior value $%s (error: %s).",
                    venue.id, current, venue_last_idle, type(exc).__name__,
                )
                prime_idle = venue_last_idle
            else:
                venue_last_idle = prime_idle

            daily_by_date[current] = daily_by_date.get(current, Decimal(0)) + prime_idle
            venue_daily.append(prime_idle)
            current = current + timedelta(days=1)

        per_venue_daily[venue.id] = venue_daily

    # Compute per-venue time-weighted average of lending-idle deduction.
    n_days = Decimal(period.n_days) if period.n_days > 0 else Decimal(1)
    per_venue_tw_avg: dict[str, Decimal] = {
        vid: sum(vals, Decimal(0)) / n_days
        for vid, vals in per_venue_daily.items()
        if vals
    }

    if not daily_by_date:
        return _empty_psm_df(), {}

    days_sorted = sorted(daily_by_date)
    cum_balance = [daily_by_date[d] for d in days_sorted]
    daily_net = [cum_balance[0]] + [
        cum_balance[i] - cum_balance[i - 1] for i in range(1, len(cum_balance))
    ]
    return pd.DataFrame({
        "block_date": days_sorted,
        "daily_net": daily_net,
        "cum_balance": cum_balance,
    }), per_venue_tw_avg


def _log_sky_revenue_debug(
    daily: pd.DataFrame,
    sde_per_venue: list[tuple[str, pd.DataFrame]],
    breakdown: list,
    sky_rev_br: Decimal,
    sde_revenue: Decimal,
    *,
    susds_spread_reimb: Decimal = Decimal("0"),
) -> None:
    """Log a full daily breakdown of sky_revenue components at INFO level.

    Emits three sections:
      1. Day-by-day table: debt, each deduction, utilized, APY, daily BR revenue.
      2. Per-SDE-venue daily asset-value table (if any SDEs active).
      3. SDE period-total revenue by venue + grand totals + reconciliation to
         the final sky_revenue (after sUSDS spread reimbursements).
    """
    # ── section 1: daily utilized decomposition ──────────────────────────────
    hdr = (
        f"  {'date':10s}  {'cum_debt':>10s}  {'alm_usds':>9s}  "
        f"{'psm_usds':>9s}  {'sde_av':>9s}  {'curve':>9s}  "
        f"{'lending':>9s}  {'utilized':>10s}  "
        f"{'ssr%':>6s}  {'br%':>6s}  {'daily_rev':>12s}"
    )
    lines = [
        "",
        "  ╔══ sky_revenue daily breakdown ══════════════════════════════════════════════════════════════╗",
        f"  {hdr}",
        "  " + "─" * len(hdr),
    ]
    for _, row in daily.iterrows():
        lines.append(
            f"  {str(row['date']):10s}  "
            f"{float(row['cum_debt'])/1e6:>9.2f}M  "
            f"{float(row['alm_usds'])/1e6:>8.2f}M  "
            f"{float(row['psm_usds'])/1e6:>8.2f}M  "
            f"{float(row['sde_av'])/1e6:>8.2f}M  "
            f"{float(row['curve_idle'])/1e6:>8.2f}M  "
            f"{float(row['lending_idle'])/1e6:>8.2f}M  "
            f"{float(row['utilized'])/1e6:>9.2f}M  "
            f"{row['ssr_apy']*100:>5.2f}%  "
            f"{row['base_apy']*100:>5.2f}%  "
            f"${float(row['daily_sky_rev']):>11,.2f}"
        )
    lines.append("  " + "─" * len(hdr))
    lines.append(
        f"  {'TOTAL BR':>74s}  ${float(sky_rev_br):>11,.2f}"
    )

    # ── section 2: per-SDE-venue daily asset value ────────────────────────────
    if sde_per_venue:
        sde_venue_ids = [vid for vid, _ in sde_per_venue]
        # Build a wide frame: date × venue_id → cum_value (as float for display)
        frames = {}
        for vid, df in sde_per_venue:
            col = df.set_index("block_date")["cum_value"].apply(float).rename(vid)
            frames[vid] = col
        wide = pd.concat(frames.values(), axis=1).fillna(0.0)
        wide["total"] = wide[sde_venue_ids].sum(axis=1)

        sde_hdr = (
            f"  {'date':10s}  "
            + "  ".join(f"{v:>9s}" for v in sde_venue_ids)
            + f"  {'total':>9s}"
        )
        lines += [
            "",
            f"  ── SDE asset value per venue (daily, $M) ──",
            sde_hdr,
            "  " + "─" * (len(sde_hdr) - 2),
        ]
        for dt, wrow in wide.iterrows():
            row_str = f"  {str(dt):10s}  "
            row_str += "  ".join(f"{wrow[v]/1e6:>8.2f}M" for v in sde_venue_ids)
            row_str += f"  {wrow['total']/1e6:>8.2f}M"
            lines.append(row_str)

    # ── section 3: SDE period-total revenue by venue ─────────────────────────
    sde_venues = [vr for vr in breakdown if vr.sd_revenue != Decimal("0")]
    if sde_venues:
        lines += [
            "",
            f"  ── SDE revenue by venue (period totals) ──",
            f"  {'venue':6s}  {'label':30s}  {'sd_share_avg':>12s}  {'value_som':>14s}  {'sd_revenue':>14s}",
            "  " + "─" * 84,
        ]
        for vr in sde_venues:
            lines.append(
                f"  {vr.venue_id:6s}  {(vr.label or '')[:30]:30s}  "
                f"{float(vr.sd_share)*100:>11.1f}%  "
                f"${float(vr.value_som):>13,.2f}  "
                f"${float(vr.sd_revenue):>13,.2f}"
            )
        lines.append("  " + "─" * 84)
        lines.append(f"  {'SDE total':>56s}  ${float(sde_revenue):>13,.2f}")

    sky_rev_total = sky_rev_br + sde_revenue
    sky_rev_final = sky_rev_total - susds_spread_reimb
    lines += [
        "",
        f"  sky_rev_br (BR on utilized−SDE):          ${float(sky_rev_br):>14,.2f}",
        f"  sde_revenue (Σ actual_rev × sd_share):    ${float(sde_revenue):>14,.2f}",
        f"  subtotal:                                 ${float(sky_rev_total):>14,.2f}",
        f"  − sUSDS spread reimbursement (30bps):    -${float(susds_spread_reimb):>14,.2f}",
        f"  ──────────────────────────────────────────────────────",
        f"  sky_revenue (final):                      ${float(sky_rev_final):>14,.2f}",
        "  ╚══════════════════════════════════════════════════════════════════════════════════════════════╝",
        "",
    ]
    _log.debug("\n".join(lines))


def _compute_cash_dist_revenue(
    venue,
    prime: Prime,
    period: Period,
    balance_src: IBalanceSource,
) -> Decimal:
    """Sum actual on-chain inflows from configured cash-distribution payers.

    For each ``CashDistributionSource`` on the venue, queries
    ``directed_inflow_timeseries`` (payer → ALM) and accumulates the
    period-end cumulative inflow minus the pre-period cumulative inflow.
    Returns the total USD amount received during the period.
    """
    total = Decimal("0")
    for src in venue.cash_distributions:
        chain = src.chain if src.chain is not None else venue.chain
        if chain not in prime.alm:
            _log.warning(
                "  [cash_dist] %s — no ALM address for chain %s; skipping payer %s",
                venue.id, chain.value, src.payer.value.hex(),
            )
            continue
        alm = prime.alm[chain]
        pin_block = period.pin_blocks[chain]
        df = balance_src.directed_inflow_timeseries(
            chain=chain.value,
            token=src.token.value,
            from_addr=src.payer.value,
            to_addr=alm.value,
            start=prime.start_date,
            pin_block=pin_block,
        )
        cum_eom = cum_at_or_before(df, "cum_inflow", period.end)
        cum_pre = cum_at_or_before(df, "cum_inflow", period.start - timedelta(days=1))
        total += cum_eom - cum_pre
    return total


def compute_monthly_pnl(
    prime: Prime,
    month: Month,
    *,
    sources: Sources | None = None,
    pin_blocks_eom: dict[Chain, int] | None = None,
    pin_blocks_som: dict[Chain, int] | None = None,
    sky_only: bool = False,
) -> MonthlyPnL:
    """Compute the full monthly settlement for ``prime`` × ``month``.

    Block resolution: by default, EoM and SoM blocks are resolved live via RPC
    (one binary search per chain, ~25 RPC calls each). Tests can supply both
    dicts explicitly to skip RPC entirely.

    When ``sky_only=True`` the per-venue pricing loop (step 3) is restricted to
    SDE venues only, and ``compute_agent_rate`` is skipped entirely. This is
    substantially faster because it avoids the hundreds of per-venue RPC calls
    needed for prime_agent_revenue. The returned ``MonthlyPnL`` has
    ``prime_agent_revenue=0`` and ``agent_rate=0``.

    **Debug mode — sky_revenue caveat.** sky_revenue is accurate to within
    ``Σ Cat B susds_spread_reimbursement`` for primes with
    ``sky_savings_token`` Cat B venues. Those venues are not SDE entries, so
    they are skipped in sky_only mode; their per-venue 30bps reimbursements
    are therefore NOT deducted from sky_revenue. Curve LP and PSM3 sUSDS-leg
    spread components are computed pre-loop and ARE deducted. For canonical
    sky_revenue use the default (non-sky-only) path. A WARNING is emitted at
    invocation time if Cat B sUSDS venues are present.

    **sky_only + Case 3b (Curve sUSDS-leg SSR re-attribution).** SDE venues
    with a sUSDS leg (Spark S24) ARE processed in sky_only mode, and the
    Case 3b re-attribution (PRD §10) deliberately stays active: it removes
    the sUSDS-leg SSR from the SDE-eligible ``actual_revenue``, lowering
    ``sde_revenue`` / ``sky_revenue`` by the former double-collect. This is
    the CANONICAL sky_revenue (matching the full run) — Sky's claim on that
    appreciation already flows via the BR − 30bps machinery, so the SDE
    redirect was double-billing. The offsetting prime-side credit
    (``external_revenue``) is invisible in sky_only output because
    ``prime_agent_revenue`` is pinned to 0 — an expected display asymmetry
    of the debug mode, not an accounting hole.
    """
    sources = _sources_from_prime(prime, sources)

    # sky_only debug-mode warning — see docstring above.
    if sky_only:
        _catB_sUSDS_venues = [
            v.id for v in prime.venues
            if getattr(v, "sky_savings_token", False)
            and not getattr(v, "demand_side_spread", False)
        ]
        if _catB_sUSDS_venues:
            _log.warning(
                "sky_only mode: prime %s has Cat B sky_savings_token venues %s "
                "— their 30bps spread reimbursement will NOT be deducted from "
                "sky_revenue. For canonical sky_revenue, re-run without "
                "--sky-only. (Curve LP and PSM3 sUSDS spreads ARE deducted.)",
                prime.id, _catB_sUSDS_venues,
            )

    # 1. Resolve the block resolver up front. We need it for pin_blocks (if not
    #    supplied) AND for V3 inflow tracking (event block → date conversion).
    resolver = (
        sources.block_resolver
        if sources.block_resolver is not None
        else get_block_resolver()
    )
    period_unpinned = Period.from_month(month)

    # Resolve EoM and SoM pin blocks for all chains in parallel (two concurrent
    # ThreadPoolExecutors, one per anchor). Each pool already parallelises across
    # chains internally, so for Spark (6 chains × 2 anchors) this cuts ~300
    # sequential RPC calls down to the cost of ~25 (one chain, one anchor).
    if pin_blocks_eom is None or pin_blocks_som is None:
        from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _ac
        _log.info(
            "resolving pin blocks for %d chain(s) (EoM + SoM in parallel)...",
            len(prime.chains),
        )
        eom_anchor = period_unpinned.end_eod_utc
        som_anchor = _previous_day_eod_utc(period_unpinned.start)
        with _TPE(max_workers=2) as _outer:
            _fut_eom = (
                _outer.submit(_resolve_pin_blocks, eom_anchor, prime.chains, resolver)
                if pin_blocks_eom is None else None
            )
            _fut_som = (
                _outer.submit(_resolve_pin_blocks, som_anchor, prime.chains, resolver)
                if pin_blocks_som is None else None
            )
            if _fut_eom is not None:
                pin_blocks_eom = _fut_eom.result()
            if _fut_som is not None:
                pin_blocks_som = _fut_som.result()
        _log.info("pin blocks resolved: eom=%s  som=%s", pin_blocks_eom, pin_blocks_som)

    # 1b. Upgrade to DuneBlockResolver for every chain that has Dune coverage.
    # One Dune query per chain replaces every per-day RPC binary-search (~25
    # eth_getBlockByNumber calls per anchor). For Spark (6 chains × 28 days ×
    # multiple timeseries per chain), this eliminates thousands of RPC calls.
    # Chains without a Dune blocks table fall back silently to RPCBlockResolver.
    # Queries are issued in parallel via a ThreadPoolExecutor.
    #
    # Dune chain → blocks table coverage (as of 2026-05):
    _DUNE_BLOCK_CHAINS = frozenset({
        "ethereum", "base", "arbitrum", "optimism", "avalanche_c",
        # unichain, plume, monad: not yet in Dune spellbook — use RPC
    })
    if sources.block_resolver is None and pin_blocks_eom:
        import os as _os
        if _os.environ.get("DUNE_API_KEY"):
            try:
                from concurrent.futures import ThreadPoolExecutor as _TPE2, as_completed as _ac2
                from ..normalize.sources.dune_block_resolver import (
                    DuneBlockResolver as _DBR,
                    MultiChainBlockResolver as _MCR,
                )
                from ..normalize.sources.rpc_block_resolver import RPCBlockResolver as _RBR
                dune_chains = {c for c in prime.chains if c.value in _DUNE_BLOCK_CHAINS}
                rpc_chains  = prime.chains - dune_chains
                _log.info(
                    "upgrading block resolver: DuneBlockResolver for %s, "
                    "RPCBlockResolver for %s",
                    sorted(c.value for c in dune_chains),
                    sorted(c.value for c in rpc_chains),
                )
                def _make_dune_resolver(chain: "Chain") -> "tuple[Chain, _DBR]":
                    dbr = _DBR(
                        chain=chain.value,
                        start_date=prime.start_date,
                        end_date=period_unpinned.end,
                        pin_block=pin_blocks_eom[chain],
                    )
                    return chain, dbr
                with _TPE2(max_workers=len(dune_chains) or 1) as _pool2:
                    _futs = {_pool2.submit(_make_dune_resolver, c): c for c in dune_chains}
                    per_chain: dict[str, object] = {}
                    for _f in _ac2(_futs):
                        _chain, _dbr = _f.result()
                        per_chain[_chain.value] = _dbr
                        _log.info(
                            "  DuneBlockResolver(%s): %d dates, %s → %s",
                            _chain.value, len(_dbr._dates),
                            _dbr._dates[0], _dbr._dates[-1],
                        )
                _rpc_fallback = _RBR()
                for c in rpc_chains:
                    per_chain[c.value] = _rpc_fallback
                resolver = _MCR(per_chain)
            except Exception as _e:
                _log.warning(
                    "DuneBlockResolver init failed (%s) — falling back to RPC for all chains", _e
                )

    # 1c. Upgrade ``sources.balance`` to a per-chain dispatcher for chains
    # where ``RPCBalanceSource`` (``eth_getLogs``-based) is known to be
    # viable. NOT every non-Dune chain qualifies: Plume's public RPC and
    # Monad's free public RPC (``rpc.monad.xyz``) both cap
    # ``eth_getLogs`` ranges so tight (~100 blocks) that a year-long
    # Transfer scan would take 300K+ requests. For those chains the
    # ``DuneBalanceSource`` returns empty silently (no chain coverage in
    # ``tokens.transfers``) which is the right default for venues that
    # use the closed-form helper (Cat B → ``_erc4626_shares_weighted_inflow``;
    # Cat C → ``_atoken_index_weighted_inflow``).
    #
    # Add a chain here once its RPC supports eth_getLogs over a useful
    # range (e.g. an archive endpoint with no 100-block ceiling) AND the
    # prime has Cat A / Cat E venues that need event-based inflow tracking.
    _RPC_BALANCE_CHAINS: frozenset[str] = frozenset()  # none viable today
    _rpc_balance_chains = {
        c for c in prime.chains
        if c.value in _RPC_BALANCE_CHAINS and c.value not in _DUNE_BLOCK_CHAINS
    }
    if sources.balance is not None and _rpc_balance_chains:
        try:
            import dataclasses as _dc
            from ..normalize.sources.multi_chain_balance import MultiChainBalanceSource as _MCBS
            from ..normalize.sources.rpc_balances import RPCBalanceSource as _RBS
            _rpc_balance = _RBS(block_resolver=resolver)
            _per_chain_balance: dict[str, object] = {
                c.value: sources.balance for c in prime.chains if c.value in _DUNE_BLOCK_CHAINS
            }
            for c in _rpc_balance_chains:
                _per_chain_balance[c.value] = _rpc_balance
            sources = _dc.replace(sources, balance=_MCBS(_per_chain_balance))
            _log.info(
                "upgraded balance source: Dune for %s, RPC for %s",
                sorted(c.value for c in prime.chains if c.value in _DUNE_BLOCK_CHAINS),
                sorted(c.value for c in _rpc_balance_chains),
            )
        except Exception as _e:  # noqa: BLE001 — best-effort; keep going on the original source
            _log.warning(
                "balance-source upgrade failed (%s) — keeping single-backend source",
                _e,
            )

    period = Period(period_unpinned.start, period_unpinned.end, pin_blocks=pin_blocks_eom)

    _log.info("step 2: gathering Dune/normalize inputs (debt, balances, SSR)...")
    # 2. Gather Normalize inputs for sky_revenue + agent_rate (Ethereum-only).
    _log.info("  2a: debt timeseries...")
    debt = get_debt_timeseries(prime, period, source=sources.debt, block_resolver=resolver)
    if not sky_only:
        # ``som_block`` + ``balance_at`` enable the on-chain anchor in
        # ``get_subproxy_balance_timeseries`` — catches pre-period funding
        # not surfaced by Dune ``tokens.transfers`` for the SubProxy
        # address (Spark SubProxy was holding ~$36M USDS throughout 2026
        # that Dune-event reconstruction didn't see). Routed through
        # ``sources.position_balance`` so tests can inject deterministic
        # values; falls back to the registry so the anchor is active
        # in production runs that don't pass an explicit Sources().
        from ..normalize.registry import get_position_balance_source as _get_pb
        _pb_src_sub = sources.position_balance or _get_pb()
        _balance_at_sub = _pb_src_sub.balance_at
        _eth_som = (pin_blocks_som or {}).get(Chain.ETHEREUM)
        _log.info("  2b: subproxy USDS balance...")
        sub_usds = get_subproxy_balance_timeseries(
            prime, Chain.ETHEREUM, USDS_ETHEREUM, period, source=sources.balance,
            som_block=_eth_som, balance_at=_balance_at_sub,
        )
        _log.info("  2c: subproxy sUSDS shares...")
        sub_susds_shares = get_subproxy_balance_timeseries(
            prime, Chain.ETHEREUM, sUSDS_ETHEREUM, period, source=sources.balance,
            som_block=_eth_som, balance_at=_balance_at_sub,
        )
        # Convert sUSDS shares → USDS-denominated cost-basis principal:
        # ``principal = Σ daily_net_shares × pps_at_that_day's_eod_block``. This is
        # the deposit-time value, NOT the current value (which includes accrued
        # SSR — using current value would double-count savings). Used by
        # agent_rate (earning base); NOT passed to sky_revenue (subproxy balances
        # are treasury/risk capital, not pure ilk-debt proceeds).
        _log.info("  2d: sUSDS → principal conversion (RPC per day)...")
        sub_susds = _susds_shares_to_principal(
            sub_susds_shares,
            sources=sources,
            block_resolver=resolver,
            chain=Chain.ETHEREUM,
        )
    else:
        _log.info("  2b-d: skipped (sky_only mode — subproxy balances not needed)")
        sub_usds = sub_susds = None
    _log.info("  2e: ALM USDS balance (all chains)...")
    alm_usds = _aggregate_alm_usds(
        prime, period, balance_source=sources.balance,
    )
    # Sum PSM USDS-equivalent across ALL chains where the prime has a PSM
    # configured. The prime's debt (cum_debt) is Ethereum-only (Vat), but
    # USDS-equivalent capital parked at L2 PSM3 (Spark on Base / Arbitrum /
    # Optimism / Unichain) was funded from that debt and reduces utilized.
    # Sky's mainnet PSM stack is non-custodial and is intentionally NOT
    # configured (see PRD §17.11) — primes never accumulate balances there.
    _log.info("  2f: PSM USDS aggregate...")
    psm_usds = _aggregate_psm_usds(
        prime, period,
        balance_source=sources.balance,
        psm3_source=sources.psm3,
        block_resolver=resolver,
        position_balance_source=sources.position_balance,
        convert_to_assets_source=sources.convert_to_assets,
    )
    # SSR history is needed by the Case-3 sUSDS appreciation integrals
    # below (PSM3 leg + Curve leg) as well as agent_rate / sky_revenue —
    # fetch it before the PSM3/Curve aggregation.
    _log.info("  2g: SSR history...")
    ssr = get_ssr_history(prime, period, source=sources.ssr)
    # Neutralising 30 bps spread credit on the sUSDS slice of PSM3 holdings
    # (PRD §17.11). Sky charges full BR on this slice (utilized NOT reduced
    # for sUSDS in compute_sky_revenue) AND pays SSR to sUSDS holders
    # (= prime via PSM3 share appreciation). Crediting 30 bps back keeps the
    # composite (+SSR − BR + 30 bps = 0) economically neutral on idle sUSDS.
    psm3_susds_spread = _psm3_susds_spread(psm_usds, period)
    # Case 3a (PRD §10): the SSR appreciation on the PSM3 sUSDS slice is
    # real prime income (accrues inside the prime's PSM3 share value) that
    # was never booked — the books showed −SSR × V on this leg while the
    # true PnL is 0 (+SSR − BR + 30bps = 0). Book it as Prime Revenue;
    # Sky's single collection (BR − 30bps = SSR) stays where it is.
    # Skipped in sky_only mode — it's a prime-side-only credit and
    # prime_agent_revenue is pinned to 0 there.
    psm3_susds_appreciation = (
        _psm3_susds_appreciation(psm_usds, period, ssr)
        if not sky_only else Decimal("0")
    )
    # Prime's proportional USDS-equivalent in configured Curve pools — Step 2
    # idle AMM USDS. Computed daily via RPC (``read_pool`` + ``balanceOf`` +
    # ``convertToAssets`` for sUSDS legs). Returns empty frame if no venue has
    # ``curve_idle_usds`` set (e.g. Grove, OBEX — $0, no cost).
    # Returns (utilized_deduction_df, curve_susds_spread, curve_susds_ssr_by_venue).
    # ``curve_susds_spread`` (30bps) is folded into the sky-revenue reduction
    # below (d255ed2); ``curve_susds_ssr_by_venue`` (full-SSR integral, Case 3b)
    # re-attributes the sUSDS-leg appreciation embedded in the LP MtM: removed
    # from the SDE-eligible actual_revenue and re-booked 100% to the prime.
    curve_idle_usds, curve_susds_spread, curve_susds_ssr_by_venue = (
        _aggregate_curve_idle_usds(
            prime, period,
            curve_pool_source=sources.curve_pool,
            block_resolver=resolver,
            convert_to_assets_source=sources.convert_to_assets,
            ssr_history=ssr,
        )
    )
    # Uniswap V4 idle USDS (USDS leg of v4 LP positions) — Step 2 idle AMM USDS,
    # same utilized-deduction role as the Curve idle path. Merged into
    # ``curve_idle_usds`` by summing the daily ``cum_balance`` snapshots so
    # ``compute_sky_revenue`` deducts the combined AMM idle USDS.
    _univ4_idle = _aggregate_univ4_idle_usds(
        prime, period,
        v4_source=sources.v4_position,
        block_resolver=resolver,
    )
    if not _univ4_idle.empty:
        if curve_idle_usds is None or curve_idle_usds.empty:
            curve_idle_usds = _univ4_idle
        else:
            curve_idle_usds = (
                pd.concat([
                    curve_idle_usds[["block_date", "daily_net", "cum_balance"]],
                    _univ4_idle[["block_date", "daily_net", "cum_balance"]],
                ])
                .groupby("block_date", as_index=False)[["daily_net", "cum_balance"]].sum()
                .sort_values("block_date").reset_index(drop=True)
            )
    # Prime's share of unborrowed underlying in configured lending pools — Step 2
    # idle lending pool USDS. Computed daily via ``balanceOf`` + ``totalSupply``.
    # Returns (empty frame, {}) if no venue has ``lending_idle_usds=True``.
    lending_idle_usds, _lending_idle_tw_avg = _aggregate_lending_idle_usds(
        prime, period,
        block_resolver=resolver,
    )
    _check_centrifuge_in_flight(prime, pin_blocks_som, pin_blocks_eom)

    # SDE table — config-driven Sky Direct exposures (replaces the legacy
    # ``Venue.sky_direct: bool`` flag). Empty table = no venues are SDE.
    sde_table = load_sde_table()
    # Each entry is (venue_id: str, df: pd.DataFrame) so the debug logger can
    # show a per-SDE-venue daily asset-value table.
    sde_asset_value_per_venue: list[tuple[str, pd.DataFrame]] = []

    import time as _time
    n_venues = sum(1 for v in prime.venues if not v.skip)
    _log.info("step 3: per-venue pricing for %d venue(s)...", n_venues)
    # 3. Per-venue: value at SoM + EoM, inflow timeseries.
    venue_inputs: list[VenueRevenueInputs] = []
    # Display-only venues: tracked for monthly reports but excluded from
    # prime_agent_revenue / sky_revenue / NAV invariant. See ``Venue.display_only``.
    display_only_breakdown: list[VenueRevenue] = []

    # 3a. Cash-distribution venues — attributed directly as prime revenue,
    # bypassing the standard SoM/EoM formula and the sky-revenue path.
    # Must run before the main loop so skipped venues (e.g. E21 GACLO-1,
    # which has no reliable NAV oracle) are still included in the output.
    _cash_dist_balance_src = (
        sources.balance if sources.balance is not None else get_balance_source()
    )
    for venue in prime.venues:
        if not venue.cash_distributions:
            continue
        _log.info(
            "  [cash_dist] %s — computing cash-distribution revenue (%d source(s))...",
            venue.id, len(venue.cash_distributions),
        )
        cash_rev = _compute_cash_dist_revenue(
            venue, prime, period, _cash_dist_balance_src
        )
        _log.info("  [cash_dist] %s — total cash revenue: %s", venue.id, cash_rev)
        # value_som / value_eom: try the standard ``get_position_value`` so
        # cash-distribution venues with a meaningful on-chain balance (e.g.
        # E21 GACLO-1 with ``nav_oracle: const_one``) report the principal
        # for display + CoF parity with BA Labs. Skip when ``venue.skip`` is
        # set (oracle untrusted / token doesn't expose required reads) or
        # when ``get_position_value`` raises (e.g. proxy reverts), in which
        # case we fall back to $0 — revenue is still captured via the cash-
        # dist override above.
        v_som = Decimal("0")
        v_eom = Decimal("0")
        if not venue.skip and venue.chain in pin_blocks_som and venue.chain in pin_blocks_eom:
            from ..extract.dune import DuneError as _DuneError
            from ..extract.rpc import RPCError as _RPCError
            import requests as _requests
            try:
                v_som = get_position_value(
                    prime, venue, pin_blocks_som[venue.chain],
                    balance_source=sources.position_balance,
                    flow_source=sources.balance,
                    erc4626_source=sources.convert_to_assets,
                    v3_position_source=sources.v3_position,
                    v4_position_source=sources.v4_position,
                    curve_pool_source=sources.curve_pool,
                    block_resolver=resolver,
                    nav_oracle_resolver=sources.nav_oracle_resolver,
                )
                v_eom = get_position_value(
                    prime, venue, pin_blocks_eom[venue.chain],
                    balance_source=sources.position_balance,
                    flow_source=sources.balance,
                    erc4626_source=sources.convert_to_assets,
                    v3_position_source=sources.v3_position,
                    v4_position_source=sources.v4_position,
                    curve_pool_source=sources.curve_pool,
                    block_resolver=resolver,
                    nav_oracle_resolver=sources.nav_oracle_resolver,
                )
            except (_RPCError, _DuneError, _requests.HTTPError,
                    _requests.ConnectionError, _requests.Timeout) as _e:
                # Transient transport failure — fall back to $0 and let the
                # cash-distribution revenue stand. Config errors
                # (UnsupportedPricingError, ValueError) propagate so a YAML
                # misconfig doesn't silently undo an un-skip like E21's.
                _log.warning(
                    "  [cash_dist] %s — get_position_value transient failure "
                    "(%s); leaving value_som/eom at $0",
                    venue.id, type(_e).__name__,
                )
        venue_inputs.append(VenueRevenueInputs(
            venue=venue,
            value_som=v_som,
            value_eom=v_eom,
            inflow_timeseries=pd.DataFrame(
                columns=["block_date", "daily_inflow", "cum_inflow"]
            ),
            sde_entry=None,
            actual_revenue_override=cash_rev,
        ))

    _venue_idx = 0
    # Populated in the sky_savings_token Cat B sub-case; injected into
    # VenueRevenue after compute_prime_agent_revenue and summed to reduce
    # sky_rev (see step 4 below).
    _susds_spread_reimbs: dict[str, Decimal] = {}
    for venue in prime.venues:
        if venue.cash_distributions:
            # Already handled by the cash-distribution pass above — skip here
            # to avoid double-counting. A venue with cash_distributions should
            # not also run through the standard SoM/EoM compute path.
            continue
        if venue.skip:
            # Excluded from MSC — typically venues whose NAV oracle is
            # untrusted or whose underlying is too volatile (e.g. Avalanche
            # cross-chain RWAs without a reliable feed). Logged once for
            # provenance.
            _log.info(
                "  [skip] %s (%s, %s) — venue.skip=True.",
                venue.id, venue.token.symbol, venue.chain.value,
            )
            continue
        # Initialised here; Cat E ERC-4626 venues set these before the
        # VenueRevenueInputs append below.
        _erc4626_period_inflow: "Decimal | None" = None
        _ev_ts: "pd.DataFrame | None" = None
        if venue.pricing_category == PricingCategory.SPARK_SAVINGS_V2:
            # Spark Savings V2 vaults — position-only tracking.
            #
            # These vaults are EXCLUDED from prime_agent_revenue. The MSC
            # accounting boundary is the ALM proxy: yield on the deployed
            # underlying is already captured by Spark's existing Cat A/B/C/E
            # venues (S1–S55), and the depositor-side VSR liability is a
            # vault-layer obligation of Spark's retail product, OUTSIDE that
            # boundary — depositor withdrawals (principal + accrued VSR)
            # leave the ALM as capital flows, not negative revenue. See
            # ``docs/spark/PRD_savings_vaults.md`` §3. Consequence: expect a
            # ~VSR-sized reconciliation gap vs Spark/BA Labs dashboards,
            # whose surplus formula nets borrow_cost.
            #
            # We track value_som/value_eom for position reporting only
            # (surfaced in the "Position-only venues" section of summary.md
            # and in venue_breakdown[].hide_per_venue_pnl).
            #
            # spETH (S58) is also skipped: its underlying is WETH (not a
            # par-stable), so totalAssets() is denominated in WETH and cannot
            # be summed with the USD venue table. See PRD §9.
            from ..normalize.prices import is_par_stable
            if venue.underlying is None or not is_par_stable(venue.underlying):
                _log.info(
                    "  [savings_v2] %s — underlying %s is not a par-stable; "
                    "skipping (out of scope — see PRD §9).",
                    venue.id,
                    venue.underlying.symbol if venue.underlying else "(none)",
                )
                continue
            if venue.chain not in pin_blocks_som or venue.chain not in period.pin_blocks:
                # WARNING, not info: the position disappears from the
                # position-only section / BA Labs reconciliation. The
                # standard venue path raises on a missing chain; this softer
                # skip is deliberate ($0 revenue contribution) but must stay
                # loud.
                _log.warning(
                    "  [savings_v2] %s — no SoM/EoM block for chain %s; "
                    "skipping (position will be MISSING from the report).",
                    venue.id, venue.chain.value,
                )
                continue
            som_block = pin_blocks_som[venue.chain]
            eom_block = period.pin_blocks[venue.chain]
            from ..extract import rpc as _rpc_sv2
            from ..extract.rpc import RPCError as _RPCError
            from ..extract.hypersync import HyperSyncError as _HSError
            import requests as _requests
            _ud = venue.underlying.decimals
            try:
                if venue.total_assets_source == "hypersync_underlying":
                    # No archive RPC on this chain (Robinhood) — reconstruct
                    # the vault's underlying balance from Transfer logs (ONE
                    # fetch serves both cutoffs — the SoM rows are a subset
                    # of the EoM range). See Venue.total_assets_source for
                    # the (small, documented) assetsOutstanding
                    # underestimate vs true totalAssets.
                    from ..normalize.sources.hypersync_position_balance import (
                        erc20_balances_from_transfers as _ebals,
                    )
                    _by_block = _ebals(
                        venue.chain.value,
                        venue.underlying.address.value,
                        venue.token.address.value,
                        (som_block, eom_block),
                    )
                    total_som = Decimal(_by_block[som_block]) / Decimal(10 ** _ud)
                    total_eom = Decimal(_by_block[eom_block]) / Decimal(10 ** _ud)
                else:
                    total_som = Decimal(_rpc_sv2.total_assets_of(
                        venue.chain, venue.token.address, som_block,
                    )) / Decimal(10 ** _ud)
                    total_eom = Decimal(_rpc_sv2.total_assets_of(
                        venue.chain, venue.token.address, eom_block,
                    )) / Decimal(10 ** _ud)
            # _HSError covers the hypersync_underlying path's transport
            # failures (the HyperSync client wraps its transient errors in
            # HyperSyncError) — same warn-and-skip degradation the RPC path
            # gets. DB errors from the optional log store still propagate:
            # a broken DATABASE_URL is misconfiguration, not transience.
            except (_RPCError, _HSError, _requests.HTTPError,
                    _requests.ConnectionError, _requests.Timeout) as _e:
                # Transient transport failure only — programming errors
                # propagate so a position doesn't silently vanish from the
                # BA Labs balance-sheet reconciliation.
                _log.warning(
                    "  [savings_v2] %s — totalAssets read failed (%s); skipping.",
                    venue.id, type(_e).__name__,
                )
                continue
            # eth_call maps reverts/empty returndata to 0, so a poisoned read
            # is indistinguishable from an empty vault (the old daily path
            # raised on a zero-pps baseline for the same reason). Don't book
            # a fabricated $0 row for a vault that holds $1B+; an actually
            # wound-down vault produces the same skip, which costs nothing.
            if total_som == 0 and total_eom == 0:
                _log.warning(
                    "  [savings_v2] %s — totalAssets is 0 at both SoM and EoM "
                    "(empty vault, or a poisoned/reverted read mapped to 0); "
                    "skipping the $0 position row. Verify against the vault "
                    "contract if this venue is expected to hold funds.",
                    venue.id,
                )
                continue
            if total_som == 0 or total_eom == 0:
                _log.warning(
                    "  [savings_v2] %s — totalAssets is 0 at one boundary "
                    "(som=$%.2f eom=$%.2f); legitimate for a vault launch/"
                    "wind-down mid-period, otherwise a poisoned read. "
                    "Recording as-is.",
                    venue.id, float(total_som), float(total_eom),
                )
            _log.info(
                "  [savings_v2] %s (%s on %s): position-only "
                "total_assets_som=$%.2f  total_assets_eom=$%.2f "
                "(excluded from prime_agent_revenue)",
                venue.id, venue.token.symbol, venue.chain.value,
                float(total_som), float(total_eom),
            )
            venue_inputs.append(VenueRevenueInputs(
                venue=venue,
                value_som=total_som,
                value_eom=total_eom,
                inflow_timeseries=pd.DataFrame(
                    columns=["block_date", "daily_inflow", "cum_inflow"]
                ),
                sde_entry=None,
                actual_revenue_override=Decimal("0"),
            ))
            continue
        # In sky_only mode, check the SDE table up-front so we can skip all
        # non-SDE venues before doing any RPC / pricing work.
        _early_sde = sde_table.overlaps_venue(prime.id, venue.id, period.start, period.end)
        if sky_only and _early_sde is None:
            _log.info("  [sky_only] skipping %s (no active SDE entry)", venue.id)
            continue


        _venue_idx += 1
        _venue_t0 = _time.monotonic()
        _log.info(
            "  [%d/%d] %s  %s/%s  starting...",
            _venue_idx, n_venues, venue.id, venue.chain.value,
            venue.pricing_category.value,
        )
        if venue.chain not in pin_blocks_som:
            raise ValueError(
                f"Missing SoM pin_block for chain {venue.chain.value} "
                f"(needed by venue {venue.id})"
            )
        som_block = pin_blocks_som[venue.chain]
        eom_block = pin_blocks_eom[venue.chain]

        # L2 sUSDS Cat B venues (S37/S43/S47/S51) use proxy contracts that
        # don't expose ``convertToAssets`` — the standard ERC-4626 path
        # raises. Defer to the dedicated PSM3-pps recompute below
        # (``_l2_susds_value``) by starting at 0; the value gets overwritten
        # before VenueRevenue is constructed.
        #
        # The guard MUST match the recompute condition exactly
        # (``chain != ETHEREUM and chain in prime.psm``) — otherwise a future
        # sUSDS Cat B venue on a chain without a registered PSM3 would have
        # its values zeroed here AND skipped by the recompute, silently
        # reporting $0 for a non-zero position.
        _defer_l2_susds = (
            venue.pricing_category == PricingCategory.ERC4626_VAULT
            and venue.sky_savings_token
            and venue.chain != Chain.ETHEREUM
            and venue.chain in prime.psm
        )
        if _defer_l2_susds:
            value_som = Decimal("0")
            value_eom = Decimal("0")
        else:
            value_som = get_position_value(
                prime, venue, som_block,
                balance_source=sources.position_balance,
                flow_source=sources.balance,
                erc4626_source=sources.convert_to_assets,
                v3_position_source=sources.v3_position,
                v4_position_source=sources.v4_position,
                curve_pool_source=sources.curve_pool,
                block_resolver=resolver,
                nav_oracle_resolver=sources.nav_oracle_resolver,
            )
            value_eom = get_position_value(
                prime, venue, eom_block,
                balance_source=sources.position_balance,
                flow_source=sources.balance,
                erc4626_source=sources.convert_to_assets,
                v3_position_source=sources.v3_position,
                v4_position_source=sources.v4_position,
                curve_pool_source=sources.curve_pool,
                block_resolver=resolver,
                nav_oracle_resolver=sources.nav_oracle_resolver,
            )

        if venue.display_only:
            # Tracked for monthly reports but excluded from prime_agent_revenue /
            # sky_revenue / NAV invariant. Any realized spread on the round-trip
            # is recognized at the anchor venue (see ``paired_with`` /
            # ``paired_source``) via the Cat A paired-principal-cap classifier.
            _log.info(
                "  [%d/%d] %s  [display-only]  som=$%.0f  eom=$%.0f",
                _venue_idx, n_venues, venue.id, value_som, value_eom,
            )
            display_only_breakdown.append(VenueRevenue(
                venue_id=venue.id,
                label=venue.label,
                value_som=value_som,
                value_eom=value_eom,
                period_inflow=Decimal("0"),
                revenue=Decimal("0"),
                cof_excluded=venue.cof_excluded,
                pricing_category=venue.pricing_category.value,
                hide_per_venue_pnl=venue.hide_per_venue_pnl,
            ))
            continue

        # Inflow timeseries — three branches:
        #
        # 1. Uniswap V3 — pool-emitted IncreaseLiquidity / DecreaseLiquidity
        #    events carry raw token amounts directly. Source is the same
        #    IV3PositionSource used for valuation.
        # 2. Cat E (RWA managers) and other Cat F (no single underlying) —
        #    deferred until per-protocol event sourcing lands. Empty inflow
        #    means period revenue collapses to MtM Δ.
        # 3. Default (Cat A/B/C/D with a known underlying) — Dune `tokens.transfers`
        #    directed flow from ALM to venue address.
        #
        # susds_spread is set to a Decimal for Cat B yield-bearing venues (sUSDS)
        # and remains None for all other venues. It is threaded into
        # VenueRevenueInputs.actual_revenue_override below.
        susds_spread: Decimal | None = None
        # susds_mtm_adjustment is set to a NEGATIVE Decimal for mixed-source
        # sUSDS venues (Spark S32, ``deduct_savings_v2_deployed``): the
        # daily-integrated SSR accrual on the depositor-sourced spUSDC slice,
        # removed from the raw MtM via
        # ``VenueRevenueInputs.actual_revenue_adjustment``. Zero elsewhere.
        susds_mtm_adjustment: Decimal = Decimal("0")
        # external_revenue_for_venue is set to a non-zero Decimal in the Cat C
        # branch when ``prime.external_alm_sources[venue.chain]`` is populated
        # — captures Merkl-style aToken drops as a separate revenue stream
        # outside the closed-form pool-native yield formula. Stays at 0 for
        # all other pricing categories (today only Cat C has the path wired).
        external_revenue_for_venue: Decimal = Decimal("0")
        if venue.lp_kind == "uniswap_v3":
            from ..normalize.positions import _uniswap_v3_inflow_timeseries
            v3_src = sources.v3_position
            if v3_src is None:
                # Prefer the Dune-backed source for Ethereum when DUNE_API_KEY
                # is set — Alchemy's free-tier ``eth_getLogs`` caps at 10K
                # blocks / 10K logs per call and rejects wider scans with
                # HTTP 400 on busy pools (Grove E12 AUSD/USDC). The Dune
                # variant reads the same liquidity events from
                # ``ethereum.logs`` in one query regardless of range.
                #
                # DuneV3InflowSource is Ethereum-only: its SQL hardcodes
                # ``FROM ethereum.logs``. For chains not indexed by Dune
                # (Monad, Plume, Unichain) fall back to RPC eth_getLogs.
                import os as _os
                from ..domain.primes import Chain as _Chain
                from ..normalize.sources.uniswap_v3 import RPCUniswapV3PositionSource
                overrides = (
                    {venue.chain: venue.nft_position_manager}
                    if venue.nft_position_manager is not None
                    else None
                )
                if _os.environ.get("DUNE_API_KEY") and venue.chain == _Chain.ETHEREUM:
                    from ..normalize.sources.dune_v3_inflow import DuneV3InflowSource
                    v3_src = DuneV3InflowSource(nfpm_per_chain=overrides)
                else:
                    v3_src = RPCUniswapV3PositionSource(nfpm_per_chain=overrides)
            inflow_ts = _uniswap_v3_inflow_timeseries(
                prime, venue, som_block, eom_block,
                source=v3_src,
                block_to_date=lambda b, _c=venue.chain.value: resolver.block_to_date(_c, b),
            )
        elif venue.lp_kind == "uniswap_v4":
            # V4 capital inflow from ``ModifyLiquidity`` events (liquidityDelta
            # priced at the event block). Mirrors the V3 path so revenue =
            # Δvalue − Σ inflow holds even when positions are minted/burned
            # mid-period (e.g. the 324004/324005 PYUSD/USDS positions minted
            # 2026-06). See ``_uniswap_v4_inflow_timeseries``.
            from ..normalize.positions import _uniswap_v4_inflow_timeseries
            v4_src = sources.v4_position
            if v4_src is None:
                v4_src = _univ4_default_source(venue)
            inflow_ts = _uniswap_v4_inflow_timeseries(
                prime, venue, som_block, eom_block,
                source=v4_src,
                block_to_date=lambda b, _c=venue.chain.value: resolver.block_to_date(_c, b),
            )
        elif venue.lp_kind == "curve_stableswap":
            # Closed-form Curve inflow analogous to Cat C aToken: LP balance
            # is the "scaled" amount, unit_price is the "index". Avoids the
            # need to decode Curve event logs (which differ across templates
            # — NextGen 2-pool uses dynamic-array signatures vs. Plain Pool).
            #
            # Known limitation: when add/remove events fire mid-period, the
            # closed-form prices every change at EoM unit_price, biasing
            # inflow by the cross-event unit_price drift. The exact event-
            # based path was deleted (was dead code; RPC eth_getLogs over a
            # multi-month range exceeds Alchemy's log cap on busy pools).
            # Phase 3+: capture Curve events via Dune and auto-route based on
            # whether the period had any events.
            from ..extract.rpc import balance_of
            from ..normalize.positions import _curve_lp_index_weighted_inflow
            from ..normalize.sources.curve_pool import CurvePoolSource
            from ..domain.primes import Address as _Addr, Chain as _Chain
            curve_src = sources.curve_pool if sources.curve_pool is not None else CurvePoolSource()
            inflow_ts = _curve_lp_index_weighted_inflow(
                prime, venue, som_block, eom_block,
                period_end_date=period.end,
                pool_source=curve_src,
                lp_balance_at=lambda c, t, h, b: balance_of(
                    _Chain(c), _Addr(t), _Addr(h), b,
                ),
            )
        elif venue.pricing_category in (PricingCategory.AAVE_ATOKEN, PricingCategory.SPARKLEND_SPTOKEN):
            # Cat C / D — Aave aToken / SparkLend spToken closed-form inflow
            # via scaledBalanceOf (un-rebased principal). This avoids the
            # face-value-Transfer model's loss of accuracy when burns happen
            # at progressively higher liquidity indices: the simple model
            # under-counts yield by Σ(burn × index_growth_remaining).
            from ..extract.rpc import balance_of, scaled_balance_of
            from ..normalize.positions import (
                _atoken_external_revenue_usd,
                _atoken_index_weighted_inflow,
            )
            from ..domain.primes import Address as _Addr, Chain as _Chain

            # Sentinel "zero address" — used by the mint/burn fixtures as the
            # counterparty for mints (from=0) and burns (to=0).
            _ZERO = b"\x00" * 20
            _balance_src_for_events = (
                sources.balance if sources.balance is not None else get_balance_source()
            )

            def _atoken_event_blocks(
                chain_value: str, token_addr: bytes, holder_addr: bytes,
                som: int, eom: int,
            ) -> list[tuple[int, int]]:
                """Day-resolution ``(pre_block, post_block)`` boundaries for
                the per-event yield path.

                The Aave aToken ``Transfer`` event scan via ``eth_getLogs``
                is unworkable on free RPC tiers (Alchemy caps the window at
                10 blocks — a single Eth-month is 215K blocks). We instead
                derive event days from the daily mint/burn aggregates we
                already capture per Cat C venue and convert each activity
                date ``d`` to:

                * ``pre_block``  = ``block_at_or_before(EOD d-1)`` — the
                  block where the scaled balance is still at its
                  PRE-event-day value. Reading ``balanceOf`` here closes
                  the segment that just ended.
                * ``post_block`` = ``block_at_or_before(EOD d)`` — the
                  block where the scaled balance reflects all of day d's
                  events. Reading ``balanceOf`` here opens the next segment.

                Precision: any yield accrued from the start of event day d
                to the in-day event time lands in the NEXT segment (or
                gets lost when consecutive event days collide and the
                next pre-block coincides with the current post-block).
                Bounded loss: ≈half a day per event. For a $11.6M position
                at ~3% APY that's ≈$500 per event — below the closed-
                form's $20K/mo fallback ceiling that this path replaces.
                """
                from datetime import datetime as _dt, time as _time, timezone as _tz, timedelta as _td
                if som + 1 > eom:
                    return []
                # Collect activity dates from mints (from=ZERO → holder)
                # and burns (holder → ZERO). We only care about WHICH days
                # had activity, not the magnitude.
                dates: set = set()
                for from_addr, to_addr in (
                    (_ZERO, holder_addr), (holder_addr, _ZERO),
                ):
                    df = _balance_src_for_events.directed_inflow_timeseries(
                        chain=chain_value, token=token_addr,
                        from_addr=from_addr, to_addr=to_addr,
                        start=prime.start_date, pin_block=eom,
                    )
                    if df is None or df.empty:
                        continue
                    for _, row in df.iterrows():
                        amt = row.get("daily_inflow", 0) or row.get("cum_inflow", 0)
                        try:
                            amt_f = float(amt)
                        except (TypeError, ValueError):
                            amt_f = 0
                        if amt_f > 0:
                            dates.add(row["block_date"])
                boundaries: list[tuple[int, int, object]] = []
                for d in dates:
                    pre_eod = _dt.combine(d - _td(days=1), _time.max, tzinfo=_tz.utc)
                    post_eod = _dt.combine(d, _time.max, tzinfo=_tz.utc)
                    pre_block = resolver.block_at_or_before(chain_value, pre_eod)
                    post_block = resolver.block_at_or_before(chain_value, post_eod)
                    # post_block must fall within the period to be useful.
                    if not (som < post_block <= eom):
                        continue
                    # pre_block may be < som_block (day d = first day of
                    # period); the helper clamps it to som_block.
                    # Include the date so callers can stamp inflow rows on the
                    # correct day rather than always using period_end_date.
                    boundaries.append((pre_block, post_block, d))
                return sorted(set(boundaries), key=lambda t: t[1])

            # Per-event vs day-resolution boundary lookup. If Sources
            # provides a sub-day-resolution ``atoken_event_blocks``
            # callable AND it returns at least one event for this
            # (token, holder) within the period, prefer it over the
            # day-resolution helper. Sub-day boundaries eliminate
            # intraday/consecutive-event precision loss.
            #
            # Fall back to day-resolution when the per-event lookup
            # returns nothing — typically because an older fixture set
            # captured ``atoken_{vid}_mints``/``burns`` daily aggregates
            # but not ``atoken_{vid}_event_log``. The day-resolution
            # path is still correct for venues with sparse events.
            if sources.atoken_event_blocks is not None:
                _day_res_cb = _atoken_event_blocks   # capture original
                _per_event_cb = sources.atoken_event_blocks
                def _atoken_event_blocks(
                    chain_value: str, token_addr: bytes, holder_addr: bytes,
                    som: int, eom: int,
                ) -> list[tuple[int, int, object]]:
                    blocks = _per_event_cb(chain_value, token_addr, holder_addr, som, eom)
                    if blocks:
                        # Sub-day-resolution boundaries: each event block
                        # gets a (pre=block-1, post=block, date) triple.
                        # block_to_date maps the post_block to a calendar date
                        # so per-event inflow rows land on the right day.
                        # The resolver is indexed up to EoM; event blocks are
                        # filtered to ``som < b <= eom`` so they should be in
                        # range. Defensive: if the resolver doesn't cover a
                        # block (e.g. fixture coverage gap, EoM pin block
                        # beyond the last daily-max-block recorded), fall
                        # back to ``period.end`` and warn — the per-event
                        # date is for time-weighting; treating it as EoM is
                        # at worst a regression to the pre-PR behaviour for
                        # that specific event.
                        triples = []
                        for b in blocks:
                            if not (som < b <= eom):
                                continue
                            try:
                                d = resolver.block_to_date(chain_value, b)
                            except (ValueError, KeyError) as exc:
                                _log.warning(
                                    "atoken_event_blocks: block_to_date failed "
                                    "for chain=%s block=%d (%s: %s); falling "
                                    "back to period.end for this event.",
                                    chain_value, b, type(exc).__name__, exc,
                                )
                                d = period.end
                            triples.append((b - 1, b, d))
                        return triples
                    # No per-event data — defer to the day-resolution
                    # daily-aggregate path.
                    return _day_res_cb(chain_value, token_addr, holder_addr, som, eom)

            def _atoken_daily_blocks(
                chain_value: str, token_addr: bytes, holder_addr: bytes,
                som: int, eom: int,
            ) -> list[tuple[int, date]]:
                """Daily ``(EoD post-block, date)`` boundaries across the
                period — the degenerate-closed-form fallback used by
                ``_atoken_index_weighted_inflow`` when a Cat C venue has no
                captured mint/burn event days AND the whole-period closed-form
                would silently return 0 (mid-period entry or clean exit). The
                dates let the recovery stamp inflows on their real calendar
                days.

                Returns only the post-blocks (``_atoken_daily_capped_yield``
                prepends ``som_block`` itself). We deliberately do NOT resolve a
                per-day ``pre_block``: it was discarded downstream, and
                resolving ``EOD(period.start − 1)`` would hit
                ``period.start − 1 < prime.start_date`` and raise on the Dune
                resolver when settling a prime's genesis month. The day grid
                itself is clamped to ``prime.start_date`` for the same reason:
                mid-month prime starts are the norm (Spark 2024-11-18, OBEX
                2025-11-17), and EoD anchors before the resolver's first
                indexed date raise ValueError.
                """
                from datetime import datetime as _dt, time as _time, timezone as _tz, timedelta as _td
                if som + 1 > eom:
                    return []
                out: list[tuple[int, date]] = []
                seen: set[int] = set()
                d = max(period.start, prime.start_date)
                while d <= period.end:
                    post_eod = _dt.combine(d, _time.max, tzinfo=_tz.utc)
                    post_block = resolver.block_at_or_before(chain_value, post_eod)
                    if som < post_block <= eom and post_block not in seen:
                        seen.add(post_block)
                        out.append((post_block, d))
                    d += _td(days=1)
                return out

            inflow_ts = _atoken_index_weighted_inflow(
                prime, venue, som_block, eom_block,
                period_end_date=period.end,
                balance_at=lambda c, t, h, b: balance_of(
                    _Chain(c), _Addr(t), _Addr(h), b,
                ),
                scaled_balance_at=lambda c, t, h, b: scaled_balance_of(
                    _Chain(c), _Addr(t), _Addr(h), b,
                ),
                transfer_event_blocks=_atoken_event_blocks,
                daily_boundary_blocks=_atoken_daily_blocks,
            )
            # Off-pool aToken rewards (Merkl, Anchorage, …). The closed-form
            # ``yield = scaled(SoM) × Δindex / RAY`` formula above only
            # captures pool-native yield on entering-period principal — any
            # aToken delivered mid-period from an external_alm_sources
            # address gets bucketed as principal injection, not revenue.
            # ``_atoken_external_revenue_usd`` queries Dune for those
            # transfers and returns their USD value (par-stable underlying
            # assumed; helper raises otherwise). Default 0 for venues with
            # no allowlist entries on this chain.
            #
            # Note: ``external_alm_sources`` is also consumed by the Cat A
            # path (``_cat_a_capital_inflow_timeseries``) with different
            # semantics — there it's an allowlist that reclassifies
            # par-stable transfers FROM that address as revenue instead of
            # capital. The two uses are orthogonal: Cat A filters on its
            # venue's stable-token transfers; Cat C/D filters on its
            # venue's aToken transfers. The same allowlist entry can drive
            # both behaviours if the sender genuinely sends both stables
            # and aTokens to the ALM (Merkl today is aTokens-only).
            external_revenue_for_venue = _atoken_external_revenue_usd(
                prime, venue, period,
            )
        elif venue.pricing_category == PricingCategory.ERC4626_VAULT:
            # Cat B — two sub-cases:
            #
            # (a) Sky-yield-bearing 4626 at ALM (e.g. sUSDS POL, S32):
            #     The SSR appreciation flows back to Sky via the borrow-rate
            #     charge on utilized. Prime earns only the 30bps spread
            #     (BR − SSR) per day on the SoM USDS value. We override
            #     actual_revenue with this spread rather than using the MtM
            #     Δvalue (which would count SSR as Prime Revenue).
            #
            # (b) All other Cat B vaults (Morpho, syrupUSDC, …):
            #     Standard MtM approach — share mint/burn × convertToAssets.
            from decimal import Decimal as _Dec
            from ..normalize.positions import _shares_to_usd_inflow_timeseries
            from ..normalize.prices import par_stable_price

            if venue.sky_savings_token:
                # Sub-case (a): yield-bearing token (sUSDS) at ALM.
                # Prime Revenue = 0 (spread reimbursement removed; SSR returns to
                # Sky via the BR charge on utilized as before).

                # On Ethereum the bridged sUSDS is ERC-4626 so ``value_som``
                # and ``value_eom`` from ``get_position_value`` already carry
                # the right pps via ``convertToAssets``. On L2 the sUSDS
                # deployment is a plain ERC-20 with no ``convertToAssets`` —
                # that call reverts and we get 0. Recompute both values via
                # the Ethereum sUSDS pps (same token, same accrual rate) when
                # the prime has a PSM3 configured on this chain (Q-S25 / #75).
                if (
                    venue.chain != Chain.ETHEREUM
                    and venue.chain in prime.psm
                ):
                    from ..normalize.positions import get_position_balance
                    from ..normalize.registry import get_psm3_source
                    # PSM3 ``convertToAssetValue`` always returns 18-decimal
                    # USDS-equivalent regardless of input asset decimals.
                    # Keep this scale next to the divisor to make the
                    # dimensional reasoning obvious for future readers.
                    _USDS_RAW_SCALE = Decimal(10**18)
                    psm3_src = (
                        sources.psm3 if sources.psm3 is not None
                        else get_psm3_source()
                    )

                    def _l2_susds_value(block: int) -> _Dec:
                        bal = get_position_balance(
                            prime, venue, block,
                            source=sources.position_balance,
                        )
                        if bal <= 0:
                            return _Dec(0)
                        pps_raw = psm3_src.susds_pps(venue.chain.value, block)
                        return bal * _Dec(pps_raw) / _Dec(10**18)

                    from ..extract.dune import DuneError as _DuneError
                    from ..extract.rpc import RPCError as _RPCError
                    import requests as _requests
                    try:
                        value_som = _l2_susds_value(som_block)
                        value_eom = _l2_susds_value(eom_block)
                        _log.info(
                            "  Cat B L2 sUSDS (ETH pps) %s on %s: "
                            "value_som=$%s value_eom=$%s",
                            venue.id, venue.chain.value, value_som, value_eom,
                        )
                    except (_RPCError, _DuneError, _requests.HTTPError,
                            _requests.ConnectionError, _requests.Timeout) as _e:
                        if _defer_l2_susds:
                            # No prior ``get_position_value`` was called for
                            # this venue (the proxy reverts on convertToAssets,
                            # so we'd deferred). value_som/value_eom are
                            # literally Decimal("0"); revenue + 30bps credit
                            # will both be wrong.
                            _log.warning(
                                "  Cat B L2 sUSDS pricing FAILED for %s on %s "
                                "(%s) — values remain $0 (defer path); "
                                "settlement data for this venue is unreliable.",
                                venue.id, venue.chain.value, _e,
                            )
                        else:
                            _log.warning(
                                "  Cat B L2 sUSDS pricing failed for %s on %s "
                                "(%s) — keeping get_position_value result; "
                                "30bps credit may be wrong for this period.",
                                venue.id, venue.chain.value, _e,
                            )

                # Prime-revenue attribution for sUSDS (PRD
                # ``docs/PRD_revenue_gross_net_audit.md`` §10):
                #
                # (i) Case 1 — clean POL (S37 Base / S43 Arb / S47 Op / S51
                #     Uni; ``demand_side_spread = False``). The sUSDS is 100%
                #     debt-sourced. Leave ``susds_spread = None`` so the
                #     natural MtM ``(value_eom − value_som) − period_inflow``
                #     books the gross SSR × V appreciation as
                #     prime_agent_revenue. It nets against the SSR × V Sky
                #     already takes via ``BR × utilized −
                #     susds_spread_reimbursement`` → venue PnL ≈ 0 (the true
                #     economic outcome; net mint to ALM = 0).
                #
                # (ii) Case 2 — S32 mixed-source (Ethereum raw sUSDS POL;
                #     ``demand_side_spread = True`` AND
                #     ``deduct_savings_v2_deployed = True``). The ALM's sUSDS
                #     balance mixes debt-sourced slices (true POL +
                #     spUSDT/spPYUSD collateral-backed — their deposits mint
                #     NEW ilk debt via ``vat.frob``, so BR is already charged
                #     on them) with ONE depositor-sourced slice: spUSDC V2
                #     (PSM-routed USDC→USDS, no new ilk debt; its SSR belongs
                #     to spUSDC depositors). The MtM runs on the RAW values +
                #     RAW inflow timeseries (internally consistent — depositor
                #     capital flows net out as inflows), and the depositor
                #     slice's SSR accrual is removed via
                #     ``susds_mtm_adjustment`` (daily-integrated
                #     ``−Σ_d spUSDC_AO_d × ssr_daily_d``, computed below;
                #     AO = ``assetsOutstanding``, the deployed-to-ALM
                #     portion — excludes the vault's USDC withdrawal
                #     buffer, which never enters S32's reading).
                #     Endpoint-only value carve-outs do NOT work here: they
                #     desync ``Δvalue`` from ``period_inflow`` and blow the
                #     revenue up by the depositor net-flow (observed:
                #     ±$80–280M/month artefacts).
                #
                # (iii) Legacy fallback — ``demand_side_spread = True`` with
                #     NO live carve-out: keep ``override = 0``. We can't
                #     isolate the depositor slice, so zeroing avoids
                #     over-crediting the prime by ``SSR × depositor_slice``
                #     (the pre-fix behaviour, understated but safe).
                if venue.demand_side_spread and not venue.deduct_savings_v2_deployed:
                    susds_spread = _Dec("0")
                else:
                    susds_spread = None

                # Case 2 depositor-SSR adjustment: daily-integrated SSR
                # accrual on the spUSDC V2 assetsOutstanding series,
                # negated. See ``_savings_v2_depositor_ssr`` for the
                # day-boundary convention (right-Riemann on EoD-d
                # positions, matching the other daily integrals) and the
                # carry-forward RPC-failure degradation. NOTE the
                # deliberate asymmetry: the depositor liability grows at
                # the VSR (depositor accrual) but we subtract at
                # the SSR — the SSR−VSR margin on depositor principal is
                # Spark's demand-side business margin, excluded from
                # supply-side settlement per the Savings-V2 scope decision
                # (PR #126).
                if venue.deduct_savings_v2_deployed:
                    _sv2_src = (
                        sources.savings_v2_deployed
                        if sources.savings_v2_deployed is not None
                        else get_savings_v2_deployed_source()
                    )
                    _dep_ssr_total = _savings_v2_depositor_ssr(
                        _sv2_src, resolver, ssr, period,
                    )
                    susds_mtm_adjustment = -_dep_ssr_total
                    _log.info(
                        "  Savings V2 depositor-SSR adjustment %s: −$%.0f "
                        "(daily-integrated spUSDC_AO × SSR across %d days)",
                        venue.id, float(_dep_ssr_total), period.n_days,
                    )

                # 30 bps spread — Supply Side PnL path (default).
                # Sky charges full BR on utilized; the orchestrator deducts
                # 30 bps × V_d (daily-integrated) from sky_revenue so the
                # prime's net cost = SSR × V (economic neutrality with the
                # MtM-booked SSR appreciation).
                #
                # Exception — demand_side_spread: True venues (currently S32):
                # The sUSDS here collateralises Spark Savings deposits. Sky
                # still charges full BR on all of utilized (the sUSDS is NOT
                # subtracted, which correctly accounts for the full debt drawn
                # from the ilk). The 30 bps spread reimbursement is handled
                # separately as part of Demand Side Distribution Rewards — it
                # does NOT flow through Supply Side settlement, and the
                # Case-2 MtM attribution does not change that (see the
                # reimbursement gate below). S32's MtM revenue (debt-sourced
                # slice via susds_mtm_adjustment) IS booked since the Case-2
                # fix.
                if venue.demand_side_spread:
                    _log.info(
                        "  %s demand_side_spread=True: 30bps reimbursement "
                        "omitted from sky_revenue (handled via Demand Side "
                        "Distribution Rewards). Full BR applies; MtM revenue "
                        "booked on the debt-sourced slice.",
                        venue.id,
                    )
                # Note: the spread reimbursement is computed below, AFTER
                # ``inflow_ts`` is built, using the daily-position helper
                # ``_susds_cat_b_spread_reimb``. Rule 5 precision parity with
                # ``_psm3_susds_spread`` and ``_aggregate_curve_idle_usds``,
                # both of which already integrate daily.

                # Build the inflow timeseries — used for both ``tw_avg_value``
                # CoF allocation display AND the daily-resolved spread
                # reimbursement (``_susds_cat_b_spread_reimb`` below).
                if venue.chain == Chain.ETHEREUM:
                    # True ERC-4626: convertToAssets works — same infra as sub-case (b).
                    _susds_erc4626_src = (
                        sources.convert_to_assets
                        if sources.convert_to_assets is not None
                        else get_convert_to_assets_source()
                    )
                    _susds_balance_src = (
                        sources.balance
                        if sources.balance is not None
                        else get_balance_source()
                    )
                    _susds_shares = 10 ** venue.token.decimals
                    _susds_scale = _Dec(10 ** venue.underlying.decimals)
                    _susds_par = par_stable_price(venue.underlying)

                    def _susds_price(
                        block,
                        _v=venue, _erc=_susds_erc4626_src,
                        _sh=_susds_shares, _sc=_susds_scale, _par=_susds_par,
                    ):
                        raw = _erc.convert_to_assets(
                            chain=_v.chain.value,
                            vault=_v.token.address.value,
                            shares=_sh, block=block,
                        )
                        return (_Dec(raw) / _sc) * _par

                    inflow_ts = _shares_to_usd_inflow_timeseries(
                        prime, venue, period,
                        balance_source=_susds_balance_src,
                        block_resolver=resolver,
                        price_at_block=_susds_price,
                    )
                elif venue.chain in prime.psm:
                    # L2: plain ERC-20 sUSDS — price via PSM3 pps.
                    # psm3_src is already set in the L2 revaluation block above.
                    from ..normalize.positions import _erc4626_shares_weighted_inflow
                    from ..extract.rpc import balance_of as _bal_of
                    from ..domain.primes import Address as _Addr_b, Chain as _Chain_b

                    def _susds_price(
                        block, _psm=psm3_src, _chain=venue.chain.value,
                    ):
                        pps_raw = _psm.susds_pps(_chain, block)
                        return _Dec(pps_raw) / _Dec(10**18)

                    if venue.chain.value in _DUNE_BLOCK_CHAINS:
                        _susds_balance_src = (
                            sources.balance
                            if sources.balance is not None
                            else get_balance_source()
                        )
                        inflow_ts = _shares_to_usd_inflow_timeseries(
                            prime, venue, period,
                            balance_source=_susds_balance_src,
                            block_resolver=resolver,
                            price_at_block=_susds_price,
                        )
                    else:
                        inflow_ts = _erc4626_shares_weighted_inflow(
                            prime, venue, som_block, eom_block,
                            period_end_date=period.end,
                            balance_at=lambda c, t, h, b: _bal_of(
                                _Chain_b(c), _Addr_b(t), _Addr_b(h), b,
                            ),
                            price_at_block=_susds_price,
                        )
                else:
                    inflow_ts = pd.DataFrame({
                        "block_date": [], "daily_inflow": [], "cum_inflow": [],
                    })

                # Daily-resolved spread reimbursement, computed AFTER
                # ``inflow_ts`` so the daily-position helper can integrate
                # ``Σ_d V_d × spread_daily``. Skipped for demand_side_spread
                # venues (S32) — by design their 30 bps spread is compensated
                # OUT-OF-BAND via Demand Side Distribution Rewards, not
                # through Supply Side settlement. The Case-2 MtM attribution
                # (susds_mtm_adjustment) does NOT change this: enabling the
                # in-band reimbursement here while DSDR also pays it would
                # double-pay Sky's spread refund (~30bps × ~$1B ≈ $250K/mo).
                # Consequence: S32's venue-level supply-side PnL reads
                # ≈ −30bps × V_debt (SSR×V booked, (SSR+30bps)×V charged) —
                # a documented residual squared away by DSDR, not an error.
                if not venue.demand_side_spread:
                    _susds_spread_reimbs[venue.id] = _susds_cat_b_spread_reimb(
                        value_som, inflow_ts, period,
                    )
            else:
                # Sub-case (b): normal Cat B MtM.
                susds_spread = None
                balance_src = sources.balance if sources.balance is not None else get_balance_source()
                erc4626_src = (
                    sources.convert_to_assets if sources.convert_to_assets is not None
                    else get_convert_to_assets_source()
                )
                if venue.underlying is None:
                    raise ValueError(f"Venue {venue.id} (Cat B) requires `underlying`")
                shares_unit = 10 ** venue.token.decimals
                underlying_scale = _Dec(10 ** venue.underlying.decimals)
                par_price = par_stable_price(venue.underlying)

                def _cat_b_price(block, _v=venue, _erc=erc4626_src,
                                 _shares=shares_unit, _scale=underlying_scale,
                                 _par=par_price):
                    raw = _erc.convert_to_assets(
                        chain=_v.chain.value,
                        vault=_v.token.address.value,
                        shares=_shares, block=block,
                    )
                    return (_Dec(raw) / _scale) * _par

                # Chains without Dune event coverage need the closed-form
                # share-weighted helper — public RPCs (Monad notably) cap
                # ``eth_getLogs`` ranges so tight that a year-long Transfer
                # scan isn't viable. The closed-form needs only ``balanceOf``
                # + ``convertToAssets`` at SoM/EoM (same shape as the Cat C
                # rebasing helper). Approximation: mid-period mints/burns
                # priced at ``pps_eom`` — negligible vs slow-moving NAV.
                # Routed through ``sources.position_balance`` so tests can
                # inject deterministic values; falls back to the registry
                # so the closed-form helper (non-Dune chains, e.g. Monad)
                # and the on-chain reconciliation in
                # ``_shares_to_usd_inflow_timeseries`` both have a live
                # ``balance_at`` callable in production runs.
                from ..normalize.registry import (
                    get_position_balance_source as _get_pb_b,
                )
                _pb_src_b = sources.position_balance or _get_pb_b()
                _balance_at = _pb_src_b.balance_at
                if venue.chain.value not in _DUNE_BLOCK_CHAINS:
                    from ..normalize.positions import _erc4626_shares_weighted_inflow
                    inflow_ts = _erc4626_shares_weighted_inflow(
                        prime, venue, som_block, eom_block,
                        period_end_date=period.end,
                        balance_at=_balance_at,
                        price_at_block=_cat_b_price,
                    )
                else:
                    # ``som_block`` + ``balance_at`` enable the EoM
                    # share-balance reconciliation in
                    # ``_shares_to_usd_inflow_timeseries`` — catches Dune
                    # ``tokens.transfers`` indexing gaps at the pin-block
                    # boundary (Grove E23 2026-05-31).
                    inflow_ts = _shares_to_usd_inflow_timeseries(
                        prime, venue, period,
                        balance_source=balance_src,
                        block_resolver=resolver,
                        price_at_block=_cat_b_price,
                        som_block=som_block,
                        balance_at=_balance_at,
                    )
        elif venue.pricing_category == PricingCategory.PAR_STABLE:
            # Cat A — raw par-stable holdings on the ALM. Source-tagged
            # inflow netting with an EXTERNAL allowlist: counterparties in
            # `prime.external_alm_sources[chain]` are off-chain custodian
            # senders (e.g. Anchorage) and pass through to revenue. Every
            # other counterparty (PSM swap leg, venue contract allocation/
            # withdrawal, mint/burn, allocator buffer) is treated as
            # value-preserving capital and netted out. Default empty set →
            # revenue = 0, which is correct for par-stables with no
            # off-chain yield source.
            from ..normalize.positions import (
                _cat_a_all_capital_inflow_timeseries,
                _cat_a_capital_inflow_timeseries,
            )
            balance_src = sources.balance if sources.balance is not None else get_balance_source()
            external = {
                addr.value
                for addr in prime.external_alm_sources.get(venue.chain, [])
            }
            # Map override list keyed by raw 20-byte address (matches the
            if venue.force_capital_inflow:
                # Revenue = $0 by construction — chains without reliable
                # transfer-event coverage (e.g. Monad): can't distinguish
                # capital from yield OR date the flows, so declare no yield
                # and synthesise the whole Δvalue as a single period-start
                # inflow (the only defensible dating with no transfer data).
                _delta = value_eom - value_som
                _log.info(
                    "  [%s] Cat A revenue=$0 (force_capital_inflow) — "
                    "synthesising inflow $%.2f (value_som=$%.2f, "
                    "value_eom=$%.2f)",
                    venue.id, float(_delta), float(value_som), float(value_eom),
                )
                inflow_ts = pd.DataFrame({
                    "block_date": [period.start],
                    "daily_inflow": [_delta],
                    "cum_inflow": [_delta],
                })
            elif not venue.external_yield_source:
                # Revenue = $0 by construction (the default) — a par-stable
                # idle holding with NO declared off-chain yield source earns
                # nothing by itself, so its entire Δvalue is capital. This is
                # what turns Spark S27's −$194,444 transfer-capture residual
                # and Grove E13/E31/E32's uncaptured-movement phantom into a
                # clean $0 (2026-07 reconciliation §8 item 5), without
                # running the counterparty classifier.
                # Unlike ``force_capital_inflow``, transfer data EXISTS here,
                # so capital keeps its real dates (a single period-start lump
                # would re-time mid-month flows to day 1 and misstate CoF
                # time-weighting on late-month deposits); the ``target_delta``
                # residual row still guarantees revenue = $0 even if the
                # transfer scan is imperfect. Venues that DO earn external
                # yield (Spark S26 Anchorage, S28 PYUSD) set
                # external_yield_source: true and fall through to the
                # classifier below.
                _delta = value_eom - value_som
                _log.info(
                    "  [%s] Cat A revenue=$0 (no external_yield_source) — "
                    "dated all-capital series, Δvalue $%.2f",
                    venue.id, float(_delta),
                )
                inflow_ts = _cat_a_all_capital_inflow_timeseries(
                    prime, venue, period,
                    balance_source=balance_src,
                    target_delta=_delta,
                )
            else:
                # ``_to_bytes`` normalisation inside the helper).
                overrides_for_chain = prime.principal_return_overrides.get(venue.chain, {})
                overrides_by_bytes = {
                    addr.value: [(o.date, o.amount) for o in entries]
                    for addr, entries in overrides_for_chain.items()
                }
                # Yield-reversal overrides — the outflow mirror (ALM →
                # external source returning over-received yield, e.g. the
                # 2026-05-19 $5M Spark→Anchorage reimbursement).
                reversals_for_chain = prime.yield_reversal_overrides.get(venue.chain, {})
                reversals_by_bytes = {
                    addr.value: [(o.date, o.amount) for o in entries]
                    for addr, entries in reversals_for_chain.items()
                }
                # Paired-principal-cap auto-wiring — extracted into
                # ``_build_paired_principal_caps`` for unit-test coverage.
                paired_principal_caps = _build_paired_principal_caps(
                    prime, venue, period, balance_src,
                )
                inflow_ts = _cat_a_capital_inflow_timeseries(
                    prime, venue, period,
                    balance_source=balance_src,
                    external_sources=external,
                    principal_return_overrides=overrides_by_bytes,
                    yield_reversal_overrides=reversals_by_bytes or None,
                    paired_principal_caps=paired_principal_caps or None,
                )
        elif venue.pricing_category == PricingCategory.EOA:
            # Cat EOA — Off-protocol relay/staging address. The venue tracks
            # outstanding ALM principal that's sitting at an EOA waiting for
            # the return leg to land at the paired anchor venue. There is no
            # native yield mechanism: every balance change is either fresh
            # principal-out (ALM → holder) or a drain triggered by the paired
            # anchor receiving a return (paired_source → ALM). Both are
            # value-preserving capital movement, so we set ``inflow_ts =
            # Δvalue`` and ``revenue = Δvalue − inflow`` collapses to 0 every
            # period — Cat EOA never contributes to Prime Revenue.
            #
            # ⚠ Important: the economic "spread" (e.g. $50M USDC out →
            # $50.12M anchor-asset back, where the +$120k is a mint/swap
            # advantage captured during the OOB acquisition) will NOT appear
            # in Prime Revenue. It persists as this venue's terminal negative
            # balance (e.g. −$120k) once the drain exceeds the principal-out,
            # and that residual is what makes the PRD §5.2 cost-basis
            # invariant balance (Σ venue values = cum_debt − allocator returns).
            # Booking the spread as venue revenue here would either
            # double-count the anchor's downstream MtM (if the anchor is a
            # raw stable that itself feeds a Cat B vault — e.g. E14 → E6) or
            # mis-attribute capital flow as yield. Surfacing the spread as
            # Prime Revenue requires a separate accounting layer; deferred.
            import pandas as _pd
            inflow_ts = _pd.DataFrame([{
                "block_date": period.end,
                "daily_inflow": value_eom - value_som,
                "cum_inflow": value_eom - value_som,
            }])
        elif venue.pricing_category == PricingCategory.RWA_TRANCHE:
            from ..normalize.positions import (
                _rwa_inflow_timeseries,
                _erc4626_event_inflow_timeseries,
            )
            balance_src = sources.balance if sources.balance is not None else get_balance_source()

            if venue.centrifuge_vault is not None:
                _ev_ts = _erc4626_event_inflow_timeseries(   # noqa: F841 (used below)
                    prime, venue, period,
                    block_resolver=resolver,
                )

                # Extract vault-event period inflow (exact USDC from events).
                _period_mask = _ev_ts["block_date"].apply(
                    lambda d: period.start <= d <= period.end
                )
                _erc4626_period_inflow = Decimal(str(
                    _ev_ts.loc[_period_mask, "daily_inflow"].sum()
                ))

                # ── Share-balance sanity check ────────────────────────────
                # Verify that the cumulative share flow from events reconciles
                # with the on-chain share balance.  A mismatch means there are
                # share movements not captured as Deposit/Withdraw (e.g. direct
                # ERC-20 transfers) and the event-sourced flows are incomplete.
                if (
                    not _ev_ts.empty
                    and "daily_net_shares_raw" in _ev_ts.columns
                ):
                    _holder_addr = (
                        venue.holder_override or prime.alm[venue.chain]
                    )
                    try:
                        from ..extract.rpc import balance_of as _rpc_bal
                        _som_shares_raw = Decimal(str(
                            _rpc_bal(
                                venue.chain,
                                venue.token.address,
                                _holder_addr,
                                som_block,
                            )
                        ))
                        _eom_shares_raw = Decimal(str(
                            _rpc_bal(
                                venue.chain,
                                venue.token.address,
                                _holder_addr,
                                eom_block,
                            )
                        ))
                        _period_net_shares = Decimal(str(
                            _ev_ts.loc[_period_mask, "daily_net_shares_raw"].sum()
                        ))
                        _expected_eom = _som_shares_raw + _period_net_shares

                        if _eom_shares_raw != 0:
                            _share_drift_pct = abs(
                                (_expected_eom - _eom_shares_raw) / _eom_shares_raw
                            ) * 100
                            if _share_drift_pct > Decimal("0.5"):
                                _log.warning(
                                    "  Cat E ERC-4626 %s: share-balance drift "
                                    "%.4f%% — events may not capture all share "
                                    "movements (expected EOM shares from events: "
                                    "%s, on-chain: %s). Inflow figures may be "
                                    "incomplete.",
                                    venue.id, float(_share_drift_pct),
                                    _expected_eom, _eom_shares_raw,
                                )
                            else:
                                _log.debug(
                                    "  Cat E ERC-4626 %s: share balance OK "
                                    "(drift %.4f%%)",
                                    venue.id, float(_share_drift_pct),
                                )

                        _implied_yield = value_eom - value_som - _erc4626_period_inflow
                        _log.info(
                            "  Cat E ERC-4626 %s: SOM=$%s  EOM=$%s  "
                            "net_capital=$%s  implied_yield=$%s",
                            venue.id,
                            f"{float(value_som):,.2f}",
                            f"{float(value_eom):,.2f}",
                            f"{float(_erc4626_period_inflow):,.2f}",
                            f"{float(_implied_yield):,.2f}",
                        )
                    except Exception as _e:
                        _log.warning(
                            "  Cat E ERC-4626 %s: share sanity check failed"
                            " (%s) — skipping.",
                            venue.id, _e,
                        )

            # All Cat E venues: token-transfer inflow_ts for the
            # ``actual_revenue = (value_eom - value_som) - period_inflow``
            # formula. The SDE sd_share is EoM-locked (see
            # ``_capped_sd_revenue_eom_locked``) and does not depend on
            # this timeseries; it only feeds revenue accounting.
            def _cat_e_nav(block, _v=venue, _br=resolver, _nr=sources.nav_oracle_resolver):
                return _resolve_rwa_nav(_v, block, block_resolver=_br, resolver=_nr)

            inflow_ts = _rwa_inflow_timeseries(
                prime, venue, period,
                balance_source=balance_src,
                block_resolver=resolver,
                nav_at_block=_cat_e_nav,
            )
        elif venue.underlying is None:
            import pandas as _pd
            inflow_ts = _pd.DataFrame({
                "block_date": [], "daily_inflow": [], "cum_inflow": [],
            })
        else:
            inflow_ts = get_venue_inflow_timeseries(
                prime, venue.chain, venue.underlying, venue.token.address, period,
                source=sources.balance,
            )

        # SDE classification — already resolved above as _early_sde (before the
        # sky_only early-exit). Reuse it here to avoid a second table lookup.
        sde_entry = _early_sde
        # Daily SDE asset-value timeseries — feeds ``utilized`` exclusion
        # in ``compute_sky_revenue`` (Step 2) AND, for capped SDE, the
        # daily-resolved sd_share computation in
        # ``compute_venue_revenue`` (``_capped_sd_revenue_daily_resolved``).
        # When this timeseries is None the venue-revenue split falls back
        # to the EoM-locked snapshot (legacy path).
        _sde_ts: pd.DataFrame | None = None
        if sde_entry is not None:
            ciuc = venue.curve_idle_usds
            if venue.lp_kind == "uniswap_v4" and ciuc is not None and ciuc.sde_coin is not None:
                # Uniswap V4 LP pool SDE: the exposure is a par-stable coin
                # (USDT or PYUSD) leg of the v4 position. Decompose the
                # position's amounts rather than reading pool reserves.
                _sde_ts = _univ4_sde_asset_value_timeseries(
                    prime, venue, period,
                    v4_source=sources.v4_position,
                    block_resolver=resolver,
                    cap_usd=sde_entry.cap_usd,
                )
                sde_asset_value_per_venue.append((venue.id, _sde_ts))
            elif ciuc is not None and ciuc.sde_coin is not None:
                # Curve LP pool SDE: the exposure is a par-stable coin in the pool
                # (e.g. USDT in sUSDS/USDT). Use pool-state coin balance rather than
                # an RWA NAV oracle. See CurveIdleUsdsConfig.sde_coin for details.
                from ..normalize.sources.curve_pool import CurvePoolSource as _CPS
                _curve_src = (
                    sources.curve_pool
                    if sources.curve_pool is not None
                    else _CPS()
                )
                _sde_ts = _curve_sde_asset_value_timeseries(
                    prime, venue, period,
                    sde_coin=ciuc.sde_coin,
                    curve_pool_source=_curve_src,
                    block_resolver=resolver,
                    cap_usd=sde_entry.cap_usd,
                )
                sde_asset_value_per_venue.append((venue.id, _sde_ts))
            else:
                # Cat E (RWA tranche) path — requires nav_oracle on the venue.
                bsrc = sources.balance if sources.balance is not None else get_balance_source()

                def _sd_nav(block, _v=venue, _br=resolver, _nr=sources.nav_oracle_resolver):
                    return _resolve_rwa_nav(_v, block, block_resolver=_br, resolver=_nr)

                # Daily SDE asset-value series — feeds TWO downstream paths:
                #   1. ``compute_sky_revenue`` via ``sde_av_total``: daily
                #      utilized exclusion (Sky's cap-protected slice is
                #      removed from the BR×Net_Subs base).
                #   2. ``compute_venue_revenue`` via ``VenueRevenueInputs.
                #      value_timeseries``: drives the daily-resolved capped
                #      sd_share (``_capped_sd_revenue_daily_resolved``).
                #
                # **Gating semantics.** The daily gate (pre-start / post-burn
                # / post-end → cum_value=0) controls both paths uniformly:
                # inactive days contribute zero utilized exclusion AND zero
                # to the Σ cum_value in the sd_share numerator. Burn-day
                # special handling (cap-coverage preserved through
                # ``usdc_settlement_date``) is also applied here so both
                # paths see the same daily values. The daily-resolved
                # function in compute_venue_revenue applies an additional
                # burn-day override (sd_share = 1.0) when the burn falls
                # inside the period and value_eom < cap — this matches
                # Grove's burn-month attribution (Sky absorbs full P&L)
                # which the raw Σ ratio under-attributes.
                #
                # The in-flight upper bound is ``usdc_settlement_date`` when
                # set (real USDC arrival at ALM, e.g. Grove E8 2026-03-11);
                # ``end_date`` is the Atlas-record / deal-end date (Mar 12
                # for E8). The two diverge by ~1-2 days when the USDC lands
                # before the Atlas record posts — see the SDE YAML comments.
                _sde_ts = _sde_asset_value_timeseries(
                    prime, venue, period,
                    balance_source=bsrc,
                    block_resolver=resolver,
                    nav_at_block=_sd_nav,
                    cap_usd=sde_entry.cap_usd,
                    start_date=sde_entry.start_date,
                    burn_date=sde_entry.burn_date,
                    usdc_settlement_date=sde_entry.usdc_settlement_date,
                    end_date=sde_entry.end_date,
                )
                # Safeguard: the SDE timeseries reads
                # ``cumulative_balance_timeseries`` (Dune transfers, filtered
                # by ``venue.min_transfer_amount_usd`` AND by capture-script
                # start_date). If either filter is mis-set the resulting
                # cum_balance can be missing the venue's pre-filter holdings,
                # silently understating the SDE deduction and inflating
                # ``sky_revenue``. This bit Grove April 2026 (~$1.7M inflated
                # sky_revenue from a capture-script start_date typo of
                # 2025-10-23 vs the correct 2025-05-14, dropping ~$520M of
                # pre-Oct-23 BUIDL+JTRSY balance from cum_balance).
                #
                # Compare cum_balance at the SoM pin block date (= the day
                # BEFORE period.start) with ``value_som`` (the authoritative
                # balanceOf-based read at the SoM pin block). Reading at
                # ``period.start - 1`` excludes any same-day events on
                # period.start (e.g. a large redemption fired at the
                # Centrifuge mint window on May 1) which would otherwise
                # produce a benign false positive — the post-fix Grove May
                # 2026 case is the motivating example: JTRSY had a $50M
                # outflow on May 1 ~08:30 UTC, so the period.start EOD
                # cum_balance was legitimately $50M LOWER than value_som.
                # Anchoring the check at the SoM date removes that source
                # of noise. Only warn when the SoM-anchored cum_balance is
                # LOWER than value_som by >$1M — that's the bug direction
                # (missing pre-period balance).
                if not _sde_ts.empty and value_som > 0:
                    from datetime import timedelta as _td
                    # Re-read cum_balance from the same source the SDE
                    # timeseries used (cached at this point — no extra Dune
                    # call). Anchor the read at the day BEFORE period.start
                    # so it matches value_som's time point (= SoM pin block
                    # = prior month's EOD).
                    _sde_holder = venue.holder_override or prime.alm[venue.chain]
                    _bal_df = bsrc.cumulative_balance_timeseries(
                        chain=venue.chain.value,
                        token=venue.token.address.value,
                        holder=_sde_holder.value,
                        start=prime.start_date,
                        pin_block=period.pin_blocks[venue.chain],
                        min_transfer_amount=Decimal(0),
                    )
                    _som_date = period.start - _td(days=1)
                    _som_bal = cum_at_or_before(_bal_df, "cum_balance", _som_date)
                    if _som_bal > 0:
                        _som_block = resolver.block_at_or_before(
                            venue.chain.value,
                            datetime.combine(_som_date, time.max, tzinfo=timezone.utc),
                        )
                        _sde_som = _som_bal * _sd_nav(_som_block)
                    else:
                        _sde_som = Decimal(0)
                    _delta = value_som - _sde_som   # signed: positive = SDE understates
                    if _delta > Decimal("1_000_000"):
                        _log.warning(
                            "  [%s] SDE timeseries SoM-anchored cum_balance "
                            "(%s → $%.0f) UNDERSTATES value_som ($%.0f) by "
                            "$%.0f — likely a cumulative_balance_timeseries "
                            "/ capture-script issue (yield-mint filter or "
                            "wrong start_date). Will UNDERSTATE the SDE "
                            "deduction from utilized and OVERSTATE "
                            "sky_revenue. Re-capture the fixture with "
                            "start_date = prime.start_date AND pass "
                            "min_transfer_amount=0 to "
                            "cumulative_balance_timeseries for SDE venues.",
                            venue.id, _som_date.isoformat(), float(_sde_som),
                            float(value_som), float(_delta),
                        )
                sde_asset_value_per_venue.append((venue.id, _sde_ts))

        # Case 3b (PRD §10): re-attribute the sUSDS-leg SSR appreciation
        # embedded in the Curve LP MtM (recursive ``convertToAssets`` in
        # ``_curve_lp_unit_price``). For SDE venues (S24, sd_share ≈ 100%)
        # the redirect would hand it to Sky a SECOND time on top of Sky's
        # BR − 30bps = SSR collection. Remove it from the SDE-eligible
        # actual_revenue and re-book 100% to the prime as
        # ``external_revenue`` (the SDE deal covers the USDT-leg exposure;
        # the sUSDS-leg SSR is outside its terms). Net effect: prime books
        # +SSR×V_susds (nets to ~0 against Sky's BR − 30bps machinery) and
        # ``sde_revenue`` / ``sky_revenue`` drop by the former
        # double-collect.
        _curve_c3 = curve_susds_ssr_by_venue.get(venue.id)
        if _curve_c3:
            susds_mtm_adjustment -= _curve_c3
            external_revenue_for_venue += _curve_c3
            _log.info(
                "  Curve sUSDS-leg SSR re-attribution %s: $%.0f moved from "
                "SDE-eligible actual_revenue to prime external_revenue",
                venue.id, float(_curve_c3),
            )

        _log.info(
            "  [%d/%d] %s  done in %.1fs  som=$%.0f  eom=$%.0f%s",
            _venue_idx, n_venues, venue.id,
            _time.monotonic() - _venue_t0,
            value_som, value_eom,
            "  [SDE]" if sde_entry is not None else "",
        )
        venue_inputs.append(VenueRevenueInputs(
            venue=venue, value_som=value_som, value_eom=value_eom,
            inflow_timeseries=inflow_ts,
            sde_entry=sde_entry,
            actual_revenue_override=susds_spread,
            external_revenue=external_revenue_for_venue,
            erc4626_period_inflow=_erc4626_period_inflow,
            value_timeseries=_sde_ts,
            actual_revenue_adjustment=susds_mtm_adjustment,
        ))

    # Re-sort venue_inputs to match the declaration order in prime.venues so
    # the per-venue breakdown is always printed in config order, regardless of
    # which pass (cash-dist pre-pass or main loop) populated each entry.
    _venue_order = {v.id: i for i, v in enumerate(prime.venues)}
    venue_inputs.sort(key=lambda vi: _venue_order.get(vi.venue.id, 9999))

    _log.info("step 4: computing revenue components...")
    # 4. Compute revenue components.
    if sky_only:
        # subproxy balances were not fetched — agent_rate is meaningless.
        # venue_inputs contains only SDE venues (non-SDE skipped in step 3),
        # so prime-side revenue is small / usually zero and we set it to 0.
        _, breakdown = compute_prime_agent_revenue(period, venue_inputs)
        agent_rate = Decimal("0")
        prime_rev = Decimal("0")
    else:
        agent_rate = compute_agent_rate(
            period, sub_usds, sub_susds, ssr,
            start_date=prime.agent_rate_start_date,
        )
        prime_rev, breakdown = compute_prime_agent_revenue(period, venue_inputs)
        # Case 3a (PRD §10): book the SSR appreciation on the PSM3 sUSDS
        # slice as Prime Revenue. PSM3 is not a venue (no per-venue row,
        # no SDE interaction), so this is a prime-level addition. It nets
        # against Sky's existing BR − 30bps = SSR collection on the same
        # slice → the composite contributes ~0 to the prime's profit,
        # matching the physical reality (the appreciation accrues inside
        # the prime's PSM3 share value).
        # NB: applies to EVERY non-sky_only prime — PR #166 accidentally
        # moved this into the chronicle_points (Grove-only) block below,
        # which would have silently dropped it on the next Spark
        # regeneration (July 2026: $111,594.67).
        prime_rev += psm3_susds_appreciation
        # Per ``d255ed2`` (Rule 5 consistency): the 30 bps spread on sUSDS held
        # in Curve LP pools and PSM3 is treated as a Sky Revenue REDUCTION (not
        # a Prime Revenue credit), matching the treatment of sky_savings_token
        # Cat B ALM venues. ``curve_susds_spread`` and ``psm3_susds_spread`` are
        # folded into ``total_susds_spread_reimb`` below instead of being added
        # to ``prime_rev``.

    # Chronicle Points (Grove only today) — Demand-Side revenue component
    # per the soterlabs/chronicle-points-dune-dash methodology: 20% of
    # (SSR + 30bps additive) on the Chronicle Farm's daily USDS balance.
    # The farm balance is fetched via a DIRECT HyperSyncBalanceSource — not
    # ``sources.balance`` — so fixture-driven runs (whose balance source is
    # a per-venue mock that knows nothing about the farm) still compute it
    # from live HyperSync data, matching how S2 hypersync reads work.
    chronicle_points = Decimal("0")
    if (
        not sky_only
        and prime.chronicle_points is not None
        and prime.chronicle_points.enabled
    ):
        from ..normalize.sources.hypersync_balances import HyperSyncBalanceSource
        from .chronicle_points import compute_chronicle_points
        # Balance scan starts at USDS genesis, NOT the program start: the
        # farm has held USDS since 2024-09-17, and a scan starting at the
        # program's 2025-07-24 accrual start misses the pre-existing ~24M
        # (the cum series even goes negative). Verified: full-history
        # reconstruction == RPC balanceOf to the wei at Feb-28/Jul-31 2026
        # EoD blocks. Only the ACCRUAL window is the settlement period.
        _farm_usds = HyperSyncBalanceSource().cumulative_balance_timeseries(
            chain=Chain.ETHEREUM.value,
            token=USDS_ETHEREUM.address.value,
            holder=prime.chronicle_points.farm.value,
            start=date(2024, 9, 1),
            pin_block=period.pin_blocks[Chain.ETHEREUM],
        )
        chronicle_points = compute_chronicle_points(period, _farm_usds, ssr)
        _log.info(
            "chronicle_points: %s earns $%.2f for %s (farm 0x%s)",
            prime.id, float(chronicle_points),
            f"{month.year}-{month.month:02d}",
            prime.chronicle_points.farm.value.hex(),
        )

    # Governance Accessibility Rewards (Skybase only today) — Demand-Side
    # component: ``GarConfig.share`` (1%) × the month's consolidated Sky
    # Net Revenue, read from the sky_total artifact (whose SNR definition
    # matches BA's "Net revenue" line). Fails loud when the artifact is
    # missing — run scripts/build_sky_total_2026.py BEFORE the GAR prime's
    # monthly report. Gated on ``from_month``: earlier cycles' GAR was
    # settled externally (MSC#9 backlog true-up + monthly lines) and must
    # not be restated into the reports.
    gar = Decimal("0")
    _month_label = f"{month.year}-{month.month:02d}"
    if (
        not sky_only
        and prime.gar is not None
        and _month_label >= prime.gar.from_month
    ):
        import json as _json
        from pathlib import Path as _Path
        _snr_prov = (
            _Path(__file__).resolve().parents[3]
            / "settlements" / "sky_total" / _month_label / "provenance.json"
        )
        if not _snr_prov.exists():
            raise FileNotFoundError(
                f"gar: {prime.id} {_month_label} needs "
                f"settlements/sky_total/{_month_label}/provenance.json — "
                "run scripts/build_sky_total_2026.py first "
                "(GAR = share × Sky Net Revenue)"
            )
        _snr = Decimal(
            _json.loads(_snr_prov.read_text())["results"]["sky_net_revenue"]
        )
        gar = prime.gar.share * _snr
        _log.info(
            "gar: %s earns $%.2f (= %s × SNR $%.2f) for %s",
            prime.id, float(gar), prime.gar.share, float(_snr), _month_label,
        )
    # Annotate each venue's VenueRevenue with (a) its lending-idle tw_avg —
    # post-hoc report scripts deduct it from avg_value for CoF allocation
    # since the idle portion is already subtracted from utilized — and
    # (b) its susds_spread_reimbursement (30bps × value_som × n_days for
    # sky_savings_token Cat B venues; zero otherwise). Both go through the
    # same dataclasses.replace pass to avoid re-iterating.
    import dataclasses as _dc
    breakdown = [
        _dc.replace(
            vr,
            lending_idle_tw_avg_usd=_lending_idle_tw_avg.get(vr.venue_id, vr.lending_idle_tw_avg_usd),
            susds_spread_reimbursement=_susds_spread_reimbs.get(vr.venue_id, vr.susds_spread_reimbursement),
        )
        for vr in breakdown
    ]
    # Fold Curve LP and PSM3 sUSDS spreads into the total sky-revenue reduction
    # alongside the Cat B per-venue values. All three paths (Cat B ALM + Curve
    # LP + PSM3) are treated identically: Sky charges full BR on the underlying
    # utilized, then refunds 30 bps as a sky_revenue reduction — net economic
    # cost to the prime is SSR × V (Rule 5 consistency, per d255ed2).
    total_susds_spread_reimb = (
        sum(_susds_spread_reimbs.values(), Decimal("0"))
        + curve_susds_spread
        + psm3_susds_spread
    )

    # SDE revenue (Σ actual × sd_share across venues) flows directly to Sky.
    sde_revenue = sum((vr.sd_revenue for vr in breakdown), Decimal("0"))

    # Aggregate per-venue daily SDE asset-value into one frame so
    # compute_sky_revenue_daily can subtract it from utilized (SDE positions
    # already pay Sky directly via sde_revenue; charging BR would double-bill).
    if sde_asset_value_per_venue:
        sde_av_total = (
            pd.concat([df for _, df in sde_asset_value_per_venue])
              .groupby("block_date", as_index=False)["cum_value"].sum()
              .sort_values("block_date").reset_index(drop=True)
        )
    else:
        sde_av_total = None

    # Subsidised borrowing rate (debt-rate-methodology Step 1.b). When
    # ``prime.subsidy.enabled`` is False this collapses to full-BR and
    # ``ref_rate_history`` is never read.
    ref_rate_history = (
        load_reference_rates_for(prime.subsidy)
        if prime.subsidy.enabled else None
    )
    sky_rev_br, sky_rev_daily, subsidy_summary = compute_sky_revenue_daily(
        period, debt, alm_usds, ssr, psm_usds=psm_usds,
        subsidy_config=prime.subsidy,
        ref_rate_history=ref_rate_history,
        sde_asset_value=sde_av_total,
        curve_idle_usds=curve_idle_usds,
        lending_idle_usds=lending_idle_usds,
    )
    # Sky's full claim: BR on (utilized − SDE − idle deductions) + actual SDE
    # revenue, minus the Sky-to-Spark transfer on sUSDS positions:
    #   * ``total_susds_spread_reimb`` — 30bps spread reimbursement across
    #     Cat B ALM + Curve LP sUSDS + PSM3 sUSDS leg. The reimbursement
    #     neutralises the SSR-via-share-price + BR-on-utilized composite
    #     on sUSDS positions: Sky charges full BR then refunds 30bps, so
    #     the prime's net cost is SSR × V (Rule 5 economic neutrality).
    sky_rev = sky_rev_br + sde_revenue - total_susds_spread_reimb
    # Pure BR × cum_debt (no idle / SDE / PSM / Curve / lending deductions,
    # no susds spread reimbursement). Display-only. NOT the gross analog of
    # sky_revenue: ``sky_revenue`` also adds ``sde_revenue`` on top of the
    # BR-on-utilized base and subtracts the spread reimbursement, so for
    # primes with active SDE positions ``sky_revenue`` can exceed
    # ``sky_revenue_gross``. The monthly report consumes this as
    # ``sky_revenue_gross − cof_total`` to display "BR reduction from
    # idle/SDE deductions". See ``MonthlyPnL.sky_revenue_gross`` docstring.
    sky_rev_gross = Decimal(str(sky_rev_daily["daily_sky_rev_gross"].sum()))

    _log_sky_revenue_debug(
        sky_rev_daily,
        sde_asset_value_per_venue,
        breakdown,
        sky_rev_br,
        sde_revenue,
        susds_spread_reimb=total_susds_spread_reimb,
    )
    # Legacy field — always 0 under the SDE-split model (Sky takes actual
    # revenue, not floored).
    sky_direct_shortfall = Decimal("0")

    # ``monthly_pnl`` is an audit-only invariant (kept for provenance round-trip,
    # not displayed in the markdown headline or pnl.csv). The ``__post_init__``
    # in ``MonthlyPnL`` checks the same expression — this is just the canonical
    # value to store. Sky Direct shortfall is already netted into ``sky_rev``
    # above, so it doesn't appear here separately.
    # Per-venue daily SDE breakdown for post-hoc reporters (xlsx "SDE daily"
    # tab — Sky / Grove / in-flight decomposition). Empty when no SDE venues
    # are active this period.
    from ..domain.monthly_pnl import SDEDailyBreakdown as _SDEDailyBreakdown
    _venues_by_id = {v.id: v for v in prime.venues}
    sde_daily_breakdown_out: list[_SDEDailyBreakdown] = []
    for _vid, _df in sde_asset_value_per_venue:
        _entry = sde_table.overlaps_venue(prime.id, _vid, period.start, period.end)
        _venue = _venues_by_id.get(_vid)
        if _entry is None or _venue is None:
            continue
        _daily = [
            {
                "block_date": _row["block_date"],
                "cum_value": _row["cum_value"],
                "uncapped_value": _row["uncapped_value"],
            }
            for _, _row in _df.iterrows()
        ]
        sde_daily_breakdown_out.append(_SDEDailyBreakdown(
            venue_id=_vid,
            label=_venue.label,
            cap_usd=_entry.cap_usd,
            burn_date=_entry.burn_date,
            usdc_settlement_date=_entry.usdc_settlement_date,
            end_date=_entry.end_date,
            daily=_daily,
        ))

    # Serialize the day-by-day sky-revenue breakdown into plain dicts (the
    # MonthlyPnL field accepts ``list[dict]`` so the wire-format works without
    # introducing a new dataclass). Decimals are stringified for
    # ``provenance.json`` round-trip; APYs stay as native floats / None.
    sky_revenue_daily_out: list[dict] = [
        {
            "date":                row["date"].isoformat(),
            "cum_debt":            str(row["cum_debt"]),
            "alm_usds":            str(row["alm_usds"]),
            "psm_usds":            str(row["psm_usds"]),
            "sde_av":              str(row["sde_av"]),
            "curve_idle":          str(row["curve_idle"]),
            "lending_idle":        str(row["lending_idle"]),
            "utilized":            str(row["utilized"]),
            "ssr_apy":             row["ssr_apy"],
            "base_apy":            row["base_apy"],
            "ref_rate_apy":        row["ref_rate_apy"],
            "sub_apy":             row["sub_apy"],
            "t_months":            row["t_months"],
            "daily_sky_rev":       str(row["daily_sky_rev"]),
            "daily_sky_rev_gross": str(row["daily_sky_rev_gross"]),
        }
        for _, row in sky_rev_daily.iterrows()
    ]

    return MonthlyPnL(
        prime_id=prime.id,
        month=month,
        period=period,
        sky_revenue=sky_rev,
        agent_rate=agent_rate,
        prime_agent_revenue=prime_rev,
        chronicle_points=chronicle_points,
        gar=gar,
        monthly_pnl=prime_rev + agent_rate + chronicle_points + gar - sky_rev,
        venue_breakdown=breakdown,
        pin_blocks_som=pin_blocks_som,
        sky_direct_shortfall=sky_direct_shortfall,
        sde_revenue=sde_revenue,
        curve_susds_spread=curve_susds_spread if not sky_only else Decimal("0"),
        psm3_susds_spread=psm3_susds_spread if not sky_only else Decimal("0"),
        psm3_susds_appreciation=(
            psm3_susds_appreciation if not sky_only else Decimal("0")
        ),
        display_only_breakdown=display_only_breakdown,
        sde_daily_breakdown=sde_daily_breakdown_out,
        sky_revenue_daily=sky_revenue_daily_out,
        susds_spread_reimbursement=total_susds_spread_reimb,
        sky_revenue_gross=sky_rev_gross,
        subsidy_summary=subsidy_summary,
    )
