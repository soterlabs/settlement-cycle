"""YAML config loader. Reads `config/<prime>.yaml` and produces a `Prime` value object."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from .pricing import PricingCategory
from .primes import Address, Chain, Prime, Token, Venue


def _parse_ilk_bytes32(s: str) -> bytes:
    s = s.lower().removeprefix("0x")
    if len(s) != 64:
        raise ValueError(f"ilk_bytes32 must be 64 hex chars; got {len(s)} ({s!r})")
    return bytes.fromhex(s)


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
        venues.append(
            Venue(
                id=v["id"],
                chain=chain,
                token=token,
                pricing_category=PricingCategory(v["pricing_category"]),
                underlying=underlying,
                label=v.get("label", ""),
            )
        )

    return Prime(
        id=cfg["id"],
        ilk_bytes32=_parse_ilk_bytes32(cfg["ilk_bytes32"]),
        start_date=date.fromisoformat(cfg["start_date"]),
        subproxy=subproxy,
        alm=alm,
        venues=venues,
    )


def load_prime_by_id(prime_id: str, config_dir: Path | None = None) -> Prime:
    """Load `<config_dir>/<prime_id>.yaml`. `config_dir` defaults to `./config/`."""
    base = config_dir or (Path(__file__).resolve().parents[3] / "config")
    return load_prime(base / f"{prime_id}.yaml")
