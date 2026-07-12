"""YAML config loader. Reads `config/<prime>.yaml` and produces a `Prime` value object."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from .pricing import PricingCategory
from .primes import (
    Address,
    CashDistributionSource,
    Chain,
    CurveIdleUsdsConfig,
    NavOracle,
    NotionalScheduleEntry,
    Prime,
    PrincipalReturnOverride,
    PsmConfig,
    PsmKind,
    Token,
    UniV4PoolKey,
    Venue,
)
from .subsidy import SubsidyConfig


def _parse_ilk_bytes32(s: str) -> bytes:
    s = s.lower().removeprefix("0x")
    if len(s) != 64:
        raise ValueError(f"ilk_bytes32 must be 64 hex chars; got {len(s)} ({s!r})")
    return bytes.fromhex(s)


def _parse_usd_amount(raw, field_name: str) -> Decimal:
    """Parse a YAML scalar as a whole-dollar USD amount.

    Accepts only ``int`` (YAML ``50000000`` → Python int). Rejects:
    * ``bool`` (subclass of int — YAML ``true``/``false`` would silently
      become Decimal(1)/(0))
    * ``float`` — YAML ``50000000.0`` parses as Python float;
      ``Decimal(str(float))`` is exact for integer-valued floats but
      introduces representation noise for fractional values (e.g.
      ``Decimal(str(50123456.78))`` has trailing rep digits). For
      whole-dollar amounts an int is unambiguous; for fractional amounts
      the operator should use a quoted string and a richer schema.

    ``field_name`` appears in the error message so the operator knows
    which YAML key produced the rejection.
    """
    if isinstance(raw, bool):
        raise ValueError(
            f"{field_name}: bool is not a valid USD amount (got {raw!r})"
        )
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, float):
        raise ValueError(
            f"{field_name}: float values are not accepted (got {raw!r}) — "
            "use a plain integer for whole-dollar amounts."
        )
    raise ValueError(
        f"{field_name}: must be an integer (got {type(raw).__name__}: {raw!r})"
    )


def _parse_notional_principal(raw) -> "tuple[NotionalScheduleEntry, ...] | None":
    """Parse a venue's ``notional_principal_usd`` field.

    Three YAML forms supported:

    Scalar (always-on, constant notional):
        ``notional_principal_usd: 50000000``  →  one entry, ``start_date``
        set to ``date.min`` so it's active for every settlement period.

    Date-ranged schedule (step function for time-varying notional):
        ``notional_principal_usd:
              - start_date: '2025-12-19'
                amount: 50000000
              - start_date: '2026-06-16'
                amount: 0``
        Each entry sets the notional from ``start_date`` onward. The
        compute layer time-weights across the settlement period. Duplicate
        ``start_date`` values are rejected to avoid an ambiguous tie-break.

    Single-entry date-ranged (notional activates from a specific date,
    $0 before that — semantically different from the scalar form):
        ``notional_principal_usd:
              - start_date: '2026-02-01'
                amount: 25000000``
        Use this when Grove (or the prime team) started tracking off-chain
        notional on a specific date that isn't ``date.min``. Periods
        entirely before ``start_date`` get $0 notional, not ``amount``.
    """
    if raw is None:
        return None
    if isinstance(raw, (bool, int, float)):
        amount = _parse_usd_amount(raw, "notional_principal_usd")
        return (
            NotionalScheduleEntry(
                start_date=date.min,
                amount=amount,
            ),
        )
    if isinstance(raw, list):
        entries = tuple(
            NotionalScheduleEntry(
                start_date=date.fromisoformat(e["start_date"]),
                amount=Decimal(str(e["amount"])),
            )
            for e in raw
        )
        seen: set[date] = set()
        for e in entries:
            if e.start_date in seen:
                raise ValueError(
                    f"notional_principal_usd: duplicate start_date "
                    f"{e.start_date.isoformat()}"
                )
            seen.add(e.start_date)
        return entries
    raise ValueError(
        f"notional_principal_usd must be a number or a list of "
        f"{{start_date, amount}} entries; got {type(raw).__name__}: {raw!r}"
    )


def _parse_min_transfer(v: dict) -> Decimal | None:
    """Read ``min_transfer_amount_usd`` from a venue stanza.

    Accepts either the new flat key or the legacy ``flow_filter:
    min_transfer_amount_usd:`` block (kept for backward-compat with
    fixtures captured before the schema flattening).
    """
    raw = v.get("min_transfer_amount_usd")
    if raw is None and isinstance(v.get("flow_filter"), dict):
        raw = v["flow_filter"].get("min_transfer_amount_usd")
    return Decimal(str(raw)) if raw is not None else None


def load_prime(config_path: Path) -> Prime:
    """Load a `Prime` value object from a YAML file."""
    with config_path.open() as f:
        cfg = yaml.safe_load(f)

    subproxy = {}
    alm = {}
    psm: dict[Chain, PsmConfig] = {}
    for chain_str, addrs in cfg.get("addresses", {}).items():
        chain = Chain(chain_str)
        if "subproxy" in addrs:
            subproxy[chain] = Address.from_str(addrs["subproxy"])
        if "alm" in addrs:
            alm[chain] = Address.from_str(addrs["alm"])
        if "psm" in addrs:
            p = addrs["psm"]
            psm[chain] = PsmConfig(
                kind=PsmKind(p["kind"]),
                address=Address.from_str(p["address"]),
                token=Address.from_str(p["token"]) if p.get("token") else None,
            )

    venues: list[Venue] = []
    for v in cfg.get("venues", []):
        chain = Chain(v["chain"])
        token = Token.from_dict(chain, v["token"])
        underlying = (
            Token.from_dict(chain, v["underlying"]) if v.get("underlying") else None
        )

        nav_oracle = None
        if "nav_oracle" in v:
            no = v["nav_oracle"]
            nav_oracle = NavOracle(
                kind=no["kind"],
                address=Address.from_str(no["address"]) if no.get("address") else None,
                underlying_decimals=(
                    int(no["underlying_decimals"]) if no.get("underlying_decimals") else None
                ),
                fallback=no.get("fallback"),
                fallback_address=(
                    Address.from_str(no["fallback_address"])
                    if no.get("fallback_address")
                    else None
                ),
                fallback_underlying_decimals=(
                    int(no["fallback_underlying_decimals"])
                    if no.get("fallback_underlying_decimals")
                    else None
                ),
                fallback2=no.get("fallback2"),
                fallback2_address=(
                    Address.from_str(no["fallback2_address"])
                    if no.get("fallback2_address")
                    else None
                ),
                fallback2_underlying_decimals=(
                    int(no["fallback2_underlying_decimals"])
                    if no.get("fallback2_underlying_decimals")
                    else None
                ),
                oracle_chain=Chain(no["oracle_chain"]) if no.get("oracle_chain") else None,
            )

        curve_idle_usds = None
        if "curve_idle_usds" in v:
            ciu = v["curve_idle_usds"]
            curve_idle_usds = CurveIdleUsdsConfig(
                coin=Address.from_str(ciu["coin"]),
                sky_savings_token=bool(ciu.get("sky_savings_token", False)),
                sde_coin=Address.from_str(ciu["sde_coin"]) if ciu.get("sde_coin") else None,
            )

        univ4_pool_key = None
        if "univ4_pool_key" in v:
            pk = v["univ4_pool_key"]
            univ4_pool_key = UniV4PoolKey(
                currency0=Address.from_str(pk["currency0"]),
                currency1=Address.from_str(pk["currency1"]),
                fee=int(pk["fee"]),
                tick_spacing=int(pk["tick_spacing"]),
                hooks=Address.from_str(pk.get("hooks", "0x0000000000000000000000000000000000000000")),
            )
        univ4_token_ids = tuple(int(t) for t in v.get("univ4_token_ids", []))

        venues.append(
            Venue(
                id=v["id"],
                chain=chain,
                token=token,
                pricing_category=PricingCategory(v["pricing_category"]),
                underlying=underlying,
                label=v.get("label", ""),
                nav_oracle=nav_oracle,
                lp_kind=v.get("lp_kind"),
                nft_position_manager=(
                    Address.from_str(v["nft_position_manager"])
                    if v.get("nft_position_manager")
                    else None
                ),
                univ4_pool_key=univ4_pool_key,
                univ4_token_ids=univ4_token_ids,
                cof_excluded=bool(v.get("cof_excluded", False)),
                hide_per_venue_pnl=bool(v.get("hide_per_venue_pnl", False)),
                min_transfer_amount_usd=_parse_min_transfer(v),
                fixed_fee_per_capital_event_usd=(
                    _parse_usd_amount(
                        v["fixed_fee_per_capital_event_usd"],
                        "fixed_fee_per_capital_event_usd",
                    )
                    if v.get("fixed_fee_per_capital_event_usd") is not None
                    else None
                ),
                sky_direct=bool(v.get("sky_direct", False)),
                holder_override=(
                    Address.from_str(v["holder_override"])
                    if v.get("holder_override")
                    else None
                ),
                skip=bool(v.get("skip", False)),
                cash_distributions=[
                    CashDistributionSource(
                        payer=Address.from_str(d["payer"]),
                        token=Address.from_str(d["token"]),
                        chain=Chain(d["chain"]) if d.get("chain") else None,
                    )
                    for d in v.get("cash_distributions", [])
                ],
                curve_idle_usds=curve_idle_usds,
                lending_idle_usds=bool(v.get("lending_idle_usds", False)),
                sky_savings_token=bool(v.get("sky_savings_token", False)),
                deduct_savings_v2_deployed=bool(v.get("deduct_savings_v2_deployed", False)),
                demand_side_spread=bool(v.get("demand_side_spread", False)),
                share_burn_destinations=[
                    Address.from_str(a) for a in v.get("share_burn_destinations", [])
                ],
                centrifuge_vault=(
                    Address.from_str(v["centrifuge_vault"])
                    if v.get("centrifuge_vault")
                    else None
                ),
                paired_with=v.get("paired_with"),
                paired_source=(
                    Address.from_str(v["paired_source"])
                    if v.get("paired_source")
                    else None
                ),
                display_only=bool(v.get("display_only", False)),
                force_capital_inflow=bool(v.get("force_capital_inflow", False)),
                external_yield_source=bool(v.get("external_yield_source", False)),
                notional_principal_usd=_parse_notional_principal(
                    v.get("notional_principal_usd")
                ),
            )
        )

    external_alm_sources: dict[Chain, list[Address]] = {}
    for chain_str, addrs in cfg.get("external_alm_sources", {}).items():
        chain = Chain(chain_str)
        external_alm_sources[chain] = [Address.from_str(a) for a in addrs]

    def _parse_event_overrides(
        key: str,
    ) -> dict[Chain, dict[Address, list[PrincipalReturnOverride]]]:
        """Parse a ``{chain: {address: [{date, amount, token?, note?}]}}``
        override block (shared shape between ``principal_return_overrides``
        and ``yield_reversal_overrides``)."""
        out: dict[Chain, dict[Address, list[PrincipalReturnOverride]]] = {}
        for chain_str, by_addr in cfg.get(key, {}).items():
            chain = Chain(chain_str)
            out[chain] = {}
            for addr_str, entries in by_addr.items():
                addr = Address.from_str(addr_str)
                out[chain][addr] = [
                    PrincipalReturnOverride(
                        date=date.fromisoformat(e["date"]),
                        amount=Decimal(str(e["amount"])),
                        token=e.get("token", ""),
                        note=e.get("note", ""),
                    )
                    for e in entries
                ]
        return out

    principal_return_overrides = _parse_event_overrides("principal_return_overrides")
    yield_reversal_overrides = _parse_event_overrides("yield_reversal_overrides")

    return Prime(
        id=cfg["id"],
        # Optional for agent-rate-only primes (Keel, Skybase) — no allocator
        # ilk exists, so the debt machinery degrades to an all-zero series.
        ilk_bytes32=(
            _parse_ilk_bytes32(cfg["ilk_bytes32"])
            if cfg.get("ilk_bytes32") is not None
            else None
        ),
        start_date=date.fromisoformat(cfg["start_date"]),
        subproxy=subproxy,
        alm=alm,
        psm=psm,
        venues=venues,
        external_alm_sources=external_alm_sources,
        principal_return_overrides=principal_return_overrides,
        yield_reversal_overrides=yield_reversal_overrides,
        subsidy=SubsidyConfig.from_dict(cfg.get("subsidy")),
        sources=dict(cfg.get("sources") or {}),
    )


def load_prime_by_id(prime_id: str, config_dir: Path | None = None) -> Prime:
    """Load `<config_dir>/<prime_id>.yaml`. `config_dir` defaults to `./config/`."""
    base = config_dir or (Path(__file__).resolve().parents[3] / "config")
    return load_prime(base / f"{prime_id}.yaml")
