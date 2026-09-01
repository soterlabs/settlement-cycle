"""Guards on the DR-only refresh path and the DR ref-code table.

Both cover regressions observed on the settle-dr-dune 4d144bf → 5bb3d4a bump,
where ``refresh_dr_only`` re-rendered every month that had a provenance file:

* skybase 2026-01…06 silently LOST their ``governance accessibility rewards``
  line (January demand-side 314,251.68 → 222,064.54) because those provenance
  files predate the GAR primitive and carry no ``gar`` key at all. Absent is
  not zero, and only absent is unsafe.
* spark 2026-06/07 gained a bare ``223 | $0.00`` ref-code row because the
  rebuilt workbook started carrying a zero row for a previously-absent code —
  pure churn in a published report, no economic change.
"""

from __future__ import annotations

import json

from settle.load.summary import render_summary

# ── DR ref-code table: zero rows are display-suppressed ──────────────────────

def _prov(dr_rows, dist="100.00"):
    return {
        "prime_id": "spark",
        "month": "2026-08",
        "period": {"start": "2026-08-01", "end": "2026-08-31", "n_days": 31},
        "results": {
            "sky_revenue": "0", "sde_revenue": "0", "prime_agent_revenue": "0",
            "agent_rate": "0", "distribution_rewards": dist,
            "gar": "0", "chronicle_points": "0",
        },
        "dr_breakdown": dr_rows,
        "venue_breakdown": [],
    }


def test_zero_amount_ref_codes_are_not_rendered():
    out = render_summary(_prov([
        {"ref_code": "128", "amount": "100.00", "notes": ""},
        {"ref_code": "223", "amount": "0", "notes": ""},
        {"ref_code": "219", "amount": "0.00", "notes": ""},
    ]))
    assert "| 128 |" in out
    assert "| 223 |" not in out, "a $0.00 code must not churn the report"
    assert "| 219 |" not in out
    # The headline total stays authoritative regardless of what is displayed.
    assert "$100.00" in out


def test_dr_section_omitted_when_every_code_is_zero():
    out = render_summary(_prov(
        [{"ref_code": "223", "amount": "0", "notes": ""}], dist="0",
    ))
    assert "## DR per ref code" not in out


def test_nonzero_codes_still_render_with_notes():
    out = render_summary(_prov([
        {"ref_code": "3009", "amount": "21.19", "notes": "gtSkyLooping"},
    ]))
    assert "| 3009 |" in out and "gtSkyLooping" in out


# ── refresh_dr_only: schema-drift guard + month filter ───────────────────────

def _write_prov(root, prime, month, *, with_gar=True):
    d = root / "settlements" / prime / month
    d.mkdir(parents=True)
    results = {
        "sky_revenue": "0", "prime_agent_revenue": "1000", "agent_rate": "0",
        "distribution_rewards": "0", "chronicle_points": "0",
        "prime_agent_total_revenue": "1000", "monthly_pnl": "1000",
    }
    if with_gar:
        results["gar"] = "50"
    p = d / "provenance.json"
    p.write_text(json.dumps({"prime_id": prime, "month": month,
                             "period": {"start": f"{month}-01"},
                             "results": results}))
    return p


def _run(monkeypatch, tmp_path, **kw):
    """Call refresh_dr_only against a temp settlements root."""
    import settle.load.writer as w
    monkeypatch.setattr(w, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(w, "load_dr", lambda p, m: {"total": 7, "rows": []})
    monkeypatch.setattr(w, "_build_canonical_xlsx", lambda *a, **k: None)
    monkeypatch.setattr(w, "write_summary", lambda *a, **k: None)
    return w.refresh_dr_only("skybase", **kw)


def test_provenance_missing_gar_is_skipped_not_rerendered(monkeypatch, tmp_path):
    """The skybase regression: no `gar` key → skip, leave the file untouched."""
    p = _write_prov(tmp_path, "skybase", "2026-01", with_gar=False)
    before = p.read_text()
    assert _run(monkeypatch, tmp_path) == []
    assert p.read_text() == before, "a pre-GAR provenance must not be rewritten"


def test_provenance_with_gar_is_refreshed(monkeypatch, tmp_path):
    p = _write_prov(tmp_path, "skybase", "2026-08", with_gar=True)
    assert len(_run(monkeypatch, tmp_path)) == 1
    assert json.loads(p.read_text())["results"]["distribution_rewards"] == "7"


def test_months_filter_touches_only_the_named_month(monkeypatch, tmp_path):
    keep = _write_prov(tmp_path, "skybase", "2026-07")
    target = _write_prov(tmp_path, "skybase", "2026-08")
    untouched = keep.read_text()
    assert len(_run(monkeypatch, tmp_path, months={"2026-08"})) == 1
    assert keep.read_text() == untouched
    assert json.loads(target.read_text())["results"]["distribution_rewards"] == "7"
