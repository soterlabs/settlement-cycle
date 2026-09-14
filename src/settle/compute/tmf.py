"""Treasury Management Function (TMF) — the monthly waterfall that turns Sky
Net Revenue into Smart Burn Engine (SBE) parameters and staking rewards.

Methodology (Sky Atlas A.2.3 + BA Labs' "TMF Configurations", forum t/28153;
validated against the operator's TMF Calculator sheet and the 2026-08-13
executive):

    Step 0  SNR                      — the cycle month's Sky Net Revenue
    Step 1  20% security/maintenance — 10% Core Council + 10% Fortification
    Step 2  backstop retention       — 50% of the remainder while Sky Reserves
                                       (Aggregate Backstop Capital, ABC) sit
                                       below the Turbo-Fill Floor; tapering to
                                       0% at the Target Backstop Capital (TBC)
    Step 3  SBE budget               — split 45% buy-SKY-for-stakers /
                                       45% USDS-for-stakers / 10% buy-and-burn.
                                       kbump is held fixed; hop is SOLVED so the
                                       engine spends the budget over a 365/12-day
                                       month; splitter.burn = 55% (both buying legs)
    Step 4  staking rewards          — SKY rewards = 45% leg ÷ prior-month TWAP;
                                       the treasury fronts them through a 90-day
                                       vest sized for 3 months (vestTot). The
                                       10/55 share of SKY actually bought under
                                       the 55% regime is burned next spell.

This module is pure: it takes decoded on-chain activity / state and the
month's pinned inputs, and returns numbers plus a rendered summary. Extraction
lives in ``normalize/sources/hypersync_sbe.py`` and ``extract/tmf_state.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from ..domain.tmf import SbeActivity, SbeDistribution, SbeKick, SbeParamChange
from .snr_artifact import read_sky_total_snr

__all__ = [
    "SbeActivity",
    "SbeDistribution",
    "SbeKick",
    "SbeParamChange",
    "SbeRegime",
    "TmfInputs",
    "TmfMonthly",
    "TmfPolicy",
    "TmfWaterfall",
    "attribute_burn",
    "burn_attribution",
    "compute_tmf_monthly",
    "compute_waterfall",
    "read_sky_total_snr",
    "regimes_from_kicks",
    "render_summary",
    "retention_rate",
    "write_tmf",
]

D = Decimal
ZERO = D(0)
ONE = D(1)
SECONDS_PER_DAY = 86_400


def _d(x: Any) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def _round0(x: Decimal) -> Decimal:
    return x.quantize(D(1), rounding=ROUND_HALF_UP)


def _q2(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def parse_ts(v: Any) -> int | None:
    """Unix seconds from an ISO-8601 string (``Z`` or offset) or a datetime —
    PyYAML hands back a ``datetime`` for an unquoted ``2026-08-17T14:02:23Z``
    and a ``str`` for the quoted form; both must work."""
    if v is None:
        return None
    if isinstance(v, int | float):
        return int(v)
    if isinstance(v, datetime):
        dt = v if v.tzinfo else v.replace(tzinfo=UTC)
        return int(dt.timestamp())
    if isinstance(v, date):          # PyYAML: unquoted date-only ``2026-09-10``
        return int(datetime(v.year, v.month, v.day, tzinfo=UTC).timestamp())
    dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


_parse_ts = parse_ts   # internal alias, kept so the module body reads unchanged


# ── policy + inputs ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TmfPolicy:
    """Atlas policy shares + engine constants (config/tmf.yaml → policy)."""

    step1_share: Decimal
    step1_core_council_share: Decimal
    step1_fortification_share: Decimal
    step2_max_retention: Decimal
    turbo_fill_floor: Decimal
    tbc_share_of_supply: Decimal
    step3_sky_rewards_share: Decimal
    step3_usds_rewards_share: Decimal
    step3_burn_share: Decimal
    vest_runway_months: int
    vest_tau_days: int
    engine_month_days: Decimal
    kbump: Decimal
    sky_farm_rewards_duration: int
    beam_max_kbump: Decimal
    beam_min_hop: int
    beam_max_rate_per_year: Decimal
    tmf_effective_from: int   # unix ts of the first TMF cast — earlier buys are legacy

    def __post_init__(self) -> None:
        legs = self.step3_sky_rewards_share + self.step3_usds_rewards_share + self.step3_burn_share
        if legs != ONE:
            raise ValueError(f"Step 3 legs must sum to 1, got {legs}")
        if self.step1_core_council_share + self.step1_fortification_share != self.step1_share:
            raise ValueError("Step 1 halves must sum to step1_share")

    @property
    def splitter_burn(self) -> Decimal:
        """On-chain ``splitter.burn`` — the two SKY-buying legs combined."""
        return self.step3_sky_rewards_share + self.step3_burn_share

    @property
    def burn_share_of_buys(self) -> Decimal:
        """Share of every SKY buy that is destined for the burn (10/55)."""
        return self.step3_burn_share / self.splitter_burn

    @property
    def vest_tau_seconds(self) -> int:
        return self.vest_tau_days * SECONDS_PER_DAY

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> TmfPolicy:
        p = cfg["policy"]
        return cls(
            step1_share=_d(p["step1_share"]),
            step1_core_council_share=_d(p["step1_core_council_share"]),
            step1_fortification_share=_d(p["step1_fortification_share"]),
            step2_max_retention=_d(p["step2_max_retention"]),
            turbo_fill_floor=_d(p["turbo_fill_floor"]),
            tbc_share_of_supply=_d(p["tbc_share_of_supply"]),
            step3_sky_rewards_share=_d(p["step3_sky_rewards_share"]),
            step3_usds_rewards_share=_d(p["step3_usds_rewards_share"]),
            step3_burn_share=_d(p["step3_burn_share"]),
            vest_runway_months=int(p["vest_runway_months"]),
            vest_tau_days=int(p["vest_tau_days"]),
            engine_month_days=_d(p["engine_month_days"]),
            kbump=_d(p["kbump"]),
            sky_farm_rewards_duration=int(p["sky_farm_rewards_duration"]),
            beam_max_kbump=_d(p["beam_max_kbump"]),
            beam_min_hop=int(p["beam_min_hop"]),
            beam_max_rate_per_year=_d(p["beam_max_rate_per_year"]),
            tmf_effective_from=_parse_ts(p["tmf_effective_from"]) or 0,
        )


@dataclass(frozen=True)
class TmfInputs:
    """The month's Step-0 inputs — the only numbers that change month to month."""

    month: str
    snr: Decimal                         # USDS — Sky Net Revenue of the cycle month
    sky_twap: Decimal                    # USDS per SKY — the month's TWAP
    aggregate_backstop_capital: Decimal  # USDS — "Sky Reserves"
    usds_supply: Decimal                 # USDS — sets the Target Backstop Capital


