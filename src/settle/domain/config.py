"""YAML config loader. Reads `config/<prime>.yaml` and produces a `Prime` value object."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from .pricing import PricingCategory
from .primes import Address, Chain, NavOracle, Prime, Token, Venue


def _parse_ilk_bytes32(s: str) -> bytes:
    s = s.lower().removeprefix("0x")
    if len(s) != 64:
        raise ValueError(f"ilk_bytes32 must be 64 hex chars; got {len(s)} ({s!r})")
    return bytes.fromhex(s)


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
    for chain_str, addrs in cfg.get("addresses", {}).items():
        chain = Chain(chain_str)
        if "subproxy" in addrs:
            subproxy[chain] = Address.from_str(addrs["subproxy"])
        if "alm" in addrs:
            alm[chain] = Address.from_str(addrs["alm"])

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
                fallback=no.get("fallback"),
                fallback_address=(
                    Address.from_str(no["fallback_address"])
                    if no.get("fallback_address")
                    else None
                ),
                oracle_chain=Chain(no["oracle_chain"]) if no.get("oracle_chain") else None,
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
                min_transfer_amount_usd=_parse_min_transfer(v),
                sky_direct=bool(v.get("sky_direct", False)),
            )
        )

    external_alm_sources: dict[Chain, list[Address]] = {}
    for chain_str, addrs in cfg.get("external_alm_sources", {}).items():
        chain = Chain(chain_str)
        external_alm_sources[chain] = [Address.from_str(a) for a in addrs]

    return Prime(
        id=cfg["id"],
        ilk_bytes32=_parse_ilk_bytes32(cfg["ilk_bytes32"]),
        start_date=date.fromisoformat(cfg["start_date"]),
        subproxy=subproxy,
        alm=alm,
        venues=venues,
        external_alm_sources=external_alm_sources,
    )


def load_prime_by_id(prime_id: str, config_dir: Path | None = None) -> Prime:
    """Load `<config_dir>/<prime_id>.yaml`. `config_dir` defaults to `./config/`."""
    base = config_dir or (Path(__file__).resolve().parents[3] / "config")
    return load_prime(base / f"{prime_id}.yaml")
