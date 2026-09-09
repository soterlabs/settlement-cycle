"""Smart Burn Engine history — the dashboard dataset.

Aggregates the full per-kick history (``domain.tmf.SbeKick``) and the SKY burn
stream (``domain.tmf.SkyBurn``) into the three USDS series the dashboard shows —

    usds_buyback      USDS the Splitter sent to the Flapper (bought SKY)
    usds_to_stakers   USDS the Splitter sent to the USDS staker farm
    usds_total        their sum (= USDS pulled from the surplus)

— by month, quarter and year, plus SKY bought, the volume-weighted price, and
SKY burned (protocol vs other). The output is a versioned JSON (aggregates,
totals, latest kick, parameter timeline) and two per-event CSVs, written to
``settlements/tmf/data/`` and mirrored into settlement-reports by the publish
workflow. Consumers read the JSON — never the markdown reports.

Pure: no I/O besides ``write_history_dataset``.
"""

from __future__ import annotations

import csv
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from ..domain.tmf import SbeKick, SbeParamChange, SkyBurn

__all__ = [
    "SCHEMA_VERSION",
    "HistoryDataset",
    "PeriodRow",
    "aggregate",
    "build_history_dataset",
    "period_key",
    "write_history_dataset",
]

SCHEMA_VERSION = "1.1.0"   # 1.1: parameter_changes rows carry `address`; values are `_dec`-formatted
ZERO = Decimal(0)
_Q2 = Decimal("0.01")
_Q6 = Decimal("0.000001")


def _ts(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), UTC).isoformat().replace("+00:00", "Z")


def period_key(ts: int, granularity: str) -> str:
    """``monthly`` → ``2026-08``, ``quarterly`` → ``2026-Q3``, ``annual`` → ``2026``."""
    d = datetime.fromtimestamp(int(ts), UTC)
    if granularity == "monthly":
        return f"{d.year:04d}-{d.month:02d}"
    if granularity == "quarterly":
        return f"{d.year:04d}-Q{(d.month - 1) // 3 + 1}"
    if granularity == "annual":
        return f"{d.year:04d}"
    raise ValueError(f"unknown granularity {granularity!r}")


@dataclass
class PeriodRow:
    period: str
    kicks: int = 0
    usds_buyback: Decimal = ZERO
    usds_to_stakers: Decimal = ZERO
    usds_total: Decimal = ZERO
    sky_bought: Decimal = ZERO
    sky_burn_protocol: Decimal = ZERO
    sky_burn_other: Decimal = ZERO
    burn_events: int = 0
    first_ts: int | None = None
    last_ts: int | None = None

    @property
    def sky_avg_price(self) -> Decimal | None:
        return (self.usds_buyback / self.sky_bought) if self.sky_bought > 0 else None

    def as_json(self) -> dict[str, Any]:
        return {
            "period": self.period,
            "kicks": self.kicks,
            "usds_buyback": _num(self.usds_buyback),
            "usds_to_stakers": _num(self.usds_to_stakers),
            "usds_total": _num(self.usds_total),
            "sky_bought": _num(self.sky_bought),
            "sky_avg_price": _num(self.sky_avg_price, _Q6),
            "sky_burn_protocol": _num(self.sky_burn_protocol),
            "sky_burn_other": _num(self.sky_burn_other),
            "burn_events": self.burn_events,
            "first_ts": _ts(self.first_ts) if self.first_ts is not None else None,
            "last_ts": _ts(self.last_ts) if self.last_ts is not None else None,
        }


def _num(x: Decimal | None, q: Decimal = _Q2) -> float | None:
    """JSON number, quantized — the dataset is for display and charting; the
    CSVs carry the full-precision per-event values."""
    if x is None:
        return None
    return float(x.quantize(q, rounding=ROUND_HALF_UP))


def aggregate(
    kicks: list[SbeKick], burns: list[SkyBurn], granularity: str
) -> list[PeriodRow]:
    """Bucket kicks and burns into ``granularity`` periods, in chronological
    order. A period with burns but no kicks (or vice versa) still appears."""
    rows: OrderedDict[str, PeriodRow] = OrderedDict()

    def row(ts: int) -> PeriodRow:
        k = period_key(ts, granularity)
        if k not in rows:
            rows[k] = PeriodRow(period=k)
        return rows[k]

    for k in sorted(kicks, key=lambda k: (k.block, k.log_index)):
        r = row(k.ts)
        r.kicks += 1
        r.usds_buyback += k.lot
        r.usds_to_stakers += k.pay
        r.usds_total += k.tot
        r.sky_bought += k.bought
        r.first_ts = k.ts if r.first_ts is None else min(r.first_ts, k.ts)
        r.last_ts = k.ts if r.last_ts is None else max(r.last_ts, k.ts)
    for b in sorted(burns, key=lambda b: (b.block, b.log_index)):
        r = row(b.ts)
        r.burn_events += 1
        if b.protocol:
            r.sky_burn_protocol += b.amount
        else:
            r.sky_burn_other += b.amount
    return [rows[k] for k in sorted(rows)]