# ── Steps 1-4 ────────────────────────────────────────────────────────────────


def retention_rate(
    abc: Decimal, *, floor: Decimal, tbc: Decimal, max_rate: Decimal
) -> Decimal:
    """Step 2 retention: ``max_rate`` below the Turbo-Fill Floor, 0 at/above the
    Target Backstop Capital, ``max_rate x (1 - ABC/TBC)`` in between."""
    if abc < floor:
        return max_rate
    if tbc <= ZERO or abc >= tbc:
        return ZERO
    return max_rate * (ONE - abc / tbc)


@dataclass(frozen=True)
class TmfWaterfall:
    # Step 1
    step1_total: Decimal
    step1_core_council: Decimal
    step1_fortification: Decimal
    after_step1: Decimal
    # Step 2
    target_backstop_capital: Decimal
    retention_rate: Decimal
    step2_retained: Decimal
    step3_capital: Decimal
    # Step 3
    sky_rewards_usds: Decimal
    usds_rewards_usds: Decimal
    burn_usds: Decimal
    buy_usds: Decimal                # the 55% that reaches the Flapper
    splitter_burn: Decimal
    batches_per_month: Decimal
    batches_per_day: Decimal
    hop_solved: Decimal              # seconds, unrounded
    hop: int                         # seconds, rounded — what the spell files
    annual_run_rate: Decimal         # USDS/yr through the engine
    sky_bought_estimate: Decimal     # buy_usds ÷ TWAP
    # Step 4
    sky_rewards_monthly: Decimal     # SKY/month
    vest_tot_exact: Decimal
    vest_tot: Decimal                # SKY, rounded to whole tokens
    stream_rate: Decimal             # SKY/s = vest_tot ÷ tau
    weekly_pull: Decimal             # SKY per 7 d
    # BEAM bounds — each independent, so a breach names the right lever
    hop_within_beam: bool
    kbump_within_beam: bool
    run_rate_within_beam: bool


def compute_waterfall(inputs: TmfInputs, policy: TmfPolicy) -> TmfWaterfall:
    snr = inputs.snr
    step1 = snr * policy.step1_share
    cc = snr * policy.step1_core_council_share
    ff = snr * policy.step1_fortification_share
    after1 = snr - step1

    tbc = inputs.usds_supply * policy.tbc_share_of_supply
    rate = retention_rate(
        inputs.aggregate_backstop_capital,
        floor=policy.turbo_fill_floor, tbc=tbc, max_rate=policy.step2_max_retention,
    )
    retained = after1 * rate
    step3 = after1 - retained

    sky_leg = step3 * policy.step3_sky_rewards_share
    usds_leg = step3 * policy.step3_usds_rewards_share
    burn_leg = step3 * policy.step3_burn_share
    buy = sky_leg + burn_leg

    batches_month = step3 / policy.kbump
    batches_day = batches_month / policy.engine_month_days
    hop_solved = D(SECONDS_PER_DAY) / batches_day if batches_day > 0 else ZERO
    hop = int(_round0(hop_solved)) if hop_solved > 0 else 0
    annual = step3 * 12

    sky_bought_est = buy / inputs.sky_twap
    sky_rewards = sky_leg / inputs.sky_twap
    vest_exact = sky_rewards * policy.vest_runway_months
    vest_tot = _round0(vest_exact)
    stream_rate = vest_tot / D(policy.vest_tau_seconds)
    weekly = stream_rate * policy.sky_farm_rewards_duration

    return TmfWaterfall(
        step1_total=step1, step1_core_council=cc, step1_fortification=ff,
        after_step1=after1,
        target_backstop_capital=tbc, retention_rate=rate, step2_retained=retained,
        step3_capital=step3,
        sky_rewards_usds=sky_leg, usds_rewards_usds=usds_leg, burn_usds=burn_leg,
        buy_usds=buy, splitter_burn=policy.splitter_burn,
        batches_per_month=batches_month, batches_per_day=batches_day,
        hop_solved=hop_solved, hop=hop, annual_run_rate=annual,
        sky_bought_estimate=sky_bought_est,
        sky_rewards_monthly=sky_rewards, vest_tot_exact=vest_exact, vest_tot=vest_tot,
        stream_rate=stream_rate, weekly_pull=weekly,
        hop_within_beam=(hop >= policy.beam_min_hop),
        kbump_within_beam=(policy.kbump <= policy.beam_max_kbump),
        run_rate_within_beam=(annual <= policy.beam_max_rate_per_year),
    )


def burn_attribution(
    sky_bought: Decimal, policy: TmfPolicy, *, burn: Decimal | None = None
) -> tuple[Decimal, Decimal]:
    """Split SKY bought under a TMF regime into (to stakers, to burn).

    ``burn`` is the ``splitter.burn`` the buys executed under (default: the
    policy's 55%); the burn leg is ``step3_burn_share / burn`` of every buy
    (10/55 today), the rest is the stakers' leg."""
    b = policy.splitter_burn if burn is None else burn
    if b <= 0:
        return sky_bought, ZERO
    if policy.step3_burn_share > b:
        raise ValueError(
            f"burn_attribution: regime splitter.burn {b} is below the policy burn leg "
            f"{policy.step3_burn_share} — the Step 3 split changed; add a dated policy "
            "(see docs/tmf/README.md, open question 1) instead of re-attributing with "
            "today's shares"
        )
    to_burn = sky_bought * (policy.step3_burn_share / b)
    return sky_bought - to_burn, to_burn


def attribute_burn(kicks: list[SbeKick], policy: TmfPolicy) -> tuple[list[SbeKick], Decimal, Decimal]:
    """Burn attribution over a month's kicks.

    Buys executed before ``policy.tmf_effective_from`` (the legacy 100%-burn
    engine, SKY parked in the treasury) are not attributed. Every later kick
    is split with ITS OWN regime's ``burn`` — so a governance change of the
    Step 3 split mid-month, or a re-run after a later change, attributes each
    regime correctly instead of keying off one global constant.

    Returns ``(window_kicks, to_stakers, to_burn)``."""
    # A kick that lands in the cast block BEFORE the spell's File(burn) log
    # still carries the legacy burn (1.0) at the effective timestamp — it is a
    # legacy buy, not a TMF one, so it is excluded on (ts, burn), not ts alone.
    window = [
        k for k in kicks
        if k.ts >= policy.tmf_effective_from
        and not (k.ts == policy.tmf_effective_from and k.burn >= ONE)
    ]
    to_stakers = ZERO
    to_burn = ZERO
    for k in window:
        s, b = burn_attribution(k.bought, policy, burn=k.burn)
        to_stakers += s
        to_burn += b
    return window, to_stakers, to_burn


