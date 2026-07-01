"""Centralized settlement pin-block accessor.

Single source of truth lives in ``config/pin_blocks.yaml``. This module
loads it once and exposes it both as raw ``str``-keyed dicts (for the Dune
capture scripts, which key by lowercase chain name) and as ``Chain``-keyed
dicts (for the runners, which build ``pin_blocks_som``/``pin_blocks_eom``).

See ``config/pin_blocks.yaml`` for the semantics (EoD blocks, som = prior
month's eom, Monad Q1 placeholders).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml

from .primes import Chain

_PIN_BLOCKS_YAML = Path(__file__).resolve().parents[3] / "config" / "pin_blocks.yaml"


@lru_cache(maxsize=1)
def _raw() -> dict:
    data = yaml.safe_load(_PIN_BLOCKS_YAML.read_text())
    return data["months"]


def _key(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


def month_boundaries(year: int, month: int) -> dict[str, dict[str, int]]:
    """Raw ``str``-keyed pins: ``{"som": {chain: block}, "eom": {chain: block}}``.

    Chain keys are lowercase names matching ``Chain(...).value`` (e.g.
    ``"ethereum"``, ``"avalanche_c"``). Used by the Dune capture scripts.
    """
    key = _key(year, month)
    try:
        entry = _raw()[key]
    except KeyError:
        raise KeyError(
            f"No pin blocks for {key} in config/pin_blocks.yaml — add them there."
        ) from None
    return {
        "som": {c: int(b) for c, b in entry["som"].items()},
        "eom": {c: int(b) for c, b in entry["eom"].items()},
    }


def eom_blocks_str(year: int, month: int) -> dict[str, int]:
    """``str``-keyed end-of-month blocks for the capture scripts."""
    return month_boundaries(year, month)["eom"]


def month_pins(
    year: int, month: int, chains: Iterable[Chain] | None = None,
) -> dict[str, dict[Chain, int]]:
    """``Chain``-keyed ``{"som": {...}, "eom": {...}}`` for the runners.

    When ``chains`` is given, the result is restricted to those chains
    (so a prime only sees the chains it actually uses).
    """
    raw = month_boundaries(year, month)
    wanted = set(chains) if chains is not None else None
    out: dict[str, dict[Chain, int]] = {}
    for side in ("som", "eom"):
        d: dict[Chain, int] = {}
        for chain_name, block in raw[side].items():
            chain = Chain(chain_name)
            if wanted is None or chain in wanted:
                d[chain] = block
        out[side] = d
    return out