@dataclass
class HistoryDataset:
    from_block: int
    to_block: int
    to_ts: int
    kicks: list[SbeKick]
    burns: list[SkyBurn]
    param_changes: list[SbeParamChange] = field(default_factory=list)
    contracts: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def totals(self) -> PeriodRow:
        agg = PeriodRow(period="all")
        for r in aggregate(self.kicks, self.burns, "annual"):
            agg.kicks += r.kicks
            agg.usds_buyback += r.usds_buyback
            agg.usds_to_stakers += r.usds_to_stakers
            agg.usds_total += r.usds_total
            agg.sky_bought += r.sky_bought
            agg.sky_burn_protocol += r.sky_burn_protocol
            agg.sky_burn_other += r.sky_burn_other
            agg.burn_events += r.burn_events
            if r.first_ts is not None:
                agg.first_ts = r.first_ts if agg.first_ts is None else min(agg.first_ts, r.first_ts)
            if r.last_ts is not None:
                agg.last_ts = r.last_ts if agg.last_ts is None else max(agg.last_ts, r.last_ts)
        return agg


def _dec(x: Decimal) -> str:
    """Exact Decimal as plain digits: trailing zeros stripped (``25000.000…`` →
    ``25000``) and never exponent notation (``1E-18`` → ``0.000000000000000001``)."""
    return format(x.normalize(), "f")


def build_history_dataset(ds: HistoryDataset) -> dict[str, Any]:
    """The JSON document. Field names are the contract with msc-dashboard —
    bump ``SCHEMA_VERSION`` (semver) when they change."""
    kicks = sorted(ds.kicks, key=lambda k: (k.block, k.log_index))
    last = kicks[-1] if kicks else None
    params = sorted(ds.param_changes, key=lambda c: (c.block, c.log_index))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _ts(int(datetime.now(UTC).timestamp())),
        "source": {
            "chain": "ethereum",
            "from_block": ds.from_block,
            "to_block": ds.to_block,
            "to_ts": _ts(ds.to_ts),
            "contracts": ds.contracts,
            "events": {
                "kick": "Splitter.Kick(tot, lot, pay) joined to Flapper.Exec(lot, bought) per tx",
                "burn": "SKY.Transfer(from, to=sink, amount); protocol = sender is the Pause Proxy",
            },
        },
        "definitions": {
            "usds_buyback": "USDS the Splitter sent to the Flapper to buy SKY (Kick.lot)",
            "usds_to_stakers": "USDS the Splitter sent to the USDS staker farm (Kick.pay)",
            "usds_total": "usds_buyback + usds_to_stakers (= USDS pulled from the surplus, Kick.tot)",
            "sky_bought": "SKY the Flapper received for usds_buyback (Exec.bought)",
            "sky_avg_price": "usds_buyback / sky_bought (USDS per SKY, volume-weighted)",
            "sky_burn_protocol": "SKY sent to a burn sink by the protocol (Pause Proxy)",
            "sky_burn_other": "SKY sent to 0x…dEaD by anyone else",
        },
        "notes": ds.notes,
        "totals": ds.totals.as_json(),
        "latest_kick": None if last is None else {
            "ts": _ts(last.ts), "block": last.block, "tx": last.tx,
            "usds_buyback": _num(last.lot), "usds_to_stakers": _num(last.pay),
            "usds_total": _num(last.tot), "sky_bought": _num(last.bought),
            "splitter_burn": _num(last.burn, Decimal("0.0001")), "splitter_hop": last.hop,
        },
        "periods": {
            g: [r.as_json() for r in aggregate(ds.kicks, ds.burns, g)]
            for g in ("monthly", "quarterly", "annual")
        },
        "parameter_changes": [
            {
                "ts": _ts(c.ts), "block": c.block, "tx": c.tx,
                "contract": c.contract, "address": c.address, "what": c.what,
                "value": _dec(c.value) if isinstance(c.value, Decimal) else c.value,
            }
            for c in params
        ],
    }


_KICK_COLUMNS = [
    "ts", "block", "log_index", "tx", "usds_total", "usds_buyback", "usds_to_stakers",
    "sky_bought", "splitter_burn", "splitter_hop", "farm", "flapper",
]
_BURN_COLUMNS = ["ts", "block", "log_index", "tx", "sender", "sink", "sky_amount", "protocol"]


def write_history_dataset(ds: HistoryDataset, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = build_history_dataset(ds)
    (out_dir / "sbe_history.json").write_text(json.dumps(doc, indent=2) + "\n")

    with (out_dir / "sbe_kicks.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_KICK_COLUMNS)
        for k in sorted(ds.kicks, key=lambda k: (k.block, k.log_index)):
            w.writerow([
                _ts(k.ts), k.block, k.log_index, k.tx, _dec(k.tot), _dec(k.lot), _dec(k.pay),
                _dec(k.bought), _dec(k.burn), k.hop, k.farm or "", k.flapper or "",
            ])
    with (out_dir / "sky_burns.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_BURN_COLUMNS)
        for b in sorted(ds.burns, key=lambda b: (b.block, b.log_index)):
            w.writerow([_ts(b.ts), b.block, b.log_index, b.tx, b.sender, b.sink, _dec(b.amount),
                        "true" if b.protocol else "false"])
    return {
        "json": out_dir / "sbe_history.json",
        "kicks": out_dir / "sbe_kicks.csv",
        "burns": out_dir / "sky_burns.csv",
    }