# ── on-chain activity: the Sbe* records live in domain/tmf.py (shared with the
#    HyperSync source, which must not import compute); aggregation is here. ──


@dataclass(frozen=True)
class SbeRegime:
    """Consecutive kicks executed under one (burn, hop) parameter pair."""

    burn: Decimal
    hop: int
    start_ts: int
    end_ts: int
    n_kicks: int
    usds_in: Decimal
    to_flapper: Decimal
    to_farm: Decimal
    sky_bought: Decimal

    @property
    def vwap(self) -> Decimal | None:
        return (self.to_flapper / self.sky_bought) if self.sky_bought > 0 else None


def regimes_from_kicks(kicks: list[SbeKick]) -> list[SbeRegime]:
    out: list[SbeRegime] = []
    cur: list[SbeKick] = []

    def flush() -> None:
        if not cur:
            return
        out.append(SbeRegime(
            burn=cur[0].burn, hop=cur[0].hop,
            start_ts=cur[0].ts, end_ts=cur[-1].ts, n_kicks=len(cur),
            usds_in=sum((k.tot for k in cur), ZERO),
            to_flapper=sum((k.lot for k in cur), ZERO),
            to_farm=sum((k.pay for k in cur), ZERO),
            sky_bought=sum((k.bought for k in cur), ZERO),
        ))

    for k in sorted(kicks, key=lambda k: (k.block, k.log_index)):
        if cur and (k.burn != cur[0].burn or k.hop != cur[0].hop):
            flush()
            cur = []
        cur.append(k)
    flush()
    return out


# ── the monthly report ───────────────────────────────────────────────────────


@dataclass
class TmfMonthly:
    month: str
    msc: str
    inputs: TmfInputs
    policy: TmfPolicy
    waterfall: TmfWaterfall
    activity: SbeActivity | None
    regimes: list[SbeRegime]
    sky_bought_month: Decimal          # every buy in the month, any regime
    usds_spent_month: Decimal
    sky_bought_burn_window: Decimal    # buys executed while splitter.burn == policy 55%
    kicks_burn_window: int
    usds_spent_burn_window: Decimal
    sky_to_stakers_from_buys: Decimal
    sky_to_burn: Decimal
    state: dict[str, Any] | None       # extract.tmf_state.read_tmf_state at to_block
    spell_state: dict[str, Any] | None # same, at the spell's execution block (when cast)
    published: dict[str, Any]
    pins: dict[str, Any]
    sources: dict[str, str]
    checks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _check(
    r: TmfMonthly, label: str, computed: Decimal | int | None,
    published: Any, *, tol: Decimal | None = None, unit: str = "",
) -> None:
    """Record a computed-vs-published comparison; warn when it misses."""
    if published is None or computed is None:
        return
    if tol is None:
        tol = D("0.01")
    pub = _d(published)
    comp = _d(computed)
    ok = abs(comp - pub) <= tol
    r.checks.append({
        "label": label, "computed": str(comp), "published": str(pub),
        "tolerance": str(tol), "unit": unit, "ok": ok,
    })
    if not ok:
        ref = "on-chain" if label.startswith("on-chain") else "published"
        r.warnings.append(
            f"{label}: computed {comp:,.2f} vs {ref} {pub:,.2f} {unit} "
            f"(Δ {comp - pub:+,.2f}, tolerance ±{tol})"
        )


