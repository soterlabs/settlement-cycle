"""Consolidated Sky Net Revenue — MSC leg (buffer basis) + non-MSC.

SNR is defined to MATCH Block Analitica's "Net revenue" dashboard line
(operator decision 2026-08-06), extracted from the settlement blocks that
executed in the month (see ``hypersync_msc_buffer``):

    MSC net (buffer basis) = Σ debt minted to buffer per prime
                           − Σ sent to prime subproxies (NET of seedings)
    non-MSC net            = non-MSC income − non-MSC expense
                           − Demand-side Buffer transfer (Operating, per BA)
    Sky Net Revenue        = MSC net + non-MSC net

Below the line (BA's ``revenue_distribution`` + reserves view):

    remitted to Sky reserves = SNR
                             − CC Buffer transfer  (= "Security and
                               Maintenance": Step-1 distribution +
                               genesis/expense repayments)
                             − capital seedings
                             [− buybacks, Aligned Delegates, GAR — BA
                               dashboard items this pipeline doesn't track]

Two items that deliberately do NOT reduce SNR:

* **The Core Council Buffer transfer** (``cc_gross``) — the Step 1 Capital
  distribution (20% of the cycle month's net revenue, split evenly Core
  Council / Fortification per Atlas A.2.3.1) plus occasional
  genesis/expense repayments. Repayments are pass-throughs: Sky collects
  them from the prime above the line (inside mint − subproxy) and forwards
  them below the line. The decomposition uses the PAID Step-1 figure from
  each MSC post's BA capital-allocations section (``config/sky_total.yaml
  → cc_step1_paid``). Verified: MSC#5–#9 transfers are PURE Step-1;
  MSC#10 carries +635,130 (Grove's genesis-expense repayment); MSC#4
  carries +787,083 (composition open — SNR-neutral).
* **The Grove TGE penalty** — income Sky retains, already inside
  mint − subproxy. Rendered informationally from the per-month config
  override (only MSC#10 settled one as its own line; earlier penalties
  were netted inside the DV payment).

**One-off subproxy inflows** (initial capital seeding: Skybase's $10M in
MSC#4, Keel's and Osero/PRYSM's $10M each in MSC#6) are read from
``config/sky_total.yaml``. On-chain trace (tx 0xe5a95157… / 0xbebdd875…)
shows the $10M came from ``Vat.suck(u=vow, v=<intermediate>, rad=10M×RAD)``
— a direct draw on Sky's surplus buffer, NOT from the allocator ilks' GRAB
dart. **Classification (operator decision 2026-08-05, following BA):
capital seedings do NOT reduce Sky Net Revenue.** They are balance-sheet
capital allocations that sit BELOW net revenue, reducing only what is
ultimately *remitted to Sky reserves* (BA dashboard line) — alongside the
Step-1 Capital distribution, buybacks, Aligned Delegates, and GAR. The
formula therefore uses subproxy sends NET of the configured one-offs; the
summary renders the seeding in a below-the-line section so the
net-revenue vs remitted-to-reserves distinction stays visible.
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
    settlement_block: int          # latest settlement block of the month (0 = none)
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
    # Step 1 Capital actually paid to the CC Buffer this month (from the MSC
    # post's BA capital-allocations section, via config cc_step1_paid).
    # $0 when the month has no settlement or the figure isn't back-filled
    # yet (the latter also fires a warning).
    cc_step1_paid: Decimal = Decimal(0)
    # Every settlement executed in the month (ascending; empty = none).
    # More than one entry when a month carried multiple settlements
    # (2026-03: MSC#5 executed Mar 2 + MSC#6 executed Mar 30).
    settlement_blocks: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def subproxy_adjusted_per_prime(self) -> dict[str, Decimal]:
        """Raw settlement-block mint − configured one-off exclusions. This
        IS what feeds the buffer-basis formula (capital seedings sit below
        net revenue — see the module docstring); the summary renders both
        figures so a reader sees the revenue-distribution vs
        capital-seeding split."""
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
        """Non-MSC net INCLUDING the DSB transfer, which is classified as an
        Operating expense to mirror Block Analitica's P&L (operator decision
        2026-08-05) even though it is paid inside the settlement tx. SNR is
        unchanged — only the MSC / non-MSC split moves."""
        return self.non_msc_income - self.non_msc_expense - self.dsb

    @property
    def sky_net_revenue(self) -> Decimal:
        """Paid-basis derivation matching BA's "Net revenue" line (operator
        decision 2026-08-06). Above the line there are only three moves:
        mints, subproxy sends net of capital seedings, and the non-MSC leg
        (which carries the DSB as an Operating expense). Everything the
        settlement tx routes to the Core Council Buffer — the Step-1
        distribution AND any genesis/expense repayments — sits BELOW the
        line ("Security and Maintenance" on BA's dashboard), and the Grove
        TGE penalty is income Sky retains (already inside mint − subproxy),
        never a deduction."""
        # NB: uses the raw non-MSC pipeline figures, not the ``non_msc_net``
        # property (which folds the DSB in for display) — the DSB is already
        # subtracted on its own line here.
        return (
            self.total_mint
            - self.total_subproxy_adjusted
            - self.dsb
            + (self.non_msc_income - self.non_msc_expense)
        )

    @property
    def remitted_to_reserves_known(self) -> Decimal:
        """Below-the-line view (partial): Sky Net Revenue − the full CC
        Buffer transfer (Step-1 distribution + genesis/expense repayments =
        BA's "Security and Maintenance") − capital seedings. BA's dashboard
        "remitted to Sky reserves" additionally deducts buybacks ("Revenue
        Allocation"), the Aligned Delegates Buffer, and GAR, which this
        pipeline does not track — so this is a ceiling, rendered for the
        net-revenue vs remitted distinction, not a reconciled figure."""
        return self.sky_net_revenue - self.cc_gross - self.total_one_off

    @property
    def cc_step1_capital(self) -> Decimal:
        """Step 1 Capital actually paid: 20% of the cycle month's net
        revenue per the MSC post (split evenly CC / Fortification, riding
        the CC Buffer transfer). NOT a cost — Sky distributing its own
        already-earned revenue. Zero in a no-settlement month."""
        return self.cc_step1_paid

    @property
    def cc_genesis_repayment(self) -> Decimal:
        """Portion of the on-chain CC mint that reduces Sky's revenue —
        genesis-capital / expense repayments (e.g. Grove's 635,130 at
        MSC#10). = cc_gross − paid Step-1."""
        return self.cc_gross - self.cc_step1_paid

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
    settlement_blocks: list[int] = []
    mints: dict[str, Decimal] = {p: Decimal(0) for p in _MINT_PRIMES}
    subs: dict[str, Decimal] = {p: Decimal(0) for p in _ALL_PRIMES}
    dsb = cc = Decimal(0)
    warnings: list[str] = []

    for _, row in df.iterrows():
        stream = row["stream"]
        if stream == "settlement_block":
            settlement_block = int(row["amount"])
            if settlement_block:
                settlement_blocks.append(settlement_block)
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

    # Grove TGE penalty: config override per month. An explicit ``null``
    # means "confirmed: no separately-settled penalty this month" (operator
    # 2026-08-05: pre-July penalties were netted inside the DV payment, not
    # settled as their own line — do NOT back-fill). Only a MISSING key
    # warns, so future months surface until confirmed either way.
    penalty_map = config.get("grove_tge_penalty") or {}
    if label in penalty_map:
        penalty = Decimal(penalty_map[label] or 0)
        penalty_source = f"config:{label}" if penalty_map[label] is not None else "config:none"
    else:
        penalty = Decimal(0)
        penalty_source = "unset"
        warnings.append(
            f"grove_tge_penalty: no entry for {label} in config/sky_total.yaml — "
            "booked $0. Add the month's figure from the MSC post if a penalty "
            "was settled as its own line (like MSC#10's 1,396,260), or an "
            "explicit null if none / netted inside the DV payment."
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

    # Step 1 Capital actually paid (from the MSC post's BA
    # capital-allocations section) — the paid figure the CC transfer
    # decomposes against BELOW the line (SNR is unaffected either way).
    # Missing entry on a settlement month ⇒ the decomposition shows the
    # full transfer as genesis/repayments, with a warning.
    step1_map = config.get("cc_step1_paid") or {}
    if label in step1_map and step1_map[label] is not None:
        step1 = Decimal(str(step1_map[label]))
    else:
        step1 = Decimal(0)
        if cc > 0:
            warnings.append(
                f"cc_step1_paid: no entry for {label} in config/sky_total.yaml — "
                f"the below-the-line decomposition shows the full Core Council "
                f"transfer ({cc:,.2f}) as genesis/repayments. SNR is unaffected; "
                "back-fill the paid Step-1 figure from the MSC post's BA "
                "capital-allocations section (20% of the cycle month's net "
                "revenue)."
            )

    _cc_genesis = cc - step1
    if _cc_genesis < 0:
        # The paid Step-1 figure exceeds the on-chain CC transfer — either
        # the config value is wrong or part of Step-1 was paid elsewhere.
        warnings.append(
            f"cc_genesis_repayment is NEGATIVE ({_cc_genesis:,.2f}) — "
            f"cc_step1_paid[{label}] exceeds the on-chain CC transfer. "
            "Cross-check the config value against the MSC post and the "
            "settlement tx."
        )

    for w in warnings:
        _log.warning("sky_total %s: %s", label, w)

    return SkyTotalMonthly(
        month=label,
        settlement_block=settlement_block,
        settlement_ts=settlement_ts,
        settlement_blocks=sorted(settlement_blocks),
        mint_per_prime=mints,
        subproxy_raw_per_prime=subs,
        one_off_per_prime=one_off_per_prime,
        dsb=dsb,
        cc_gross=cc,
        grove_tge_penalty=penalty,
        grove_tge_penalty_source=penalty_source,
        non_msc_income=inc,
        non_msc_expense=exp,
        cc_step1_paid=step1,
        warnings=warnings,
    )


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
    elif len(r.settlement_blocks) > 1:
        blocks_txt = ", ".join(f"**{b}**" for b in r.settlement_blocks)
        anchor_txt = (
            f"Extracted from the {len(r.settlement_blocks)} MSC settlement "
            f"blocks {blocks_txt} — every settlement transaction executed in "
            f"this calendar month, components summed (execution-month "
            f"bucketing, aligned with Block Analitica's P&L from "
            f"2026-08-05). MSC net = Σ "
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
        f"Consolidated Sky Net Revenue, buffer basis, defined to match "
        f"Block Analitica's \"Net revenue\" dashboard line (operator "
        f"decision 2026-08-06). {anchor_txt}"
        f"debt minted to buffer per prime − Σ sent to prime subproxies "
        f"(net of capital seedings). The Demand-side Buffer transfer is "
        f"paid inside the settlement tx but classified under the non-MSC "
        f"leg as an Operating expense. The FULL Core Council Buffer "
        f"transfer (Step 1 Capital distribution + genesis/expense "
        f"repayments = BA's \"Security and Maintenance\") sits BELOW net "
        f"revenue, and the Grove TGE penalty is income Sky retains "
        f"(already inside mint − subproxy) — neither reduces SNR."
    )
    L.append("")

    L.append("## MSC leg (buffer basis)")
    L.append("")
    L.append("| Section | Line | USDS |")
    L.append("|---|---|---:|")
    for prime in _MINT_PRIMES:
        L.append(f"| Debt minted to buffer | {prime} | {_usds(r.mint_per_prime[prime])} |")
    L.append(f"| Debt minted to buffer | **subtotal** | **{_usds(r.total_mint)}** |")
    # Buffer-basis formula uses subproxy sends NET of one-off capital
    # seedings — seedings sit below net revenue (BA's remitted-to-reserves
    # treatment, operator decision 2026-08-05). The sub-row exposes the
    # excluded seeding portion for audit.
    for prime in _ALL_PRIMES:
        raw = r.subproxy_raw_per_prime[prime]
        adj = r.subproxy_adjusted_per_prime[prime]
        one_off = r.one_off_per_prime.get(prime, Decimal(0))
        L.append(f"| Sent to prime subproxy | {prime} | -{_usds(adj)} |")
        if one_off.quantize(Decimal("0.01")) != 0:
            L.append(
                f"| Sent to prime subproxy | — excluded: one-off capital seeding "
                f"(below the line; on-chain send was {_usds(raw)}) | ({_usds(one_off)}) |"
            )
    L.append(f"| Sent to prime subproxy | **subtotal (net of seedings)** | **-{_usds(r.total_subproxy_adjusted)}** |")
    if r.grove_tge_penalty.quantize(Decimal("0.01")) != 0:
        # Informational only — the penalty is income Sky retains (it is
        # already inside mint − subproxy) and does NOT reduce SNR.
        L.append(
            f"| Grove TGE penalty (income retained in SNR — informational) | "
            f"{r.grove_tge_penalty_source} | ({_usds(r.grove_tge_penalty)}) |"
        )
    L.append(f"| **MSC net (buffer basis)** | | **{_usds(r.msc_net)}** |")
    L.append("")

    L.append("## Non-MSC leg")
    L.append("")
    L.append("| Line | USDS |")
    L.append("|---|---:|")
    L.append(f"| non-MSC income | {_usds(r.non_msc_income)} |")
    L.append(f"| non-MSC expense | -{_usds(r.non_msc_expense)} |")
    # Paid inside the settlement tx, but classified as an Operating expense
    # to mirror BA's P&L (operator decision 2026-08-05).
    L.append(f"| Demand-side Buffer transfer (Operating, per BA classification) | -{_usds(r.dsb)} |")
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

    # Below the line — net revenue vs remitted-to-reserves distinction
    # (BA dashboard). Only the items this pipeline tracks; BA additionally
    # deducts buybacks ("Revenue Allocation"), the Aligned Delegates
    # Buffer, and GAR.
    # NB: explicit != 0 on both operands — `(a or b).quantize(...)` would
    # short-circuit on a truthy-but-sub-cent cc_gross and hide the section
    # even when seedings are in the millions.
    if r.cc_gross != 0 or r.total_one_off != 0:
        L.append("## Below the line (toward \"remitted to Sky reserves\")")
        L.append("")
        L.append("| Field | USDS |")
        L.append("|---|---:|")
        L.append(f"| Sky Net Revenue | {_usds(r.sky_net_revenue)} |")
        L.append(
            f"| − Core Council Buffer transfer (BA: \"Security and "
            f"Maintenance\") | -{_usds(r.cc_gross)} |"
        )
        L.append(
            f"| &nbsp;&nbsp;of which: Step 1 Capital distribution "
            f"(20% of cycle-month net, paid per MSC post) | -{_usds(r.cc_step1_capital)} |"
        )
        if r.cc_genesis_repayment >= 0:
            L.append(
                f"| &nbsp;&nbsp;of which: genesis / expense repayments | "
                f"-{_usds(r.cc_genesis_repayment)} |"
            )
        else:
            L.append(
                f"| &nbsp;&nbsp;of which: genesis / expense repayments "
                f"(NEGATIVE — see warning) | {_usds(r.cc_genesis_repayment)} |"
            )
        L.append(f"| − capital seedings (one-off subproxy endowments) | -{_usds(r.total_one_off)} |")
        L.append(
            f"| **remitted to Sky reserves (known items only)** | "
            f"**{_usds(r.remitted_to_reserves_known)}** |"
        )
        L.append(
            "\n*BA's dashboard line additionally deducts buybacks (\"Revenue "
            "Allocation\"), the Aligned Delegates Buffer, and GAR (not "
            "tracked here).*"
        )
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
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "month": r.month,
        "settlement_block": r.settlement_block,
        "settlement_blocks": r.settlement_blocks,
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
            # Paid figure from the MSC post's BA capital-allocations section
            # (config cc_step1_paid) — not derived.
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
