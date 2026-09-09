"""Treasury Management Function — on-chain activity records.

Plain data decoded from Smart Burn Engine events, shared by the HyperSync
source (``normalize/sources/hypersync_sbe.py``, producer) and the TMF compute
(``compute/tmf.py``, consumer). Lives in ``domain`` so the normalize layer
depends only on extract + domain, per PRD §4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

__all__ = ["SbeActivity", "SbeDistribution", "SbeKick", "SbeParamChange"]


@dataclass(frozen=True)
class SbeKick:
    """One Kicker.flap → Splitter.kick → Flapper.exec batch."""

    block: int
    log_index: int
    ts: int
    tx: str
    tot: Decimal      # USDS pulled from the surplus (Kick.tot, rad → USDS)
    lot: Decimal      # USDS to the Flapper (Kick.lot)
    pay: Decimal      # USDS to the USDS farm (Kick.pay)
    bought: Decimal   # SKY received by the receiver (Exec.bought; 0 when lot == 0)
    burn: Decimal     # splitter.burn in force at the kick (wad → fraction)
    hop: int          # splitter.hop in force at the kick


@dataclass(frozen=True)
class SbeParamChange:
    block: int
    log_index: int
    ts: int
    tx: str
    contract: str     # chainlog key
    what: str             # 'hop' | 'burn' | 'kbump' | 'khump' | 'vestId' | 'rewardsDuration' |
                          # 'vest.init (id)' | 'vest.yank (id)' | '<what> (raw)' for unknown levers
    value: Decimal | str  # human units (seconds / fraction / USDS / id); an address for File(address)


@dataclass(frozen=True)
class SbeDistribution:
    """A REWARDS_DIST_LSSKY_SKY.distribute() — vested SKY moved into the SKY farm."""

    block: int
    ts: int
    tx: str
    amount: Decimal   # SKY


@dataclass
class SbeActivity:
    month: str
    from_block: int
    to_block: int
    from_ts: int
    to_ts: int
    kicks: list[SbeKick] = field(default_factory=list)
    param_changes: list[SbeParamChange] = field(default_factory=list)
    distributions: list[SbeDistribution] = field(default_factory=list)
