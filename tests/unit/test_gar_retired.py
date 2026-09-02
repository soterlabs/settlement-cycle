"""GAR retirement: bounded at 2026-08, not deleted.

Governance Accessibility Rewards was a Skybase-only Demand-Side primitive
(1% of the month's Sky Net Revenue) carried on reports 2026-01…2026-07.
The MSC retired it from August 2026 (operator decision 2026-09).

It is expressed as ``GarConfig.until_month`` — the first month WITHOUT GAR
— rather than by deleting the primitive, because the settled months must
stay reproducible. Deleting it would mean a re-run of any 2026-01…07 month
silently dropped the line, and ``settlements/**/provenance.json`` is
gitignored, so there would be no way to recover the stored value. That
regression has already happened once: January's demand side went
314,251.68 → 222,064.54 (see ``load.writer._DEMAND_SIDE_FIELDS``).

SNR impact is asymmetric and is asserted at the bottom:
``gar → dv → send``, and ``msc_net = mint − send``, so dropping GAR RAISES
Sky Net Revenue by that amount. Months before 2026-07 are on the PAID
basis (``sky_total.py``, which never reads ``gar``); July is pinned via
``msc_preview.skybase.gar_in_dv``. So only 2026-08 onward is affected.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from settle.domain.config import load_prime
from settle.domain.period import Month

_REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def skybase():
    return load_prime(_REPO / "config" / "skybase.yaml")


# --- the bound itself -----------------------------------------------------

def test_skybase_gar_is_bounded_at_2026_08(skybase):
    assert skybase.gar is not None, "the primitive is bounded, not deleted"
    assert skybase.gar.share == Decimal("0.01")
    assert skybase.gar.from_month == "2026-01"
    assert skybase.gar.until_month == "2026-08"


def test_only_skybase_has_gar():
    """Retirement must not touch any other prime — none ever had it."""
    for cfg in sorted((_REPO / "config").glob("*.yaml")):
        if cfg.name in ("skybase.yaml", "sky_total.yaml", "subsidy_reference_rates.yaml"):
            continue
        try:
            p = load_prime(cfg)
        except Exception:
            continue                      # not a prime config
        assert p.gar is None, f"{cfg.name} unexpectedly declares GAR"


# ``basis`` distinguishes the three outcomes: "" = never in the program,
# "retired from …" = past the bound, otherwise the share × SNR derivation.
@pytest.mark.parametrize("month,earns,basis_exp", [
    (Month(2025, 12), False, ""),                      # before from_month
    (Month(2026, 1), True, None),                      # first month with GAR
    (Month(2026, 7), True, None),                      # last month (MSC#11)
    (Month(2026, 8), False, "retired from 2026-08"),   # RETIRED from here
    (Month(2026, 9), False, "retired from 2026-08"),
    (Month(2027, 1), False, "retired from 2026-08"),
])
def test_month_gate(skybase, month, earns, basis_exp, tmp_path):
    """Drives the real ``compute_gar`` gate, with a fake sky_total artifact."""
    from settle.compute.gar import compute_gar

    d = tmp_path / "settlements" / "sky_total" / str(month)
    d.mkdir(parents=True)
    (d / "provenance.json").write_text(
        '{"results": {"sky_net_revenue": "10000000"}, "generated_at_utc": "t"}'
    )
    gar, basis = compute_gar(skybase, month, repo_root=tmp_path)
    if earns:
        assert gar == Decimal("100000")          # 1% of 10M
        assert basis, "an earning month must record its derivation"
    else:
        assert gar == Decimal("0")
        assert basis == basis_exp


def test_retired_month_is_distinguishable_from_never_enrolled():
    """A $0 GAR must say WHY. Otherwise skybase 2026-09 provenance is
    indistinguishable from obex, which never had the program — and the basis
    field exists to be an audit trail."""
    import dataclasses

    from settle.compute.gar import compute_gar

    sb = load_prime(_REPO / "config" / "skybase.yaml")
    retired = compute_gar(sb, Month(2026, 9), repo_root=Path("/nonexistent"))[1]
    never = compute_gar(
        dataclasses.replace(sb, gar=None), Month(2026, 9), repo_root=Path("/x"),
    )[1]
    assert retired == "retired from 2026-08"
    assert never == ""
    assert retired != never


def test_retired_month_needs_no_sky_total_artifact(skybase):
    """The gate must short-circuit BEFORE the artifact lookup.

    Otherwise every month from 2026-08 on would fail loud looking for a
    sky_total provenance it has no reason to read.
    """
    from settle.compute.gar import compute_gar
    gar, _ = compute_gar(skybase, Month(2026, 8), repo_root=Path("/nonexistent"))
    assert gar == Decimal("0")


# --- the bound must be well-formed ----------------------------------------

@pytest.mark.parametrize("until", ["2026-01", "2025-06"])
def test_range_ending_at_or_before_its_start_is_rejected(until):
    """A mis-ordered range disables the primitive for EVERY month — a $0
    revenue line with no error. Fail loud instead."""
    from settle.domain.primes import GarConfig
    with pytest.raises(ValueError, match="must be after"):
        GarConfig(share=Decimal("0.01"), from_month="2026-01", until_month=until)


def test_open_ended_range_is_still_allowed():
    from settle.domain.primes import GarConfig
    cfg = GarConfig(share=Decimal("0.01"), from_month="2026-01")
    assert cfg.until_month is None


# --- reporting ------------------------------------------------------------

def _prov(results_extra: dict) -> dict:
    results = {
        "sky_revenue": "0", "sde_revenue": "0", "prime_agent_revenue": "0",
        "agent_rate": "1000", "distribution_rewards": "500",
        "chronicle_points": "0", "gar": "0",
    }
    results.update(results_extra)
    return {
        "prime_id": "skybase", "month": "2026-09",
        "period": {"start": "2026-09-01", "end": "2026-09-30", "n_days": 30},
        "results": results, "dr_breakdown": [], "venue_breakdown": [],
    }


def test_retired_month_renders_no_row():
    from settle.load.summary import render_summary
    out = render_summary(_prov({}))                      # gar == "0"
    assert "governance accessibility" not in out.lower()
    assert "**1,500.00**" in out                         # agent_rate + DR only


def test_earning_month_still_renders_and_sums():
    from settle.load.summary import render_summary
    out = render_summary(_prov({"gar": "250.00"}))
    assert "| governance accessibility rewards | 250.00 |" in out
    assert "**1,750.00**" in out


# --- SNR coupling ---------------------------------------------------------

def test_paid_basis_never_reads_gar():
    """Jan…Jun are PAID basis — anchored on the settlement tx, so GAR is
    structurally invisible to their SNR."""
    src = (_REPO / "src" / "settle" / "compute" / "sky_total.py").read_text()
    assert "gar" not in src


def test_accrual_demand_side_includes_gar():
    """From 2026-07 the accrual basis sums ``gar`` into the demand side, which
    flows into ``send`` and therefore subtracts from ``msc_net``. This is why
    retiring GAR RAISES SNR from 2026-08 on."""
    src = (_REPO / "src" / "settle" / "compute" / "sky_total_accrual.py").read_text()
    assert 'Decimal(r.get("gar") or 0)' in src
    assert "self.total_mint - self.total_send" in src
