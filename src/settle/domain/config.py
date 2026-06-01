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
    Venue,
)
from .subsidy import SubsidyConfig


def _parse_ilk_bytes32(s: str) -> bytes:
    s = s.lower().removeprefix("0x")
    if len(s) != 64:
        raise ValueError(f"ilk_bytes32 must be 64 hex chars; got {len(s)} ({s!r})")
    return bytes.fromhex(s)


def _parse_notional_principal(raw) -> "tuple[NotionalScheduleEntry, ...] | None":
    """Parse a venue's ``notional_principal_usd`` field.

    Two YAML forms supported:

    Scalar (constant notional):
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
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        # bool is a subclass of int — reject explicitly before the int branch
        # so YAML ``true``/``false`` doesn't quietly become Decimal("1")/("0").
        raise ValueError(
            f"notional_principal_usd: bool is not a valid notional amount "
            f"(got {raw!r})"
        )
    if isinstance(raw, int):
        return (
            NotionalScheduleEntry(
                start_date=date.min,
                amount=Decimal(raw),
            ),
        )
    if isinstance(raw, float):
        # YAML ``50000000.0`` parses as Python float — Decimal(str(float))
        # is exact for integer-valued floats but introduces representation
        # noise for fractional values (e.g. Decimal(str(0.1)) is "0.1" but
        # Decimal(str(50123456.78)) has trailing float-rep digits). Refuse
        # rather than silently corrupt notional amounts. Operators wanting
        # fractional notional should use the list form with an explicit
        # string amount, or wrap the scalar in quotes.
        raise ValueError(
            f"notional_principal_usd: float values are not accepted (got "
            f"{raw!r}) — use a plain integer for whole-dollar amounts, or "
            "the list form with a quoted string amount for fractional."
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
                cof_excluded=bool(v.get("cof_excluded", False)),
                min_transfer_amount_usd=_parse_min_transfer(v),
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
                notional_principal_usd=_parse_notional_principal(
                    v.get("notional_principal_usd")
                ),
            )
        )

    external_alm_sources: dict[Chain, list[Address]] = {}
    for chain_str, addrs in cfg.get("external_alm_sources", {}).items():
        chain = Chain(chain_str)
        external_alm_sources[chain] = [Address.from_str(a) for a in addrs]

    principal_return_overrides: dict[
        Chain, dict[Address, list[PrincipalReturnOverride]]
    ] = {}
    for chain_str, by_addr in cfg.get("principal_return_overrides", {}).items():
        chain = Chain(chain_str)
        principal_return_overrides[chain] = {}
        for addr_str, entries in by_addr.items():
            addr = Address.from_str(addr_str)
            principal_return_overrides[chain][addr] = [
                PrincipalReturnOverride(
                    date=date.fromisoformat(e["date"]),
                    amount=Decimal(str(e["amount"])),
                    token=e.get("token", ""),
                    note=e.get("note", ""),
                )
                for e in entries
            ]

    return Prime(
        id=cfg["id"],
        ilk_bytes32=_parse_ilk_bytes32(cfg["ilk_bytes32"]),
        start_date=date.fromisoformat(cfg["start_date"]),
        subproxy=subproxy,
        alm=alm,
        psm=psm,
        venues=venues,
        external_alm_sources=external_alm_sources,
        principal_return_overrides=principal_return_overrides,
        subsidy=SubsidyConfig.from_dict(cfg.get("subsidy")),
    )


def load_prime_by_id(prime_id: str, config_dir: Path | None = None) -> Prime:
    """Load `<config_dir>/<prime_id>.yaml`. `config_dir` defaults to `./config/`."""
    base = config_dir or (Path(__file__).resolve().parents[3] / "config")
    return load_prime(base / f"{prime_id}.yaml")
