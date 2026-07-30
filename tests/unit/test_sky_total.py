"""Unit tests for the sky_total compute layer — the buffer-basis algebra,
one-off guard, warnings ordering, and rendering."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from settle.compute import sky_total as ST
from settle.domain import Month


# ── stub source ─────────────────────────────────────────────────────────────

class _StubSource:
    """Emits stream rows verbatim from a list — no on-chain access."""

    def __init__(self, rows: list[dict], cfg: dict | None = None) -> None:
        self._rows = rows
        self._cfg = cfg or {}

    def streams(self, month, pin_block):
        return pd.DataFrame(self._rows, columns=["stream", "label", "amount"])


def _rows(mint, subs, dsb, cc, *, block=25574490, ts=1784557319):
    """Build the [stream,label,amount] row set the source emits."""
    out = [
        {"stream": "settlement_block", "label": str(block), "amount": block},
        {"stream": "settlement_ts",    "label": str(ts),    "amount": ts},
    ]
    for prime, amt in mint.items():
        out.append({"stream": f"mint:{prime}", "label": prime, "amount": amt})
    for prime, amt in subs.items():
        out.append({"stream": f"subproxy:{prime}", "label": prime, "amount": amt})
    out.append({"stream": "dsb", "label": "dsb", "amount": dsb})
    out.append({"stream": "cc",  "label": "cc",  "amount": cc})
    return out


# ── non_msc provenance fixture ──────────────────────────────────────────────

def _seed_non_msc(tmp_path: Path, month: Month, income: Decimal, expense: Decimal):
    d = tmp_path / "settlements" / "non_msc" / f"{month.year}-{month.month:02d}"
    d.mkdir(parents=True)
    (d / "provenance.json").write_text(json.dumps({
        "results": {"total_income": str(income), "total_expense": str(expense)},
        "warnings": [],
    }))


# ── pure algebra ────────────────────────────────────────────────────────────

def test_derived_reproduces_doc_june_2026():
    """June 2026 doc §3 figures. Our derivation and the doc's own reported
    SNR differ by ~2.3K due to rounding in the published Step-1 Capital
    figure (BA publishes 20% × 13,714,694 = 2,742,939, whereas our exact
    formula uses 20% × our SNR = 2,745,221). Tolerance is set at 3K to
    absorb this while still catching a real regression."""
    snr, cc_gen = ST._derived_sky_net_and_cc_genesis(
        total_mint=Decimal("32716623"),
        total_subproxy_raw=Decimal("13875840"),
        dsb=Decimal("34902"),
        cc_gross=Decimal("3378069"),
        grove_tge_penalty=Decimal("1396260"),
        non_msc_net=Decimal("-3050668"),
    )
    # Doc §3 headline: Sky Net Revenue 13,723,823 (ties BA to +0.067%).
    assert abs(snr - Decimal("13723823")) < Decimal("3000")
    # Cross-check: 20% × BA's net = Step 1 Capital 2,742,939 → cc_genesis
    # ≈ 3,378,069 − 2,742,939 = 635,130. (Our exact value differs by the
    # same rounding as above.)
    assert abs(cc_gen - Decimal("635130")) < Decimal("3000")


def test_snr_scales_linearly_with_non_msc_net():
    """Sanity check: bumping non_msc_net by X should bump SNR by X / 0.8
    (because the 20% Step-1 Capital gets carved out of Sky's own SNR)."""
    kw = dict(
        total_mint=Decimal("30_000_000"),
        total_subproxy_raw=Decimal("15_000_000"),
        dsb=Decimal("0"),
        cc_gross=Decimal("2_000_000"),
        grove_tge_penalty=Decimal("0"),
    )
    snr0, _ = ST._derived_sky_net_and_cc_genesis(non_msc_net=Decimal("0"), **kw)
    snr1, _ = ST._derived_sky_net_and_cc_genesis(non_msc_net=Decimal("1_000_000"), **kw)
    assert (snr1 - snr0) == Decimal("1_000_000") / Decimal("0.8")


# ── compute integration ─────────────────────────────────────────────────────

def _base_cfg():
    return {
        "grove_tge_penalty": {},
        "one_off_transfers": {},
    }


def test_compute_populates_all_fields(tmp_path):
    month = Month(2026, 6)
    _seed_non_msc(tmp_path, month, Decimal("15881200"), Decimal("18931868"))
    rows = _rows(
        mint={"spark": Decimal("16923682"), "grove": Decimal("12342158"), "obex": Decimal("3450783")},
        subs={"spark": Decimal("9746443"), "grove": Decimal("2328332"), "obex": Decimal("1519539"),
              "keel": Decimal("77284"), "skybase": Decimal("204242")},
        dsb=Decimal("34902"), cc=Decimal("3378069"),
    )
    cfg = {**_base_cfg(), "grove_tge_penalty": {"2026-06": Decimal("1396260")}}
    src = _StubSource(rows)
    result = ST.compute_sky_total_monthly(
        month, source=src, repo_root=tmp_path, pin_block=25574490, config=cfg,
    )
    assert result.month == "2026-06"
    assert result.settlement_block == 25574490
    assert result.total_mint == Decimal("32716623")
    assert result.total_subproxy_raw == Decimal("13875840")
    assert result.dsb == Decimal("34902")
    assert result.cc_gross == Decimal("3378069")
    assert result.grove_tge_penalty == Decimal("1396260")
    assert result.grove_tge_penalty_source == "config:2026-06"
    # Doc's target SNR is 13,723,823 (+0.067% vs BA 13,714,694). Tolerance
    # 3K absorbs the Step-1-Capital rounding gap (see
    # test_derived_reproduces_doc_june_2026).
    assert abs(result.sky_net_revenue - Decimal("13723823")) < Decimal("3000")


def test_one_off_exceeding_raw_raises(tmp_path):
    month = Month(2026, 1)
    _seed_non_msc(tmp_path, month, Decimal("0"), Decimal("0"))
    # Skybase raw subproxy send = 5M, but config claims 10M one-off → guard.
    rows = _rows(
        mint={"spark": Decimal("0"), "grove": Decimal("0"), "obex": Decimal("0")},
        subs={"spark": Decimal("0"), "grove": Decimal("0"), "obex": Decimal("0"),
              "keel": Decimal("0"), "skybase": Decimal("5000000")},
        dsb=Decimal("0"), cc=Decimal("0"),
    )
    cfg = {**_base_cfg(), "one_off_transfers": {"2026-01": {"skybase": Decimal("10000000")}}}
    src = _StubSource(rows)
    with pytest.raises(ValueError, match="exceeds the on-chain settlement-block mint"):
        ST.compute_sky_total_monthly(
            month, source=src, repo_root=tmp_path, pin_block=25574490, config=cfg,
        )


def test_unknown_prime_in_one_off_raises(tmp_path):
    month = Month(2026, 1)
    _seed_non_msc(tmp_path, month, Decimal("0"), Decimal("0"))
    rows = _rows(
        mint={"spark": Decimal("0"), "grove": Decimal("0"), "obex": Decimal("0")},
        subs={"spark": Decimal("0"), "grove": Decimal("0"), "obex": Decimal("0"),
              "keel": Decimal("0"), "skybase": Decimal("0")},
        dsb=Decimal("0"), cc=Decimal("0"),
    )
    cfg = {**_base_cfg(), "one_off_transfers": {"2026-01": {"typo": Decimal("1000")}}}
    src = _StubSource(rows)
    with pytest.raises(ValueError, match="unknown prime"):
        ST.compute_sky_total_monthly(
            month, source=src, repo_root=tmp_path, pin_block=25574490, config=cfg,
        )


def test_missing_grove_tge_penalty_warns_and_books_zero(tmp_path):
    month = Month(2026, 5)
    _seed_non_msc(tmp_path, month, Decimal("15022235"), Decimal("19416950"))
    rows = _rows(
        mint={"spark": Decimal("15000000"), "grove": Decimal("10000000"), "obex": Decimal("3000000")},
        subs={"spark": Decimal("8000000"), "grove": Decimal("2000000"), "obex": Decimal("1000000"),
              "keel": Decimal("50000"), "skybase": Decimal("100000")},
        dsb=Decimal("30000"), cc=Decimal("3000000"),
    )
    cfg = _base_cfg()
    src = _StubSource(rows)
    result = ST.compute_sky_total_monthly(
        month, source=src, repo_root=tmp_path, pin_block=25574490, config=cfg,
    )
    assert result.grove_tge_penalty == Decimal("0")
    assert result.grove_tge_penalty_source == "unset"
    assert any("grove_tge_penalty" in w for w in result.warnings)


def test_negative_cc_genesis_fires_warning_before_construction(tmp_path):
    """The warning MUST be present on the returned object — earlier code
    reassigned result.warnings after construction; this guards regressions.
    Set up: keep cc_gross small so 0.2·SNR > cc_gross and cc_genesis flips
    negative."""
    month = Month(2026, 4)
    _seed_non_msc(tmp_path, month, Decimal("5000000"), Decimal("2000000"))
    rows = _rows(
        mint={"spark": Decimal("30000000"), "grove": Decimal("10000000"), "obex": Decimal("3000000")},
        subs={"spark": Decimal("2000000"), "grove": Decimal("500000"), "obex": Decimal("200000"),
              "keel": Decimal("50000"), "skybase": Decimal("100000")},
        dsb=Decimal("30000"), cc=Decimal("500000"),
    )
    cfg = _base_cfg()
    src = _StubSource(rows)
    result = ST.compute_sky_total_monthly(
        month, source=src, repo_root=tmp_path, pin_block=25574490, config=cfg,
    )
    assert result.cc_genesis_repayment < 0
    assert any("cc_genesis_repayment is NEGATIVE" in w for w in result.warnings)


def test_render_summary_avoids_double_minus_on_negative_cc_genesis(tmp_path):
    """The `--<value>` render bug was in an earlier revision when
    cc_genesis went negative. Regression guard."""
    month = Month(2026, 4)
    _seed_non_msc(tmp_path, month, Decimal("5000000"), Decimal("2000000"))
    rows = _rows(
        mint={"spark": Decimal("30000000"), "grove": Decimal("10000000"), "obex": Decimal("3000000")},
        subs={"spark": Decimal("2000000"), "grove": Decimal("500000"), "obex": Decimal("200000"),
              "keel": Decimal("50000"), "skybase": Decimal("100000")},
        dsb=Decimal("30000"), cc=Decimal("500000"),
    )
    cfg = _base_cfg()
    src = _StubSource(rows)
    result = ST.compute_sky_total_monthly(
        month, source=src, repo_root=tmp_path, pin_block=25574490, config=cfg,
    )
    md = ST.render_summary(result)
    # The `--<digit>` artifact was the specific bug — `---` in markdown
    # table separators is legit; only reject a minus followed by another
    # minus and then a digit.
    import re
    assert not re.search(r"--\d", md), f"double-minus render bug: {md}"
    assert "NEGATIVE" in md  # explicit callout instead


def test_compute_falls_back_to_source_cfg_if_config_not_passed(tmp_path):
    """Backwards-compat: existing callers that don't pass ``config`` should
    keep working via source._cfg."""
    month = Month(2026, 6)
    _seed_non_msc(tmp_path, month, Decimal("0"), Decimal("0"))
    rows = _rows(
        mint={"spark": Decimal("0"), "grove": Decimal("0"), "obex": Decimal("0")},
        subs={"spark": Decimal("0"), "grove": Decimal("0"), "obex": Decimal("0"),
              "keel": Decimal("0"), "skybase": Decimal("0")},
        dsb=Decimal("0"), cc=Decimal("0"),
    )
    src = _StubSource(rows, cfg=_base_cfg())
    result = ST.compute_sky_total_monthly(
        month, source=src, repo_root=tmp_path, pin_block=25574490,
    )
    assert result.sky_net_revenue == Decimal("0")


def test_compute_raises_when_no_config_and_source_has_none(tmp_path):
    """Explicit error when neither path is available."""
    month = Month(2026, 6)
    _seed_non_msc(tmp_path, month, Decimal("0"), Decimal("0"))
    rows = _rows(
        mint={"spark": Decimal("0"), "grove": Decimal("0"), "obex": Decimal("0")},
        subs={"spark": Decimal("0"), "grove": Decimal("0"), "obex": Decimal("0"),
              "keel": Decimal("0"), "skybase": Decimal("0")},
        dsb=Decimal("0"), cc=Decimal("0"),
    )

    class _NoCfg:
        def streams(self, m, pin):
            return pd.DataFrame(rows, columns=["stream", "label", "amount"])

    with pytest.raises(ValueError, match="no config provided"):
        ST.compute_sky_total_monthly(
            month, source=_NoCfg(), repo_root=tmp_path, pin_block=25574490,
        )
