"""Consolidated Sky Net Revenue — MSC leg (buffer basis) + non-MSC.

Implements the 2026-07-16 handoff methodology §3, extracted from the M+1
settlement block (see ``hypersync_msc_buffer`` for the timing/detection
details):

    MSC net (buffer basis) = Σ debt minted to buffer per prime
                           − Σ sent to prime subproxies
                           − sent to Demand-side Buffer
                           − sent to Core Council (genesis repayment ONLY)
                           − Grove token-launch penalty (excluded per forum)
    Sky Net Revenue        = MSC net + non-MSC income − non-MSC expense

The Core Council on-chain mint is GROSS — it bundles the genesis-capital
repayment (which reduces Sky's revenue) with the Step 1 Capital 20%
distribution (which is Sky's revenue being distributed, NOT a cost). We
separate them algebraically: if ``x`` is Sky Net Revenue and ``g`` is the
CC-genesis component, then

    x = mint − subproxies − DSB − g − grove_penalty + non_msc_net
    x = mint − subproxies − DSB − (cc_gross − 0.20·x) − grove_penalty + non_msc_net
    0.80·x = mint − subproxies − DSB − cc_gross − grove_penalty + non_msc_net
    x = (mint − subproxies − DSB − cc_gross − grove_penalty + non_msc_net) / 0.80
    g = cc_gross − 0.20·x
    MSC net = x − non_msc_net

This ties BA's cross-check exactly ("20% of BA's net = Step 1 Capital split to
Core Council + Fortification = 2,742,939" for June 2026).

The Grove TGE penalty is a per-month config override (its on-chain mechanism
is "still open with BA" — PRD §17.13 B16); the June 2026 value from the doc
is pinned in ``config/sky_total.yaml``, other months surface a warning until
back-filled.

**One-off subproxy inflows** (initial capital seeding, e.g. Skybase's $10M in
MSC#5, Keel's $10M in MSC#7) are read from ``config/sky_total.yaml``.
On-chain trace (tx 0xe5a95157… / 0xbebdd875…) shows the $10M came from
``Vat.suck(u=vow, v=<intermediate>, rad=10M×RAD)`` — a direct draw on Sky's
surplus buffer, NOT from the allocator ilks' GRAB dart. So the seeding
REDUCES Sky's monthly buffer-basis Net Revenue (it's a real cost, backed by
new sin on vow — a claim on future revenue to be paid back via ilk folds).
The formula uses raw subproxy sends (which include the $10M), correctly
subtracting the seeding from MSC net. The summary renders the one-off as an
informational sub-row so audit can see what portion of a subproxy line is
capital-seeding vs recurring revenue distribution. If a policy view wants
"operational" Sky Net Revenue that excludes capital seeding, add ``one_off``
back to SNR downstream — but the doc §3 methodology (literal
"debt minted…minus everything sent back out…minus penalty") subtracts it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..domain import Month

_log = logging.getLogger(__name__)

# Step 1 Capital ratio — 20% of Sky Net Revenue is distributed to Core Council
# + Fortification per BA methodology (doc §3 cross-check). If Sky ever changes
# this ratio, update the config schema to carry it per-month and pass it
# through here.
_STEP1_CAPITAL_RATIO = Decimal("0.20")

# Extended 2026-08-05 with the Diamond PAU compartments + Osero, matching
# config/sky_total.yaml's allocator_ilks / subproxies keys (the review-fix
# that registered the new ilks left these compute-side allowlists behind —
# any mismatch fails loud here).
_MINT_PRIMES = ("spark", "grove", "obex", "grove_pau", "osero")
_ALL_PRIMES = ("spark", "grove", "obex", "keel", "skybase", "osero")


def _month_bounds(month: Month) -> tuple[date, date]:
    start = date(month.year, month.month, 1)
    if month.month == 12:
        end_excl = date(month.year + 1, 1, 1)
    else:
        end_excl = date(month.year, month.month + 1, 1)
    return start, end_excl


@dataclass
class SkyTotalMonthly:
    month: str
    settlement_block: int
    settlement_ts: int
    # Buffer-basis MSC components (USDS).
    mint_per_prime: dict[str, Decimal]              # spark/grove/obex only
    subproxy_raw_per_prime: dict[str, Decimal]      # all 5, on-chain settlement-block mint
    one_off_per_prime: dict[str, Decimal]           # config-driven exclusions (initial capital, etc.)
    dsb: Decimal
    cc_gross: Decimal                               # on-chain USDS mint to CC
    grove_tge_penalty: Decimal
    grove_tge_penalty_source: str                   # "config:<month>" | "unset"
    # Non-MSC inputs (pulled from settlements/non_msc/<month>/provenance.json).
    non_msc_income: Decimal
    non_msc_expense: Decimal
    warnings: list[str] = field(default_factory=list)

    @property
    def subproxy_adjusted_per_prime(self) -> dict[str, Decimal]:
        """Display-only: raw settlement-block mint − configured one-off
        exclusions. Rendered in the summary next to the raw figure so a
        reader sees the revenue-distribution vs capital-seeding split. Does
        NOT feed the buffer-basis formula — see the module docstring."""
        return {
            p: self.subproxy_raw_per_prime[p] - self.one_off_per_prime.get(p, Decimal(0))
            for p in self.subproxy_raw_per_prime
        }

    @property
    def total_mint(self) -> Decimal:
        return sum(self.mint_per_prime.values(), Decimal(0))

    @property
    def total_subproxy_raw(self) -> Decimal:
        return sum(self.subproxy_raw_per_prime.values(), Decimal(0))

    @property
    def total_one_off(self) -> Decimal:
        return sum(self.one_off_per_prime.values(), Decimal(0))

    @property
    def total_subproxy_adjusted(self) -> Decimal:
        return self.total_subproxy_raw - self.total_one_off

    @property
    def non_msc_net(self) -> Decimal:
        return self.non_msc_income - self.non_msc_expense

    @property
    def sky_net_revenue(self) -> Decimal:
        """Algebraic derivation — see module docstring. Uses RAW subproxy
        sends (one-offs cancel against the corresponding allocator mint that
        raised the same debt in the same settlement)."""
        num = (
            self.total_mint
            - self.total_subproxy_raw
            - self.dsb
            - self.cc_gross
            - self.grove_tge_penalty
            + self.non_msc_net
        )
        if self.settlement_block == 0:
            # No settlement executed this month (execution-month bucketing,
            # 2026-01): nothing was distributed to CC, so there is no 20%
            # Step-1 component inside cc_gross to back out — the /0.80
            # gross-up would fabricate revenue (+25% of the non-MSC net).
            # This month's 20% is carved at the NEXT month's settlement.
            return num
        return num / (Decimal(1) - _STEP1_CAPITAL_RATIO)

    @property
    def cc_step1_capital(self) -> Decimal:
        """20% of Sky Net Revenue distributed to CC + Fortification. NOT a
        cost — Sky is distributing its own already-earned revenue. Zero in
        a no-settlement month (nothing was distributed; the carve happens
        at the next month's settlement)."""
        if self.settlement_block == 0:
            return Decimal(0)
        return _STEP1_CAPITAL_RATIO * self.sky_net_revenue

    @property
    def cc_genesis_repayment(self) -> Decimal:
        """Portion of the on-chain CC mint that reduces Sky's revenue (the
        actual "cost" carved out of ``cc_gross``)."""
        return self.cc_gross - self.cc_step1_capital

    @property
    def msc_net(self) -> Decimal:
        return self.sky_net_revenue - self.non_msc_net


def resolve_pin_block(month: Month) -> int:
    """Pin block: end of month M+1 (safe ceiling covering the M+1 settlement
    window). Unlike ``non_msc.resolve_pin_block`` which pins end-of-M, this
    unit pins end-of-M+1 because month M's MSC settlement fires somewhere in
    M+1 (the exact day varies from early-Feb-2 to mid-Jul-20 across MSC#5–10,
    but the M+1 rule holds). Clamped to a safe head for months still in
    progress."""
    from ..domain import Chain
    from ..extract import rpc

    if month.month >= 11:
        end_excl = date(month.year + 1, (month.month + 2 - 12), 1)
    else:
        end_excl = date(month.year, month.month + 2, 1)
    target = datetime.combine(end_excl, time.min, tzinfo=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    if target > now:
        target = now - timedelta(minutes=10)
    return rpc.find_block_at_or_before(Chain.ETHEREUM, target)


def _load_non_msc(repo_root: Path, month: Month) -> tuple[Decimal, Decimal, list[str]]:
    label = f"{month.year}-{month.month:02d}"
    p = repo_root / "settlements" / "non_msc" / label / "provenance.json"
    if not p.exists():
        raise FileNotFoundError(
            f"sky_total: missing non_msc provenance at {p} — run "
            f"`scripts/run_non_msc_2026.py` first"
        )
    prov = json.loads(p.read_text())
    r = prov["results"]
    return (
        Decimal(r["total_income"]),
        Decimal(r["total_expense"]),
        list(prov.get("warnings") or []),
    )


def compute_sky_total_monthly(
    month: Month,
    *,
    source: Any,
    repo_root: Path,
    pin_block: int | None = None,
    config: dict[str, Any] | None = None,
) -> SkyTotalMonthly:
    """Compute the buffer-basis Sky Net Revenue for ``month``.

    ``config`` is the parsed ``sky_total.yaml`` dict (see ``load_config`` in
    ``hypersync_msc_buffer``). When omitted, we fall back to the source's own
    config via ``source._cfg`` — convenient for the usual path where the
    caller instantiates both from the same file — but the parameter is the
    supported protocol boundary. Passing an explicit ``config`` lets tests
    (or alternate sources) drive the compute layer without exposing
    ``_cfg`` on the source.
    """
    if pin_block is None:
        pin_block = resolve_pin_block(month)
    if config is None:
        config = getattr(source, "_cfg", None)
    if config is None:
        raise ValueError(
            "sky_total: no config provided and source has no _cfg fallback — "
            "pass `config=load_config()` explicitly"
        )

    df = source.streams(month, pin_block)

    settlement_block = 0
    settlement_ts = 0
    mints: dict[str, Decimal] = {p: Decimal(0) for p in _MINT_PRIMES}
    subs: dict[str, Decimal] = {p: Decimal(0) for p in _ALL_PRIMES}
    dsb = cc = Decimal(0)
    warnings: list[str] = []

    for _, row in df.iterrows():
        stream = row["stream"]
        if stream == "settlement_block":
            settlement_block = int(row["amount"])
            continue
        if stream == "settlement_ts":
            settlement_ts = int(row["amount"])
            continue
        amount = Decimal(str(row["amount"]))
        if stream.startswith("mint:"):
            prime = stream.split(":", 1)[1]
            if prime not in _MINT_PRIMES:
                raise ValueError(f"sky_total: unexpected mint prime {prime!r}")
            mints[prime] = amount
        elif stream.startswith("subproxy:"):
            prime = stream.split(":", 1)[1]
            if prime not in _ALL_PRIMES:
                raise ValueError(f"sky_total: unexpected subproxy prime {prime!r}")
            subs[prime] = amount
        elif stream == "dsb":
            dsb = amount
        elif stream == "cc":
            cc = amount
        else:
            raise ValueError(f"sky_total: unknown stream {stream!r}")

    label = f"{month.year}-{month.month:02d}"

    # Grove TGE penalty: config override per month.
    penalty_map = config.get("grove_tge_penalty") or {}
    if label in penalty_map and penalty_map[label] is not None:
        penalty = Decimal(penalty_map[label])
        penalty_source = f"config:{label}"
    else:
        penalty = Decimal(0)
        penalty_source = "unset"
        warnings.append(
            f"grove_tge_penalty: no override for {label} in config/sky_total.yaml — "
            "booked $0. The methodology doc's §3 line was 1,396,260 for 2026-06; "
            "back-fill earlier months from the corresponding MSC forum posts."
        )

    # One-off subproxy exclusions (initial capital seeding, etc.).
    one_off_map = (config.get("one_off_transfers") or {}).get(label) or {}
    one_off_per_prime: dict[str, Decimal] = {p: Decimal(0) for p in _ALL_PRIMES}
    for prime, amt in one_off_map.items():
        if prime not in _ALL_PRIMES:
            raise ValueError(f"sky_total: one_off_transfers[{label}] has unknown prime {prime!r}")
        d = Decimal(str(amt))
        if d > subs[prime]:
            raise ValueError(
                f"sky_total {label}: one_off_transfers[{prime}] = {d} exceeds "
                f"the on-chain settlement-block mint to that subproxy ({subs[prime]}). "
                "Cross-check the config value against the on-chain settlement block."
            )
        one_off_per_prime[prime] = d
        warnings.append(
            f"one_off_transfers: excluding {d:,.2f} USDS from '{prime}' subproxy "
            f"(config/sky_total.yaml → one_off_transfers[{label}][{prime}])"
        )

    # Non-MSC inputs.
    inc, exp, non_msc_warns = _load_non_msc(repo_root, month)
    for w in non_msc_warns:
        warnings.append(f"non_msc: {w}")

    # Precompute the sky_net_revenue / cc_genesis so the guard warning is
    # part of the warnings list BEFORE we instantiate — keeps SkyTotalMonthly
    # frozen at construction and avoids any post-hoc mutation.
    _snr, _cc_genesis = _derived_sky_net_and_cc_genesis(
        total_mint=sum(mints.values(), Decimal(0)),
        total_subproxy_raw=sum(subs.values(), Decimal(0)),
        dsb=dsb,
        cc_gross=cc,
        grove_tge_penalty=penalty,
        non_msc_net=inc - exp,
    )
    if _cc_genesis < 0:
        # Sanity guard: cc_genesis is what's left of the on-chain CC transfer
        # after carving out the algebraic 20% Step 1 Capital slice. It should
        # be non-negative — if it's not, either (a) the 20% ratio didn't
        # apply in this cycle (e.g. pre-methodology-change), or (b) an
        # unmodeled outflow is inflating SNR (mint side too high, or an
        # outflow we're missing).
        warnings.append(
            f"cc_genesis_repayment is NEGATIVE ({_cc_genesis:,.2f}) — "
            "the 20% Step 1 Capital rule (doc §3) doesn't hold for this cycle, or "
            "an outflow is unmodeled. Cross-check against BA's forum figure for "
            f"MSC#{label} before treating this month's Sky Net Revenue as "
            "reconciled."
        )

    for w in warnings:
        _log.warning("sky_total %s: %s", label, w)

    return SkyTotalMonthly(
        month=label,
        settlement_block=settlement_block,
        settlement_ts=settlement_ts,
        mint_per_prime=mints,
        subproxy_raw_per_prime=subs,
        one_off_per_prime=one_off_per_prime,
        dsb=dsb,
        cc_gross=cc,
        grove_tge_penalty=penalty,
        grove_tge_penalty_source=penalty_source,
        non_msc_income=inc,
        non_msc_expense=exp,
        warnings=warnings,
    )


def _derived_sky_net_and_cc_genesis(
    total_mint: Decimal,
    total_subproxy_raw: Decimal,
    dsb: Decimal,
    cc_gross: Decimal,
    grove_tge_penalty: Decimal,
    non_msc_net: Decimal,
) -> tuple[Decimal, Decimal]:
    """Pure form of the algebraic derivation, matching the ``SkyTotalMonthly``
    properties. Extracted so ``compute_sky_total_monthly`` can compute the
    warning-triggering ``cc_genesis`` before instantiation.
    """
    numerator = (
        total_mint - total_subproxy_raw - dsb - cc_gross - grove_tge_penalty + non_msc_net
    )
    snr = numerator / (Decimal(1) - _STEP1_CAPITAL_RATIO)
    cc_genesis = cc_gross - _STEP1_CAPITAL_RATIO * snr
    return snr, cc_genesis


# ── artifacts ────────────────────────────────────────────────────────────────

def _usds(x: Decimal) -> str:
    d = Decimal(x)
    if d.quantize(Decimal("0.01")) == 0:
        return "0.00"
    return f"-{-d:,.2f}" if d < 0 else f"{d:,.2f}"


def render_summary(r: SkyTotalMonthly) -> str:
    L: list[str] = []
    L.append(f"# SKY_TOTAL — {r.month}")
    L.append("")
    if r.settlement_block == 0:
        anchor_txt = (
            "No MSC settlement transaction executed in this calendar month "
            "(execution-month bucketing: each month carries the settlement "
            "that EXECUTED in it — the prior month's cycle), so the MSC leg "
            "is zero. MSC net = Σ "
        )
    else:
        settlement_dt = datetime.fromtimestamp(r.settlement_ts, tz=timezone.utc)
        anchor_txt = (
            f"Extracted from the MSC settlement block "
            f"**{r.settlement_block}** ({settlement_dt:%Y-%m-%d %H:%M UTC}) — "
            f"the single atomic settlement transaction executed in this "
            f"month (execution-month bucketing, aligned with Block "
            f"Analitica's P&L from 2026-08-05: month M carries cycle M−1's "
            f"settlement). MSC net = Σ "
        )
    L.append(
        f"Consolidated Sky Net Revenue, buffer basis (methodology handoff "
        f"2026-07-16 §3). {anchor_txt}"
        f"debt minted to buffer per prime − Σ sent to prime subproxies − sent "
        f"to Demand-side Buffer − sent to Core Council (genesis portion) − "
        f"Grove TGE penalty. The Core Council on-chain mint is GROSS; the "
        f"20% Step 1 Capital distribution is carved out algebraically from "
        f"Sky Net Revenue."
    )
    L.append("")

    L.append("## MSC leg (buffer basis)")
    L.append("")
    L.append("| Section | Line | USDS |")
    L.append("|---|---|---:|")
    for prime in _MINT_PRIMES:
        L.append(f"| Debt minted to buffer | {prime} | {_usds(r.mint_per_prime[prime])} |")
    L.append(f"| Debt minted to buffer | **subtotal** | **{_usds(r.total_mint)}** |")
    # Buffer-basis formula uses RAW subproxy sends. The one-off (initial
    # capital seeding via Vat.suck(vow) — a real draw on Sky's surplus
    # buffer) is a real cost and IS included in the total; the sub-row here
    # exposes what portion of a line is capital-seeding vs recurring revenue
    # distribution for audit.
    for prime in _ALL_PRIMES:
        raw = r.subproxy_raw_per_prime[prime]
        one_off = r.one_off_per_prime.get(prime, Decimal(0))
        L.append(f"| Sent to prime subproxy | {prime} | -{_usds(raw)} |")
        if one_off.quantize(Decimal("0.01")) != 0:
            L.append(
                f"| Sent to prime subproxy | — of which: one-off capital seeding "
                f"(Vat.suck on vow; real cost) | {_usds(one_off)} |"
            )
    L.append(f"| Sent to prime subproxy | **subtotal (raw)** | **-{_usds(r.total_subproxy_raw)}** |")
    L.append(f"| Sent to Demand-side Buffer |  | -{_usds(r.dsb)} |")
    L.append(f"| Sent to Core Council | on-chain gross | -{_usds(r.cc_gross)} |")
    L.append(f"| Sent to Core Council | of which: Step 1 Capital (20% × SNR, add-back) | +{_usds(r.cc_step1_capital)} |")
    # Guard against the `--<value>` double-minus that appears when
    # cc_genesis_repayment goes negative (the 20% rule doesn't hold for the
    # cycle — a warning is also surfaced below).
    if r.cc_genesis_repayment >= 0:
        L.append(f"| Sent to Core Council | of which: **genesis repayment (net cost)** | **-{_usds(r.cc_genesis_repayment)}** |")
    else:
        L.append(f"| Sent to Core Council | of which: **genesis repayment (NEGATIVE — see warning)** | **{_usds(r.cc_genesis_repayment)}** |")
    L.append(
        f"| Grove TGE penalty (excluded from Sky revenue) | {r.grove_tge_penalty_source} "
        f"| -{_usds(r.grove_tge_penalty)} |"
    )
    L.append(f"| **MSC net (buffer basis)** | | **{_usds(r.msc_net)}** |")
    L.append("")

    L.append("## Non-MSC leg")
    L.append("")
    L.append("| Line | USDS |")
    L.append("|---|---:|")
    L.append(f"| non-MSC income | {_usds(r.non_msc_income)} |")
    L.append(f"| non-MSC expense | -{_usds(r.non_msc_expense)} |")
    L.append(f"| **non-MSC net** | **{_usds(r.non_msc_net)}** |")
    L.append("")

    L.append("## Sky Net Revenue")
    L.append("")
    L.append("| Field | USDS |")
    L.append("|---|---:|")
    L.append(f"| MSC net (buffer basis) | {_usds(r.msc_net)} |")
    L.append(f"| non-MSC net | {_usds(r.non_msc_net)} |")
    L.append(f"| **Sky Net Revenue** | **{_usds(r.sky_net_revenue)}** |")
    L.append("")

    for w in r.warnings:
        L.append(f"> ⚠ {w}")
    if r.warnings:
        L.append("")
    return "\n".join(L)


def write_sky_total(r: SkyTotalMonthly, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prov = {
        "id": "sky_total",
        "month": r.month,
        "settlement_block": r.settlement_block,
        "settlement_ts": r.settlement_ts,
        "results": {
            "mint_per_prime": {k: str(v) for k, v in r.mint_per_prime.items()},
            "total_mint": str(r.total_mint),
            "subproxy_raw_per_prime": {k: str(v) for k, v in r.subproxy_raw_per_prime.items()},
            "one_off_per_prime": {k: str(v) for k, v in r.one_off_per_prime.items()},
            "subproxy_adjusted_per_prime": {k: str(v) for k, v in r.subproxy_adjusted_per_prime.items()},
            "total_subproxy_raw": str(r.total_subproxy_raw),
            "total_one_off": str(r.total_one_off),
            "total_subproxy_adjusted": str(r.total_subproxy_adjusted),
            "dsb": str(r.dsb),
            "cc_gross": str(r.cc_gross),
            "cc_step1_capital": str(r.cc_step1_capital),
            "cc_genesis_repayment": str(r.cc_genesis_repayment),
            "grove_tge_penalty": str(r.grove_tge_penalty),
            "grove_tge_penalty_source": r.grove_tge_penalty_source,
            "msc_net": str(r.msc_net),
            "non_msc_income": str(r.non_msc_income),
            "non_msc_expense": str(r.non_msc_expense),
            "non_msc_net": str(r.non_msc_net),
            "sky_net_revenue": str(r.sky_net_revenue),
        },
        "warnings": r.warnings,
    }
    (out_dir / "provenance.json").write_text(json.dumps(prov, indent=2) + "\n")
    (out_dir / "summary.md").write_text(render_summary(r))
    return {
        "provenance": out_dir / "provenance.json",
        "summary": out_dir / "summary.md",
    }