def compute_tmf_monthly(
    month: str,
    month_cfg: dict[str, Any],
    policy: TmfPolicy,
    *,
    activity: SbeActivity | None,
    state: dict[str, Any] | None,
    spell_state: dict[str, Any] | None = None,
    repo_root: Path | None = None,
    sources: dict[str, str] | None = None,
    pins: dict[str, Any] | None = None,
) -> TmfMonthly:
    warnings: list[str] = []
    src: dict[str, str] = dict(sources or {})

    # ── Step 0: SNR — pinned to the MSC post, cross-checked vs sky_total ──
    artifact = read_sky_total_snr(repo_root, month) if repo_root else None
    artifact_snr = artifact.snr if artifact else None
    if month_cfg.get("snr") is not None:
        snr = _d(month_cfg["snr"])
        src["snr"] = f"config/tmf.yaml months['{month}'].snr (MSC post figure)"
    elif artifact_snr is not None:
        snr = _round0(artifact_snr)
        src["snr"] = f"settlements/sky_total/{month} (rounded to whole USDS)"
    else:
        raise FileNotFoundError(
            f"tmf {month}: no SNR — pin months['{month}'].snr in config/tmf.yaml "
            "or build settlements/sky_total first (scripts/build_sky_total_2026.py)."
        )

    if "sky_twap" not in month_cfg or "sky_reserves" not in month_cfg:
        raise KeyError(
            f"tmf {month}: months['{month}'] needs sky_twap and sky_reserves — "
            "both are published (t/28153) and not derivable on-chain."
        )
    twap = _d(month_cfg["sky_twap"])
    abc = _d(month_cfg["sky_reserves"])
    src.setdefault("sky_twap", "BA Labs observatory TWAP, as quoted in forum t/28153")
    src.setdefault("sky_reserves", "financial.skyeco.com/risk/capital, as quoted in forum t/28153")

    if state is not None and state.get("usds_total_supply") is not None:
        usds_supply = _d(state["usds_total_supply"])
        src["usds_supply"] = f"USDS.totalSupply() at block {state.get('block')}"
    elif month_cfg.get("usds_supply") is not None:
        usds_supply = _d(month_cfg["usds_supply"])
        src["usds_supply"] = f"config/tmf.yaml months['{month}'].usds_supply"
    else:
        raise KeyError(f"tmf {month}: usds_supply needs on-chain state or a config pin")

    inputs = TmfInputs(
        month=month, snr=snr, sky_twap=twap,
        aggregate_backstop_capital=abc, usds_supply=usds_supply,
    )
    wf = compute_waterfall(inputs, policy)

    # ── SBE execution in the month ──
    regimes = regimes_from_kicks(activity.kicks) if activity else []
    sky_month = sum((k.bought for k in activity.kicks), ZERO) if activity else ZERO
    usds_month = sum((k.lot for k in activity.kicks), ZERO) if activity else ZERO
    window, to_stakers, to_burn = attribute_burn(activity.kicks if activity else [], policy)
    sky_window = sum((k.bought for k in window), ZERO)
    usds_window = sum((k.lot for k in window), ZERO)

    pub = dict(month_cfg.get("spell") or {})
    r = TmfMonthly(
        month=month, msc=str(month_cfg.get("msc", "")), inputs=inputs, policy=policy,
        waterfall=wf, activity=activity, regimes=regimes,
        sky_bought_month=sky_month, usds_spent_month=usds_month,
        sky_bought_burn_window=sky_window, kicks_burn_window=len(window),
        usds_spent_burn_window=usds_window,
        sky_to_stakers_from_buys=to_stakers, sky_to_burn=to_burn,
        state=state, spell_state=spell_state, published=pub, pins=dict(pins or {}),
        sources=src, warnings=warnings,
    )

    # ── cross-checks ──
    if artifact_snr is not None:
        _check(r, "Step 0 SNR vs settlements/sky_total", snr, _round0(artifact_snr),
               tol=D(1), unit="USDS")
    elif repo_root is not None:
        warnings.append(
            f"no settlements/sky_total/{month} artifact to cross-check the pinned SNR against"
        )
    _check(r, "Step 3 capital", _q2(wf.step3_capital), pub.get("step3_capital"), unit="USDS")
    _check(r, "USDS staking rewards (45%)", _q2(wf.usds_rewards_usds), pub.get("usds_rewards"),
           unit="USDS")
    _check(r, "SKY staking rewards (45% ÷ TWAP)", _q2(wf.sky_rewards_monthly),
           pub.get("sky_rewards"), unit="SKY")
    _check(r, "splitter.hop", wf.hop, pub.get("hop"), tol=ZERO, unit="s")
    _check(r, "REWARDS_LSSKY_USDS.rewardsDuration", wf.hop, pub.get("rewards_duration_usds"),
           tol=ZERO, unit="s")
    _check(r, "splitter.burn", wf.splitter_burn, pub.get("burn"), tol=ZERO)
    _check(r, "vestTot", wf.vest_tot, pub.get("vest_tot"), tol=ZERO, unit="SKY")
    if pub.get("vest_tau_days") is not None:
        _check(r, "vestTau (days)", policy.vest_tau_days, pub["vest_tau_days"], tol=ZERO, unit="d")
    _check(r, "Core Council Buffer transfer (Step 1)", _round0(wf.step1_total),
           pub.get("core_council_transfer"), tol=D(1), unit="USDS")
    dune = month_cfg.get("dune_8544603") or {}
    if activity is not None:
        # Everything below derives from extracted kicks — without activity
        # the window is trivially empty and comparing 0 to the published burn
        # would be noise, not a finding.
        _check(r, "SKY to burn (10/55 of window buys)", _q2(to_burn), pub.get("sky_burn"),
               tol=D("0.01"), unit="SKY")
        bw = pub.get("burn_window") or {}
        _check(r, "burn-window kicks", len(window), bw.get("kicks"), tol=ZERO)
        _check(r, "burn-window USDS spent", usds_window, bw.get("usds_spent"), unit="USDS")
        _check(r, "burn-window SKY bought", _q2(sky_window), bw.get("sky_bought"), unit="SKY")
        _check(r, "burn-window SKY to stakers (45/55)", _q2(to_stakers),
               bw.get("sky_to_stakers"), unit="SKY")
        _check(r, "SKY bought in month vs Dune 8544603", sky_month, dune.get("sbe_sky_bought"),
               tol=D("0.01"), unit="SKY")
        _check(r, "USDS spent in month vs Dune 8544603", usds_month, dune.get("sbe_usds_spent"),
               unit="USDS")
    if dune.get("sky_twap") is not None:
        # Informational — BA's observatory TWAP vs Dune's minute average.
        dt = _d(dune["sky_twap"])
        r.checks.append({
            "label": "SKY TWAP: BA observatory vs Dune prices.usd minute-avg",
            "computed": str(twap), "published": str(dt), "tolerance": "info",
            "unit": "USDS/SKY", "ok": True,
        })
        rel = (twap - dt) / dt
        if abs(rel) > D("0.005"):
            warnings.append(
                f"TWAP sources differ by {rel:+.3%}: BA {twap} vs Dune {dt} — "
                "the report uses BA's (the governance input)"
            )

    # ── on-chain consistency at the spell's execution block. The cycle-month
    # spell casts in M+1, so the report month's OWN month-end state still
    # shows the previous parameters — only the post-cast snapshot is a valid
    # check of what the spell filed. ──
    # Argument order matters for the rendered columns: ``computed`` is OUR
    # value (the waterfall / policy), ``published`` the reference — here the
    # chain reading — so a ✗ reads the same way as every other row.
    ss = spell_state
    if ss is not None:
        _check(r, "on-chain splitter.hop after cast", wf.hop, int(ss["splitter_hop"]),
               tol=ZERO, unit="s")
        _check(r, "on-chain REWARDS_LSSKY_USDS.rewardsDuration after cast", wf.hop,
               int(ss["usds_farm_rewards_duration"]), tol=ZERO, unit="s")
        _check(r, "on-chain splitter.burn after cast", wf.splitter_burn,
               _d(ss["splitter_burn"]), tol=ZERO)
        _check(r, "on-chain vest.tot(vestId) after cast", wf.vest_tot, _d(ss["vest_tot"]),
               tol=ZERO, unit="SKY")
        _check(r, "on-chain vest tau after cast (fin - bgn)", policy.vest_tau_seconds,
               int(ss["vest_fin"]) - int(ss["vest_bgn"]), tol=ZERO, unit="s")
        if pub.get("vest_id") is not None:
            _check(r, "on-chain distributor vestId after cast", pub["vest_id"],
                   int(ss["dist_vest_id"]), tol=ZERO)
    if state is not None and state.get("vest_cap") is not None:
        if wf.stream_rate > _d(state["vest_cap"]):
            warnings.append(
                f"stream rate {wf.stream_rate:.4f} SKY/s exceeds MCD_VEST_SKY_TREASURY.cap "
                f"{_d(state['vest_cap']):.4f} SKY/s — the vest cannot be created as sized"
            )
    if not wf.hop_within_beam:
        warnings.append(
            f"solved hop {wf.hop}s is below the SBE BEAM minHop {policy.beam_min_hop}s — "
            "needs an executive, not an operator change"
        )
    if not wf.kbump_within_beam:
        warnings.append(
            f"kicker.kbump {policy.kbump:,.0f} USDS exceeds the SBE BEAM maxKbump "
            f"{policy.beam_max_kbump:,.0f} — needs an executive, not an operator change"
        )
    if not wf.run_rate_within_beam:
        warnings.append(
            f"annual run-rate {wf.annual_run_rate:,.0f} USDS/yr exceeds the BEAM maxRate "
            f"{policy.beam_max_rate_per_year:,.0f}"
        )
    if wf.retention_rate != policy.step2_max_retention:
        warnings.append(
            "Step 2 retention is off the 50% tier — the between-Floor-and-TBC formula and "
            "the TBC supply basis (USDS only vs USDS+DAI) are unconfirmed; see docs/tmf/README.md"
        )
    if activity is not None and not window and pub.get("sky_burn") not in (None, "0", 0):
        warnings.append("published SKY burn is non-zero but no kicks ran after the TMF took effect")
    if (pins or {}).get("partial"):
        warnings.append(
            f"PARTIAL MONTH: the archive/chain had not reached {month}'s month-end when this ran — "
            f"activity and state stop at block {(pins or {}).get('to_block')} "
            f"({_ts((pins or {}).get('to_ts'))}); kicks, SKY bought and the burn cover only part "
            "of the month. Re-run after month-end."
        )
    return r


