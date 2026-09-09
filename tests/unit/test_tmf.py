"""Unit tests for the TMF waterfall — anchored on the two published cycles.

July 2026 (2026-08-13 exec, executed 2026-08-17) and August 2026 (2026-09-10
exec per forum t/28153) are the validation columns of the operator's TMF
Calculator sheet; every figure below is the published one.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from settle.compute.tmf import (
    SbeActivity,
    SbeKick,
    TmfInputs,
    TmfPolicy,
    attribute_burn,
    burn_attribution,
    compute_tmf_monthly,
    compute_waterfall,
    read_sky_total_snr,
    regimes_from_kicks,
    render_summary,
    retention_rate,
)

_REPO = Path(__file__).resolve().parents[2]
D = Decimal


@pytest.fixture(scope="module")
def cfg() -> dict:
    return yaml.safe_load((_REPO / "config" / "tmf.yaml").read_text())


@pytest.fixture(scope="module")
def policy(cfg: dict) -> TmfPolicy:
    return TmfPolicy.from_config(cfg)


def _inputs(month: str, snr: str, twap: str, abc: str, supply: str) -> TmfInputs:
    return TmfInputs(month=month, snr=D(snr), sky_twap=D(twap),
                     aggregate_backstop_capital=D(abc), usds_supply=D(supply))


# ── policy ──────────────────────────────────────────────────────────────────

def test_policy_derived_shares(policy: TmfPolicy):
    assert policy.splitter_burn == D("0.55")
    assert policy.burn_share_of_buys == D("0.10") / D("0.55")
    assert policy.vest_tau_seconds == 90 * 86_400


def test_policy_rejects_legs_not_summing_to_one(cfg: dict):
    bad = json.loads(json.dumps(cfg))
    bad["policy"]["step3_burn_share"] = "0.11"
    with pytest.raises(ValueError, match="Step 3 legs"):
        TmfPolicy.from_config(bad)


# ── Step 2 tiers ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("abc,expected", [
    ("0", "0.5"), ("149999999", "0.5"),           # below the Floor → full retention
    ("150000000", "0.125"),                        # at Floor, TBC 200M -> 50% x (1 - 0.75)
    ("180000000", "0.05"),                         # 50% x (1 - 0.9)
    ("200000000", "0"), ("250000000", "0"),        # at / above target → 0
])
def test_retention_rate_tiers(abc, expected):
    r = retention_rate(D(abc), floor=D("150000000"), tbc=D("200000000"), max_rate=D("0.5"))
    assert r == D(expected)


def test_retention_rate_when_target_below_floor_is_step_function():
    # TBC (95M) < Floor (150M): the "between" band is empty — 50% below the
    # Floor, 0 at/above it.
    assert retention_rate(D("80e6"), floor=D("150e6"), tbc=D("95e6"), max_rate=D("0.5")) == D("0.5")
    assert retention_rate(D("150e6"), floor=D("150e6"), tbc=D("95e6"), max_rate=D("0.5")) == 0


# ── July 2026 anchor (MSC#11 → 2026-08-13 exec) ─────────────────────────────

def test_july_2026_waterfall_matches_the_executive(policy: TmfPolicy):
    wf = compute_waterfall(
        _inputs("2026-07", "10517426", "0.058608801023606662", "80482665.96", "6255703158"),
        policy,
    )
    q = D("0.01")
    assert wf.step1_total.quantize(q) == D("2103485.20")
    assert wf.after_step1.quantize(q) == D("8413940.80")
    assert wf.retention_rate == D("0.5")
    assert wf.step2_retained.quantize(q) == D("4206970.40")
    assert wf.step3_capital.quantize(q) == D("4206970.40")
    assert wf.sky_rewards_usds.quantize(q) == D("1893136.68")
    assert wf.usds_rewards_usds.quantize(q) == D("1893136.68")
    assert wf.burn_usds.quantize(q) == D("420697.04")
    assert wf.buy_usds.quantize(q) == D("2313833.72")
    assert wf.hop_solved.quantize(q) == D("3748.07")
    assert wf.hop == 3748
    assert wf.annual_run_rate.quantize(D(1)) == D("50483645")
    assert wf.sky_bought_estimate.quantize(D(1)) == D("39479288")
    assert wf.sky_rewards_monthly.quantize(q) == D("32301235.43")
    assert wf.vest_tot == D("96903706")
    assert wf.stream_rate.quantize(D("0.0001")) == D("12.4619")
    assert wf.weekly_pull.quantize(D(1)) == D("7536955")
    assert wf.hop_within_beam and wf.kbump_within_beam and wf.run_rate_within_beam


# ── August 2026 anchor (MSC#12 → 2026-09-10 exec, t/28153) ──────────────────

def test_august_2026_waterfall_matches_tmf_configurations(policy: TmfPolicy):
    wf = compute_waterfall(
        _inputs("2026-08", "15745296", "0.059371239604985234", "75728460.53", "6366968221"),
        policy,
    )
    q = D("0.01")
    assert wf.step1_total.quantize(q) == D("3149059.20")
    assert wf.step3_capital.quantize(q) == D("6298118.40")
    assert wf.usds_rewards_usds.quantize(q) == D("2834153.28")
    assert wf.buy_usds.quantize(q) == D("3463965.12")
    assert wf.burn_usds.quantize(q) == D("629811.84")
    assert wf.hop_solved.quantize(q) == D("2503.60")
    assert wf.hop == 2504                       # rounds UP here — nearest, not floor
    assert wf.sky_rewards_monthly.quantize(q) == D("47736131.14")
    assert wf.vest_tot == D("143208393")
    assert wf.weekly_pull.quantize(D(1)) == D("11138431")


def test_august_burn_attribution_matches_ba_labs(policy: TmfPolicy):
    # 311 kicks x 3,300 USDS = 1,026,300 USDS -> 15,735,190.69 SKY (t/28153)
    to_stakers, to_burn = burn_attribution(D("15735190.69"), policy)
    assert to_stakers.quantize(D("0.01")) == D("12874246.93")
    assert to_burn.quantize(D("0.01")) == D("2860943.76")


# ── regimes + monthly assembly ──────────────────────────────────────────────

def _kick(i: int, ts: int, burn: str, hop: int, bought: str, tx: str | None = None) -> SbeKick:
    b = D(burn)
    return SbeKick(block=100 + i, log_index=1, ts=ts, tx=tx or f"0x{i:064x}",
                   tot=D(6000), lot=D(6000) * b, pay=D(6000) * (1 - b),
                   bought=D(bought), burn=b, hop=hop)


def test_regimes_split_on_parameter_change():
    kicks = [
        _kick(0, 1_000, "1", 13787, "100000"),
        _kick(1, 14_787, "1", 13787, "100000"),
        _kick(2, 20_000, "0.55", 3748, "50000"),
        _kick(3, 23_748, "0.55", 3748, "50000"),
        _kick(4, 27_496, "0.55", 3748, "50000"),
    ]
    regs = regimes_from_kicks(kicks)
    assert [(g.burn, g.hop, g.n_kicks) for g in regs] == [
        (D(1), 13787, 2), (D("0.55"), 3748, 3),
    ]
    old, new = regs
    assert old.to_farm == 0 and old.to_flapper == D(12000)
    assert new.to_flapper == D(9900) and new.to_farm == D(8100)
    assert new.vwap == D(9900) / D(150000)


def _seed_sky_total(root: Path, label: str, snr: str, *, provenance: bool) -> None:
    d = root / "settlements" / "sky_total" / label
    d.mkdir(parents=True)
    if provenance:
        (d / "provenance.json").write_text(json.dumps({"results": {"sky_net_revenue": snr}}))
    else:
        (d / "summary.md").write_text(
            "# SKY_TOTAL\n\n| Field | USDS |\n|---|---:|\n"
            f"| **Sky Net Revenue** | **{D(snr):,.2f}** |\n"
        )


def test_read_sky_total_snr_falls_back_to_committed_summary(tmp_path: Path):
    _seed_sky_total(tmp_path, "2026-08", "15745296.07", provenance=False)
    a = read_sky_total_snr(tmp_path, "2026-08")
    assert (a.snr, a.generated) == (D("15745296.07"), "summary.md")
    _seed_sky_total(tmp_path, "2026-07", "10517425.807934152", provenance=True)
    assert read_sky_total_snr(tmp_path, "2026-07").snr == D("10517425.807934152")
    assert read_sky_total_snr(tmp_path, "2026-06") is None


def _august_activity(policy: TmfPolicy) -> SbeActivity:
    """Two regimes with the published August totals: 103 legacy kicks
    (11,365,511.43 SKY) then 311 kicks at 55% (15,735,190.69 SKY)."""
    kicks: list[SbeKick] = []
    per_old = D("11365511.43") / 103
    per_new = D("15735190.69") / 311
    for i in range(103):
        kicks.append(_kick(i, 1_785_542_400 + i * 13787, "1", 13787, str(per_old)))
    for j in range(311):
        kicks.append(_kick(200 + j, 1_787_011_000 + j * 3748, "0.55", 3748, str(per_new)))
    return SbeActivity(month="2026-08", from_block=25656293, to_block=25878704,
                       from_ts=1785542400, to_ts=1788220799, kicks=kicks)


def test_compute_tmf_monthly_august_cross_checks_all_pass(cfg: dict, policy: TmfPolicy, tmp_path: Path):
    _seed_sky_total(tmp_path, "2026-08", "15745296.07", provenance=False)
    state = {"block": 25878704, "ts": 1788220799, "usds_total_supply": D("6366968221"),
             "vest_cap": D("70.73")}
    r = compute_tmf_monthly("2026-08", cfg["months"]["2026-08"], policy,
                            activity=_august_activity(policy), state=state, repo_root=tmp_path)
    failed = [c for c in r.checks if not c["ok"]]
    assert failed == [], failed
    assert r.kicks_burn_window == 311
    assert r.sky_to_burn.quantize(D("0.01")) == D("2860943.76")
    assert r.sky_bought_month.quantize(D("0.01")) == D("27100702.12")  # Σ of rounded shares
    assert r.warnings == []
    # The alternative (whole-month) reading is shown but NOT used.
    md = render_summary(r)
    assert "2,860,943.76 SKY" in md and "4,927,400.3" in md   # synthetic Σ differs in the last cent
    assert "splitter.hop | 2,504 s | 2,504 s" in md


def test_compute_tmf_monthly_uses_spell_block_state_not_month_end(cfg: dict, policy: TmfPolicy, tmp_path: Path):
    """The cycle-month spell casts in M+1: month-end state still shows the
    OLD hop/burn and must not be flagged — only the post-cast snapshot is."""
    _seed_sky_total(tmp_path, "2026-07", "10517425.81", provenance=False)
    month_end = {"block": 25656292, "ts": 1785542399, "usds_total_supply": D("6255703158"),
                 "vest_cap": D("70.73"), "splitter_hop": 13787, "splitter_burn": D(1),
                 "vest_tot": D("286714697")}
    post_cast = {"block": 25775271, "ts": 1787061743, "splitter_hop": 3748,
                 "usds_farm_rewards_duration": 3748, "splitter_burn": D("0.55"),
                 "vest_tot": D("96903706"), "vest_bgn": 1787061743,
                 "vest_fin": 1787061743 + 90 * 86400, "dist_vest_id": 16,
                 "kicker_kbump": D(6000)}
    act = SbeActivity(month="2026-07", from_block=1, to_block=2, from_ts=0, to_ts=1,
                      kicks=[_kick(0, 100, "1", 13787, "19831814.35")])
    mcfg = {k: v for k, v in cfg["months"]["2026-07"].items() if k != "dune_8544603"}
    r = compute_tmf_monthly("2026-07", mcfg, policy, activity=act,
                            state=month_end, spell_state=post_cast, repo_root=tmp_path)
    failed = [c for c in r.checks if not c["ok"]]
    assert failed == [], failed
    assert r.warnings == []
    assert r.sky_to_burn == 0                      # no 55%-regime kicks in July
    labels = [c["label"] for c in r.checks]
    assert "on-chain splitter.hop after cast" in labels
    assert not any("weekly distributor pull" in x for x in labels)   # removed: never fires in prod


def test_compute_tmf_monthly_requires_snr_or_artifact(cfg: dict, policy: TmfPolicy, tmp_path: Path):
    mcfg = dict(cfg["months"]["2026-08"])
    mcfg.pop("snr")
    with pytest.raises(FileNotFoundError, match="no SNR"):
        compute_tmf_monthly("2026-08", mcfg, policy, activity=None,
                            state={"usds_total_supply": D(1), "block": 1}, repo_root=tmp_path)
    # With the artifact present the pin is optional — SNR is read and rounded.
    _seed_sky_total(tmp_path, "2026-08", "15745296.07", provenance=False)
    r = compute_tmf_monthly("2026-08", mcfg, policy, activity=None,
                            state={"usds_total_supply": D("6366968221"), "block": 1},
                            repo_root=tmp_path)
    assert r.inputs.snr == D("15745296")
    assert r.waterfall.hop == 2504


# ── review follow-ups (PR #195) ─────────────────────────────────────────────

_CAST_TS = 1786975343   # 2026-08-17 14:02:23 UTC — policy.tmf_effective_from


def test_policy_parses_tmf_effective_from(policy: TmfPolicy):
    assert policy.tmf_effective_from == _CAST_TS


def test_attribute_burn_is_per_regime_not_global_constant(policy: TmfPolicy):
    kicks = [
        _kick(0, _CAST_TS - 10, "1", 13787, "1000"),        # legacy: not attributed
        _kick(1, _CAST_TS + 10, "0.55", 3748, "550"),       # 10/55 -> 100 burn
        _kick(2, _CAST_TS + 20, "0.60", 2504, "600"),       # split moved to 50/40/10 -> 10/60 -> 100 burn
    ]
    window, to_stakers, to_burn = attribute_burn(kicks, policy)
    assert [k.bought for k in window] == [D(550), D(600)]
    assert to_burn == D(200)
    assert to_stakers == D(950)


def test_no_activity_emits_no_burn_window_checks(cfg: dict, policy: TmfPolicy, tmp_path: Path):
    """Credential-less run: the window is trivially empty — comparing 0 to the
    published burn would be five spurious failures."""
    r = compute_tmf_monthly("2026-08", cfg["months"]["2026-08"], policy, activity=None,
                            state={"usds_total_supply": D("6366968221"), "block": 1},
                            repo_root=tmp_path)
    assert [c for c in r.checks if not c["ok"]] == []
    assert not any(c["label"].startswith(("burn-window", "SKY to burn")) for c in r.checks)
    assert all("burn" not in w for w in r.warnings)


def test_executed_at_accepts_yaml_datetime(cfg: dict, policy: TmfPolicy, tmp_path: Path):
    """PyYAML parses an unquoted ``executed_at: 2026-08-17T14:02:23Z`` into a
    datetime — render + write must not assume a str."""
    from datetime import UTC, datetime

    from settle.compute.tmf import write_tmf
    mcfg = json.loads(json.dumps(cfg["months"]["2026-07"]))
    mcfg["spell"]["executed_at"] = datetime(2026, 8, 17, 14, 2, 23, tzinfo=UTC)
    r = compute_tmf_monthly("2026-07", mcfg, policy, activity=None,
                            state={"usds_total_supply": D("6255703158"), "block": 1},
                            repo_root=tmp_path)
    md = render_summary(r)
    assert "| vestBgn | block.timestamp at cast | 2026-08-17 14:02:23 UTC |" in md
    out = write_tmf(r, tmp_path / "out")
    assert json.loads(out["provenance"].read_text())["published"]["executed_at"].startswith("2026-08-17T14:02:23")


def test_parameter_block_marks_follow_the_vest_checks(cfg: dict, policy: TmfPolicy, tmp_path: Path):
    """A mis-sized vest stream filed by the spell must show ✗ in the headline
    table, not only in the Cross-checks table."""
    post_cast = {"block": 25775271, "ts": _CAST_TS, "splitter_hop": 3748,
                 "usds_farm_rewards_duration": 3748, "splitter_burn": D("0.55"),
                 "vest_tot": D("97903706"), "vest_bgn": _CAST_TS,
                 "vest_fin": _CAST_TS + 89 * 86400, "dist_vest_id": 16, "kicker_kbump": D(6000)}
    r = compute_tmf_monthly("2026-07", cfg["months"]["2026-07"], policy, activity=None,
                            state={"usds_total_supply": D("6255703158"), "block": 1},
                            spell_state=post_cast, repo_root=tmp_path)
    md = render_summary(r)
    assert "| vestTot | 96,903,706 SKY | 96,903,706 SKY | 97,903,706 SKY | ✗ |" in md
    assert "| vestTau (days) | 90 d | 90 d | 89 d | ✗ |" in md
    assert "| splitter.hop | 3,748 s | 3,748 s | 3,748 s | ✓ |" in md


def test_beam_breach_names_the_right_lever(cfg: dict, tmp_path: Path):
    bad = json.loads(json.dumps(cfg))
    bad["policy"]["kbump"] = "15000"
    pol = TmfPolicy.from_config(bad)
    r = compute_tmf_monthly("2026-08", cfg["months"]["2026-08"], pol, activity=None,
                            state={"usds_total_supply": D("6366968221"), "block": 1},
                            repo_root=tmp_path)
    assert not r.waterfall.kbump_within_beam and r.waterfall.hop_within_beam
    assert any("kbump 15,000 USDS exceeds the SBE BEAM maxKbump" in w for w in r.warnings)
    assert not any("below the SBE BEAM minHop" in w for w in r.warnings)
    assert "kbump ≤ 12,000: BREACH · hop ≥ 550 s: ok" in render_summary(r)


def test_partial_month_is_bannered(cfg: dict, policy: TmfPolicy, tmp_path: Path):
    r = compute_tmf_monthly("2026-08", cfg["months"]["2026-08"], policy, activity=None,
                            state={"usds_total_supply": D("6366968221"), "block": 1},
                            repo_root=tmp_path,
                            pins={"partial": True, "to_block": 25800000, "to_ts": 1787500000})
    assert any(w.startswith("PARTIAL MONTH") for w in r.warnings)
    assert "**PARTIAL MONTH** — data stops at block 25800000" in render_summary(r)