# ── rendering ────────────────────────────────────────────────────────────────


def _usds(x: Decimal | None, places: int = 2) -> str:
    if x is None:
        return "—"
    d = Decimal(x)
    q = Decimal(1).scaleb(-places)
    d = d.quantize(q, rounding=ROUND_HALF_UP)
    if d == 0:
        return f"{0:.{places}f}"
    return f"{d:,.{places}f}"


def _pct(x: Decimal) -> str:
    return f"{Decimal(x) * 100:.2f}%".replace(".00%", "%")


def _ts(ts: int | None) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _month_name(label: str) -> str:
    y, m = label.split("-")
    return datetime(int(y), int(m), 1).strftime("%B %Y")


def render_summary(r: TmfMonthly) -> str:
    wf, p, i = r.waterfall, r.policy, r.inputs
    pub = r.published
    mn = _month_name(r.month)
    L: list[str] = []
    L.append(f"# TMF — {r.month}")
    L.append("")
    L.append(
        f"Treasury Management Function waterfall for the **{mn}** cycle "
        f"({r.msc or 'MSC'}): Sky Net Revenue → Step 1 security & maintenance → "
        f"Step 2 backstop retention → Step 3 Smart Burn Engine budget → Step 4 staking "
        f"rewards, plus the Smart Burn Engine's actual execution in {mn} (kicks, USDS "
        f"spent, SKY bought) and the on-chain parameter state at month-end. Method: Sky "
        f"Atlas A.2.3 + TMF Configurations (forum t/28153); engine month = 365/12 days. "
        f"Pinned inputs are cross-checked against the executive's published parameter "
        f"block below."
    )
    L.append("")
    if pub.get("title"):
        L.append(f"**Spell:** {pub['title']} — {pub.get('status', '')}")
        L.append("")
    if r.pins.get("partial"):
        L.append(f"> ⚠ **PARTIAL MONTH** — data stops at block {r.pins.get('to_block')} "
                 f"({_ts(r.pins.get('to_ts'))}); the month was not closed when this ran.")
        L.append("")

    # ── Inputs ──
    L.append("## ① Inputs (Step 0)")
    L.append("")
    L.append("| Input | Value | Source |")
    L.append("|---|---:|---|")
    L.append(f"| Sky Net Revenue | {_usds(i.snr, 0)} USDS | {r.sources.get('snr', '')} |")
    L.append(f"| SKY monthly TWAP | {i.sky_twap} USDS/SKY | {r.sources.get('sky_twap', '')} |")
    L.append(f"| Aggregate Backstop Capital (Sky Reserves) | {_usds(i.aggregate_backstop_capital)} USDS "
             f"| {r.sources.get('sky_reserves', '')} |")
    L.append(f"| USDS total supply | {_usds(i.usds_supply, 0)} USDS | {r.sources.get('usds_supply', '')} |")
    L.append(f"| kicker.kbump (held fixed) | {_usds(p.kbump, 0)} USDS/batch | config/tmf.yaml policy |")
    L.append("")

    # ── Step 1 ──
    L.append("## Step 1 — Security & maintenance (20%)")
    L.append("")
    L.append("| Line | USDS |")
    L.append("|---|---:|")
    L.append(f"| Total ({_pct(p.step1_share)} of SNR) → Core Council Buffer, one transfer | {_usds(wf.step1_total)} |")
    L.append(f"| &nbsp;&nbsp;Core Council half ({_pct(p.step1_core_council_share)}) | {_usds(wf.step1_core_council)} |")
    L.append(f"| &nbsp;&nbsp;Fortification Foundation half ({_pct(p.step1_fortification_share)}) | {_usds(wf.step1_fortification)} |")
    L.append(f"| **Remaining after Step 1** | **{_usds(wf.after_step1)}** |")
    L.append("")

    # ── Step 2 ──
    L.append("## Step 2 — Aggregate Backstop Capital")
    L.append("")
    L.append("| Line | Value |")
    L.append("|---|---:|")
    L.append(f"| Turbo-Fill Floor | {_usds(p.turbo_fill_floor, 0)} USDS |")
    L.append(f"| Target Backstop Capital ({_pct(p.tbc_share_of_supply)} x USDS supply) | {_usds(wf.target_backstop_capital, 0)} USDS |")
    tier = ("below Floor → full retention" if i.aggregate_backstop_capital < p.turbo_fill_floor
            else "at/above target → 0%" if wf.retention_rate == 0 else "between Floor and target")
    L.append(f"| Retention rate this month | {_pct(wf.retention_rate)} ({tier}) |")
    L.append(f"| Retained (stays in the Surplus Buffer) | {_usds(wf.step2_retained)} USDS |")
    L.append(f"| **Step 3 remainder (engine budget)** | **{_usds(wf.step3_capital)} USDS/month** |")
    L.append("")

    # ── Step 3 ──
    L.append("## Step 3 — Smart Burn Engine")
    L.append("")
    L.append("| Line | Value |")
    L.append("|---|---:|")
    L.append(f"| SKY-rewards leg ({_pct(p.step3_sky_rewards_share)}) — buys SKY for stakers | {_usds(wf.sky_rewards_usds)} USDS |")
    L.append(f"| USDS-rewards leg ({_pct(p.step3_usds_rewards_share)}) — paid to stakers as USDS | {_usds(wf.usds_rewards_usds)} USDS |")
    L.append(f"| Burn leg ({_pct(p.step3_burn_share)}) — buys SKY to burn | {_usds(wf.burn_usds)} USDS |")
    L.append(f"| Total buyback ({_pct(wf.splitter_burn)}) → Flapper | {_usds(wf.buy_usds)} USDS |")
    L.append(f"| Implied batches / month ÷ / day | {wf.batches_per_month:,.2f} ÷ {wf.batches_per_day:,.2f} |")
    L.append(f"| Implied hop (solved, kbump fixed) | {wf.hop_solved:,.2f} s → **{wf.hop:,} s** |")
    L.append(f"| Annual run-rate through the engine | {_usds(wf.annual_run_rate, 0)} USDS/yr |")
    beam = [
        f"kbump ≤ {_usds(p.beam_max_kbump, 0)}: {'ok' if wf.kbump_within_beam else 'BREACH'}",
        f"hop ≥ {p.beam_min_hop} s: {'ok' if wf.hop_within_beam else 'BREACH'}",
        f"≤ {_usds(p.beam_max_rate_per_year, 0)}/yr: {'ok' if wf.run_rate_within_beam else 'BREACH'}",
    ]
    L.append(f"| SBE BEAM bounds | {' · '.join(beam)} |")
    L.append(f"| Bought SKY (model estimate, {_pct(wf.splitter_burn)} leg ÷ TWAP) | {_usds(wf.sky_bought_estimate, 0)} SKY |")
    L.append("")

    # ── Step 4 ──
    L.append("## Step 4 — Staking rewards")
    L.append("")
    L.append("| Line | Value |")
    L.append("|---|---:|")
    L.append(f"| Monthly SKY rewards (45% leg ÷ TWAP) → REWARDS_LSSKY_SKY | {_usds(wf.sky_rewards_monthly)} SKY |")
    L.append(f"| Monthly USDS rewards → REWARDS_LSSKY_USDS (via Splitter, per batch) | {_usds(wf.usds_rewards_usds)} USDS |")
    L.append(f"| vestTot ({p.vest_runway_months} months of SKY rewards, {p.vest_tau_days}-day stream) | {_usds(wf.vest_tot_exact)} → **{_usds(wf.vest_tot, 0)} SKY** |")
    L.append(f"| Stream rate (vestTot ÷ tau) | {wf.stream_rate:,.4f} SKY/s |")
    L.append(f"| Distributor pull per farm period ({p.sky_farm_rewards_duration // SECONDS_PER_DAY} d) ≈ | {_usds(wf.weekly_pull, 0)} SKY |")
    if r.state and r.state.get("vest_cap") is not None:
        cap = _d(r.state["vest_cap"])
        L.append(f"| vs MCD_VEST_SKY_TREASURY.cap {cap:,.2f} SKY/s | {'ok' if wf.stream_rate <= cap else 'EXCEEDS'} |")
    L.append("")

    # ── Parameter block ──
    ss = r.spell_state or {}
    L.append("## ② Parameter block for the spell (computed vs published vs on-chain)")
    L.append("")
    if ss:
        L.append(f"On-chain column read at the spell's execution block {int(ss['block']):,} "
                 f"({_ts(ss.get('ts'))}).")
        L.append("")
    L.append("| Parameter | Computed | Published | On-chain @ cast | |")
    L.append("|---|---:|---:|---:|:-:|")

    def row(label: str, comp: str, key: str, fmt: Callable[[Any], str] = str,
            chain: str = "—", checks: tuple[str, ...] = ()) -> None:
        """One parameter row; the mark folds the EXPLICITLY named cross-checks
        (published + on-chain) so a label mismatch can't render a stale ✓."""
        v = pub.get(key)
        shown = fmt(v) if v is not None else "—"
        oks = [c["ok"] for c in r.checks if c["label"] in (label, *checks)]
        mark = "" if not oks else ("✓" if all(oks) else "✗")
        L.append(f"| {label} | {comp} | {shown} | {chain} | {mark} |")

    row("splitter.hop", f"{wf.hop:,} s", "hop", lambda v: f"{int(v):,} s",
        chain=f"{int(ss['splitter_hop']):,} s" if ss else "—",
        checks=("on-chain splitter.hop after cast",))
    row("REWARDS_LSSKY_USDS.rewardsDuration", f"{wf.hop:,} s", "rewards_duration_usds",
        lambda v: f"{int(v):,} s",
        chain=f"{int(ss['usds_farm_rewards_duration']):,} s" if ss else "—",
        checks=("on-chain REWARDS_LSSKY_USDS.rewardsDuration after cast",))
    row("splitter.burn", _pct(wf.splitter_burn), "burn", lambda v: _pct(_d(v)),
        chain=_pct(_d(ss["splitter_burn"])) if ss else "—",
        checks=("on-chain splitter.burn after cast",))
    L.append(f"| kicker.kbump | {_usds(p.kbump, 0)} USDS | unchanged | "
             f"{_usds(_d(ss['kicker_kbump']), 0) + ' USDS' if ss else '—'} | |")
    row("vestTot", f"{_usds(wf.vest_tot, 0)} SKY", "vest_tot", lambda v: f"{_usds(_d(v), 0)} SKY",
        chain=f"{_usds(_d(ss['vest_tot']), 0)} SKY" if ss else "—",
        checks=("on-chain vest.tot(vestId) after cast",))
    row("vestTau (days)", f"{p.vest_tau_days} d", "vest_tau_days", lambda v: f"{int(v)} d",
        chain=f"{(int(ss['vest_fin']) - int(ss['vest_bgn'])) // SECONDS_PER_DAY} d" if ss else "—",
        checks=("on-chain vest tau after cast (fin - bgn)",))
    cast_ts = _ts(_parse_ts(pub.get("executed_at")))
    L.append(f"| vestBgn | block.timestamp at cast | {cast_ts} | "
             f"{_ts(ss.get('vest_bgn')) if ss else '—'} | |")
    L.append(f"| dist (stream beneficiary) | REWARDS_DIST_LSSKY_SKY | "
             f"{('vestId ' + str(pub['vest_id'])) if pub.get('vest_id') is not None else '—'} | "
             f"{('vestId ' + str(ss['dist_vest_id'])) if ss else '—'} | |")
    row("Core Council Buffer transfer (Step 1)", f"{_usds(wf.step1_total, 0)} USDS",
        "core_council_transfer", lambda v: f"{_usds(_d(v), 0)} USDS")
    row("SKY to burn (10/55 of window buys)", f"{_usds(r.sky_to_burn)} SKY", "sky_burn",
        lambda v: f"{_usds(_d(v))} SKY")
    L.append("")

    # ── Execution ──
    L.append(f"## ③ Smart Burn Engine execution in {mn}")
    L.append("")
    if r.activity is None:
        L.append("*No on-chain activity extracted (run without HyperSync credentials).*")
        L.append("")
    else:
        a = r.activity
        L.append(f"Blocks {a.from_block:,}-{a.to_block:,} ({_ts(a.from_ts)} → {_ts(a.to_ts)}), "
                 f"Splitter `Kick` joined to Flapper `Exec` by transaction.")
        L.append("")
        L.append("| Regime (burn / hop) | From | To | Kicks | USDS in | → Flapper | → USDS farm | SKY bought | VWAP |")
        L.append("|---|---|---|---:|---:|---:|---:|---:|---:|")
        for g in r.regimes:
            vw = f"{g.vwap:.6f}" if g.vwap is not None else "—"
            L.append(f"| {_pct(g.burn)} / {g.hop:,} s | {_ts(g.start_ts)} | {_ts(g.end_ts)} | {g.n_kicks} "
                     f"| {_usds(g.usds_in, 0)} | {_usds(g.to_flapper, 0)} | {_usds(g.to_farm, 0)} "
                     f"| {_usds(g.sky_bought)} | {vw} |")
        tot_in = sum((g.usds_in for g in r.regimes), ZERO)
        tot_farm = sum((g.to_farm for g in r.regimes), ZERO)
        n = sum(g.n_kicks for g in r.regimes)
        vw_all = (r.usds_spent_month / r.sky_bought_month) if r.sky_bought_month > 0 else None
        L.append(f"| **month** | | | **{n}** | **{_usds(tot_in, 0)}** | **{_usds(r.usds_spent_month, 0)}** "
                 f"| **{_usds(tot_farm, 0)}** | **{_usds(r.sky_bought_month)}** "
                 f"| **{f'{vw_all:.6f}' if vw_all is not None else '—'}** |")
        L.append("")
        L.append(f"**Burn attribution** — buys executed from the TMF's first cast "
                 f"({_ts(p.tmf_effective_from)}) are split per the regime they ran under: "
                 f"burn leg = {_pct(p.step3_burn_share)} ÷ that regime's `splitter.burn` "
                 f"({_pct(p.step3_burn_share)}/{_pct(p.splitter_burn)} today), the rest to SKY "
                 "stakers. Earlier buys (legacy 100% engine) went to the treasury un-attributed "
                 "(BA Labs convention, t/28153).")
        L.append("")
        L.append("| Line | Value |")
        L.append("|---|---:|")
        L.append(f"| Kicks since the TMF took effect | {r.kicks_burn_window} |")
        L.append(f"| USDS spent in the window | {_usds(r.usds_spent_burn_window)} USDS |")
        L.append(f"| SKY bought in the window | {_usds(r.sky_bought_burn_window)} SKY |")
        L.append(f"| &nbsp;&nbsp;to SKY stakers ({_pct(p.step3_sky_rewards_share)}/{_pct(p.splitter_burn)}) "
                 f"| {_usds(r.sky_to_stakers_from_buys)} SKY |")
        L.append(f"| &nbsp;&nbsp;**to burn ({_pct(p.step3_burn_share)}/{_pct(p.splitter_burn)})** "
                 f"| **{_usds(r.sky_to_burn)} SKY** |")
        alt = r.sky_bought_month * p.burn_share_of_buys
        L.append(f"| *Alternative reading — 10/55 of every buy in the month (TMF sheet)* | *{_usds(alt)} SKY* |")
        L.append("")
        if a.param_changes:
            L.append("**Parameter changes observed in the month**")
            L.append("")
            L.append("| When | Block | Contract | Parameter | Value | Tx |")
            L.append("|---|---:|---|---|---:|---|")
            for c in a.param_changes:
                L.append(f"| {_ts(c.ts)} | {c.block:,} | {c.contract} | {c.what} | {c.value} | `{c.tx[:10]}…` |")
            L.append("")
        if a.distributions:
            tot_d = sum((d.amount for d in a.distributions), ZERO)
            L.append(f"**SKY farm top-ups** — {len(a.distributions)} distributor pulls, "
                     f"{_usds(tot_d)} SKY moved Pause Proxy → REWARDS_LSSKY_SKY.")
            L.append("")
            L.append("| When | Block | SKY | Tx |")
            L.append("|---|---:|---:|---|")
            for d in a.distributions:
                L.append(f"| {_ts(d.ts)} | {d.block:,} | {_usds(d.amount)} | `{d.tx[:10]}…` |")
            L.append("")

    # ── State ──
    _state_keys = (
        "kicker_kbump", "kicker_khump", "splitter_hop", "splitter_burn",
        "usds_farm_rewards_duration", "usds_farm_reward_rate", "usds_farm_total_supply",
        "sky_farm_rewards_duration", "sky_farm_reward_rate", "sky_farm_total_supply",
        "dist_vest_id", "vest_tot", "vest_rxd", "vest_cap", "pause_proxy_sky",
        "vat_dai_vow", "vat_sin_vow", "vow_Sin", "vow_Ash", "usds_total_supply",
        "dai_total_supply",
    )
    if r.state and all(k in r.state for k in _state_keys):
        s = r.state
        L.append(f"## ④ On-chain state at month-end (block {s.get('block'):,}, {_ts(s.get('ts'))})")
        L.append("")
        L.append("| Contract | Parameter | Value |")
        L.append("|---|---|---:|")
        L.append(f"| MCD_KICK | kbump | {_usds(_d(s['kicker_kbump']), 0)} USDS |")
        L.append(f"| MCD_KICK | khump (gate offset) | {_usds(_d(s['kicker_khump']), 0)} USDS |")
        L.append(f"| MCD_SPLIT | hop | {int(s['splitter_hop']):,} s |")
        L.append(f"| MCD_SPLIT | burn | {_pct(_d(s['splitter_burn']))} |")
        L.append(f"| MCD_SPLIT | zzz (last kick) | {_ts(s.get('splitter_zzz'))} |")
        L.append(f"| REWARDS_LSSKY_USDS | rewardsDuration | {int(s['usds_farm_rewards_duration']):,} s |")
        L.append(f"| REWARDS_LSSKY_USDS | rewardRate | {_d(s['usds_farm_reward_rate']):.6f} USDS/s |")
        L.append(f"| REWARDS_LSSKY_USDS | totalSupply (staked SKY) | {_usds(_d(s['usds_farm_total_supply']), 0)} |")
        L.append(f"| REWARDS_LSSKY_SKY | rewardsDuration | {int(s['sky_farm_rewards_duration']):,} s |")
        L.append(f"| REWARDS_LSSKY_SKY | rewardRate | {_d(s['sky_farm_reward_rate']):.6f} SKY/s |")
        L.append(f"| REWARDS_LSSKY_SKY | totalSupply (staked SKY) | {_usds(_d(s['sky_farm_total_supply']), 0)} |")
        L.append(f"| REWARDS_DIST_LSSKY_SKY | vestId | {s['dist_vest_id']} |")
        L.append(f"| REWARDS_DIST_LSSKY_SKY | lastDistributedAt | {_ts(s.get('dist_last_distributed_at'))} |")
        L.append(f"| MCD_VEST_SKY_TREASURY | stream {s['dist_vest_id']}: bgn → fin | {_ts(s.get('vest_bgn'))} → {_ts(s.get('vest_fin'))} |")
        L.append(f"| MCD_VEST_SKY_TREASURY | stream {s['dist_vest_id']}: tot / rxd | {_usds(_d(s['vest_tot']), 0)} / {_usds(_d(s['vest_rxd']), 0)} SKY |")
        L.append(f"| MCD_VEST_SKY_TREASURY | cap | {_d(s['vest_cap']):.4f} SKY/s |")
        L.append(f"| MCD_PAUSE_PROXY | SKY balance | {_usds(_d(s['pause_proxy_sky']), 0)} SKY |")
        L.append(f"| MCD_VAT / MCD_VOW | vat.dai(vow) - vat.sin(vow) | {_usds(_d(s['vat_dai_vow']) - _d(s['vat_sin_vow']), 0)} USDS |")
        L.append(f"| MCD_VAT / MCD_VOW | vat.dai(vow) - (sin - Sin - Ash) | {_usds(_d(s['vat_dai_vow']) - (_d(s['vat_sin_vow']) - _d(s['vow_Sin']) - _d(s['vow_Ash'])), 0)} USDS |")
        L.append(f"| USDS / DAI | totalSupply | {_usds(_d(s['usds_total_supply']), 0)} / {_usds(_d(s['dai_total_supply']), 0)} |")
        L.append("")
        # APY snapshot
        staked_u = _d(s["usds_farm_total_supply"])
        staked_s = _d(s["sky_farm_total_supply"])
        L.append("**APY snapshot** — this cycle's annualised rewards over the month-end staked "
                 "balances, TWAP as the SKY price (projection, not a realised yield).")
        L.append("")
        if staked_s > 0 and staked_u < staked_s * D("0.05"):
            L.append("> The USDS farm held under 5% of the SKY farm's stake at month-end — it was "
                     "dormant / ramping (reactivated 2026-08-17), so its APY here is not meaningful.")
            L.append("")
        L.append("| Farm | Annual rewards | Staked | APY |")
        L.append("|---|---:|---:|---:|")
        if staked_u > 0 and i.sky_twap > 0:
            apy_u = wf.usds_rewards_usds * 12 / (staked_u * i.sky_twap)
            L.append(f"| USDS farm | {_usds(wf.usds_rewards_usds * 12, 0)} USDS | {_usds(staked_u, 0)} SKY (≈ {_usds(staked_u * i.sky_twap, 0)} USDS) | {_pct(apy_u)} |")
        if staked_s > 0:
            apy_s = wf.sky_rewards_monthly * 12 / staked_s
            L.append(f"| SKY farm | {_usds(wf.sky_rewards_monthly * 12, 0)} SKY | {_usds(staked_s, 0)} SKY | {_pct(apy_s)} |")
        L.append("")

    # ── Checks ──
    L.append("## Cross-checks")
    L.append("")
    L.append("| Check | Computed | Published / reference | |")
    L.append("|---|---:|---:|:-:|")
    for chk in r.checks:
        mark = "info" if chk["tolerance"] == "info" else ("✓" if chk["ok"] else "✗")
        L.append(f"| {chk['label']} | {chk['computed']} | {chk['published']} | {mark} |")
    L.append("")
    for w in r.warnings:
        L.append(f"> ⚠ {w}")
    if r.warnings:
        L.append("")
    L.append(
        "*Sources: TMF Configurations (forum.skyeco.com/t/28153) · Sky Atlas A.2.3 · "
        "dss-flappers (Kicker / Splitter / FlapperUniV2SwapOnly) · on-chain via HyperSync "
        "(events) and ETH_RPC (state). Open methodology questions in docs/tmf/README.md.*"
    )
    L.append("")
    return "\n".join(L)


# ── persistence ──────────────────────────────────────────────────────────────


def _jsonable(x: Any) -> Any:
    if isinstance(x, Decimal):
        return str(x)
    if isinstance(x, datetime | date):
        return x.isoformat()
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if hasattr(x, "__dataclass_fields__"):
        return {k: _jsonable(getattr(x, k)) for k in x.__dataclass_fields__}
    return x


def write_tmf(r: TmfMonthly, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prov = {
        "id": "tmf",
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "month": r.month,
        "msc": r.msc,
        "inputs": _jsonable(r.inputs),
        "policy": _jsonable(r.policy),
        "sources": r.sources,
        "pins": _jsonable(r.pins),
        "results": {
            "waterfall": _jsonable(r.waterfall),
            "sky_bought_month": str(r.sky_bought_month),
            "usds_spent_month": str(r.usds_spent_month),
            "kicks_burn_window": r.kicks_burn_window,
            "usds_spent_burn_window": str(r.usds_spent_burn_window),
            "sky_bought_burn_window": str(r.sky_bought_burn_window),
            "sky_to_stakers_from_buys": str(r.sky_to_stakers_from_buys),
            "sky_to_burn": str(r.sky_to_burn),
            "regimes": _jsonable(r.regimes),
        },
        "activity": _jsonable(r.activity) if r.activity else None,
        "state": _jsonable(r.state),
        "published": _jsonable(r.published),
        "checks": r.checks,
        "warnings": r.warnings,
    }
    (out_dir / "provenance.json").write_text(json.dumps(prov, indent=2) + "\n")
    (out_dir / "summary.md").write_text(render_summary(r))
    return {"provenance": out_dir / "provenance.json", "summary": out_dir / "summary.md"}
