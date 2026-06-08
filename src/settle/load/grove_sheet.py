"""Grove-PnL-workbook-shaped breakdown — compute the per-venue
``cof_alloc / profit_to_sky / profit_to_grove`` re-attribution from a
``provenance.json``-equivalent dict.

This module is the canonical source for the per-venue split that the
settlement xlsx (``build_settlement_xlsx.py``) renders in its Summary /
Venues / Sky Revenue tabs.

Inputs:
    prov     : dict — parsed ``provenance.json``
    prime_id : str  — e.g. ``"grove"`` / ``"spark"``

Output:
    (rows, totals) where ``rows`` is a list of per-venue dicts and
    ``totals`` is the headline-level summary.

The math is *exact by construction*:
    Σ_v Profit_to_Sky_v   ≡ sky_revenue (net, after sUSDS spread reimbursements)
    Σ_v Profit_to_Grove_v ≡ prime_agent_revenue

Per-venue formula (mirrors Grove's PnL workbook layout):

    sd_share_v        = 1.0                                 (fixed SDE)
                        sd_revenue / actual_revenue          (capped SDE,
                                                              read from
                                                              provenance)
                        0.0                                  (non-SDE)
    weight_v          = 1 − sd_share_v
    avg_value_v       = max(tw_avg_value_usd, tw_avg_notional_usd)
    sd_revenue_v      = actual + external − revenue          (matches
                                                              upstream
                                                              compute)
    spread_reimb_v    = susds_spread_reimbursement
                        (non-zero only for sky_savings_token Cat B)
    CoF_total         = sky_revenue + Σ spread_reimb_v − Σ sd_revenue_v
                        (sky_revenue is net of spread reimb; add back to
                         recover the gross-BR allocation base)
    cof_alloc_v       = avg_value_v × weight_v / Σ_v(avg × weight) × CoF_total
    profit_to_sky_v   = cof_alloc_v + sd_revenue_v − spread_reimb_v
    profit_to_grove_v = revenue_v − cof_alloc_v
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]


def _D(x) -> Decimal:
    """Parse a JSON / dict value into Decimal. Treats empty / missing as 0."""
    if x is None or x == "":
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    if isinstance(x, bool):
        return Decimal("1") if x else Decimal("0")
    return Decimal(str(x))


def _truthy(x) -> bool:
    """Read a JSON bool or a CSV-style ``"true"``/``"false"`` string."""
    if isinstance(x, bool):
        return x
    return str(x or "").strip().lower() == "true"


def load_sde_entries(prime_id: str, period_start: date) -> dict[str, dict]:
    """Return ``{venue_id: {kind, cap_usd, label, source, end_date}}`` for
    Sky Direct entries active on ``period_start`` for ``prime_id``.

    Active = ``start_date ≤ period_start`` AND
    (``end_date`` is None OR ``end_date ≥ period_start``). Pattern-kind
    entries are skipped — they apply at the prime/PSM3 level, not per-venue.
    """
    path = _REPO / "config" / "sky_direct_exposures.yaml"
    with path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    out: dict[str, dict] = {}
    for section in ("active", "historical"):
        for entry in cfg.get(section, []) or []:
            if entry.get("prime") != prime_id:
                continue
            if entry.get("kind") == "pattern":
                continue
            venue_id = entry.get("venue_id")
            if venue_id is None:
                continue
            start = entry["start_date"]
            end = entry.get("end_date")
            if isinstance(start, str):
                start = date.fromisoformat(start)
            if isinstance(end, str):
                end = date.fromisoformat(end)
            if start > period_start:
                continue
            if end is not None and end < period_start:
                continue
            out[venue_id] = {
                "kind": entry["kind"],
                "cap_usd": entry.get("cap_usd"),
                "label": entry.get("label", ""),
                "source": entry.get("source", ""),
                "end_date": end,
            }
    return out


def _mid_period_sde_blend(
    *,
    value_som: Decimal,
    value_eom: Decimal,
    cap_usd: Decimal | None,
    period_start: date,
    period_end: date,
    sde_end: date,
) -> tuple[Decimal, int, int, int, Decimal] | None:
    """Compute the mid-period-SDE-end CoF-bearing avg_value blend.

    Returns ``None`` when no override is needed (``sde_end`` falls outside
    ``[period_start, period_end)`` — strict upper bound because
    ``sde_end == period_end`` means SDE was active for the full period
    and the override is a no-op).

    Otherwise returns ``(new_avg, sde_days_n, non_sde_days_n,
    total_days_n, grove_excess)``::

        total_days_n   = (period_end − period_start).days + 1   (inclusive)
        sde_days_n     = (sde_end    − period_start).days + 1   (inclusive both ends)
        non_sde_days_n = (period_end − sde_end).days            (strictly after)
        grove_excess   = max(0, value_som − cap_usd)            (capped) | 0 (fixed)
        new_avg        = (grove_excess × sde_days_n
                          + value_eom × non_sde_days_n) / total_days_n

    Day-count convention matches the pipeline gate
    ``_sde_asset_value_timeseries`` (``current > end_date`` → SDE-inactive),
    so ``end_date`` itself is the LAST SDE-active day.
    """
    if not (period_start <= sde_end < period_end):
        return None
    total_days_n   = (period_end - period_start).days + 1
    sde_days_n     = (sde_end - period_start).days + 1
    non_sde_days_n = (period_end - sde_end).days
    total_d   = Decimal(str(total_days_n))
    sde_d     = Decimal(str(sde_days_n))
    non_sde_d = Decimal(str(non_sde_days_n))
    grove_excess = (
        max(Decimal("0"), value_som - cap_usd)
        if cap_usd is not None else Decimal("0")
    )
    new_avg = (grove_excess * sde_d + value_eom * non_sde_d) / total_d
    return new_avg, sde_days_n, non_sde_days_n, total_days_n, grove_excess


def _classify(row: dict, sde: dict[str, dict]) -> tuple[Decimal, Decimal, str]:
    """Returns ``(sd_share, sd_revenue, label_note)`` for a venue row.

    Reads back ``sd_revenue = actual_revenue + external_revenue − revenue``
    from the row's own numbers so the post-hoc split matches the compute
    layer's daily-resolved values exactly, including capped-SDE windows
    and burn-day overrides.
    """
    venue_id = row["venue_id"]
    actual   = _D(row["actual_revenue"])
    revenue  = _D(row["revenue"])
    external = _D(row.get("external_revenue") or 0)
    # revenue = actual − sd_revenue + external_revenue  → sd_revenue =
    # actual + external − revenue (post-2026-05-02 model).
    inferred_sd_revenue = actual + external - revenue

    entry = sde.get(venue_id)
    if entry is None:
        # Non-SDE venue: any non-zero inferred sd_revenue is either numerical
        # noise (clamp silently if tiny) or an indication the compute layer
        # produced SDE revenue without a matching YAML entry (tag for
        # operator visibility).
        if abs(inferred_sd_revenue) <= Decimal("0.01"):
            return Decimal("0"), Decimal("0"), ""
        return (
            inferred_sd_revenue / actual if actual != 0 else Decimal("0"),
            inferred_sd_revenue,
            "(SDE inferred from numbers; not in YAML)",
        )

    kind = entry["kind"]
    if kind == "fixed":
        return Decimal("1"), actual, "SDE (fixed)"
    if kind == "capped":
        # Use the compute layer's daily-resolved number; derive effective
        # share post-hoc for display. Negative sd_revenue preserved
        # (legitimate when a capped tranche took a loss — e.g. burn).
        share = inferred_sd_revenue / actual if actual != 0 else Decimal("0")
        return share, inferred_sd_revenue, f"SDE (capped @ ${entry['cap_usd']:,.0f})"
    return Decimal("0"), Decimal("0"), f"(unknown SDE kind: {kind})"


def compute_sheet_rows(
    prov: dict, prime_id: str,
) -> tuple[list[dict], dict]:
    """Build the per-venue Grove-shaped breakdown from a parsed
    ``provenance.json`` dict. Returns ``(rows, totals)``.

    ``rows`` is a list of per-venue dicts (one row per venue plus optional
    synthetic SPREAD / PSM_CURVE_DEDUCT rows). ``totals`` is the
    headline-level summary used by the xlsx Summary tab.

    Reads venue rows from ``prov["venue_breakdown"]`` — same shape as
    ``venues.csv`` historically, with the same field names.
    """
    period_start = date.fromisoformat(prov["period"]["start"])
    period_end   = date.fromisoformat(prov["period"]["end"])
    n_days       = int(prov["period"]["n_days"])
    headline_sky    = _D(prov["results"]["sky_revenue"])
    headline_prime  = _D(prov["results"]["prime_agent_revenue"])
    headline_agent  = _D(prov["results"]["agent_rate"])
    # ``sky_revenue_gross`` = pure BR × cum_debt (no deductions). Zero on
    # legacy provenance files predating this field.
    headline_sky_gross = _D(prov["results"].get("sky_revenue_gross") or 0)
    # Σ 30bps reimbursement across Cat B ALM + Curve LP + PSM3 sUSDS paths.
    # ``sky_revenue`` is already net of this; add it back when computing
    # ``cof_total`` to recover the gross-BR allocation base. Legacy
    # provenance only carried curve+psm3 components, so we fall back if
    # the aggregate is missing.
    susds_spread_total = _D(prov["results"].get("susds_spread_reimbursement") or 0)
    if susds_spread_total == 0:
        curve_spread = _D(prov["results"].get("curve_susds_spread") or 0)
        psm3_spread  = _D(prov["results"].get("psm3_susds_spread")  or 0)
        susds_spread_total = curve_spread + psm3_spread
    # Curve LP + PSM3 portion of the Sky-Revenue reduction (display-only —
    # used for the synthetic SPREAD row in the per-venue table).
    aggregate_susds_spread = (
        _D(prov["results"].get("curve_susds_spread") or 0)
        + _D(prov["results"].get("psm3_susds_spread") or 0)
    )

    sde = load_sde_entries(prime_id, period_start)
    rows = prov["venue_breakdown"]

    # First pass: classify each venue, compute avg_value × weight.
    enriched: list[dict] = []
    for r in rows:
        sd_share, sd_revenue, note = _classify(r, sde)
        # Time-weighted avg principal (from the compute layer); falls back to
        # the SoM/EoM midpoint if absent (legacy provenance).
        tw_raw = r.get("tw_avg_value_usd")
        if tw_raw not in (None, ""):
            avg_value = _D(tw_raw)
        else:
            avg_value = (_D(r["value_som"]) + _D(r["value_eom"])) / Decimal("2")
            note = (note + " " if note else "") + "(CoF approx)"
        # Off-chain notional principal — venues whose ALM holds $0 on-chain
        # but Sky still charges interest on funded principal (E21 Galaxy CLO).
        notional_raw = r.get("tw_avg_notional_usd")
        if notional_raw not in (None, ""):
            tw_notional = _D(notional_raw)
            if tw_notional > avg_value:
                avg_value = tw_notional
                note = (note + " " if note else "") + "(off-chain notional)"
        # Deduct lending-idle portion before CoF allocation (Cat C/D Spark
        # venues — prime's share of unborrowed underlying is already
        # subtracted from utilized daily).
        lending_idle_tw = _D(r.get("lending_idle_tw_avg_usd") or 0)
        if lending_idle_tw > 0:
            avg_value = max(Decimal("0"), avg_value - lending_idle_tw)
            note = (note + " " if note else "") + "(avg excl. lending_idle)"
        # cof_excluded venues are kept out of the CoF pool (weight=0). Two
        # distinct exclusion reasons share the flag:
        #   - idle-ALM positions already deducted from utilized via
        #     cum_alm_usds (actual_revenue=0).
        #   - Savings V2 depositor-capital vaults never in cum_alm_usds
        #     (actual_revenue<0; the VSR liability accrual).
        # The two paths differ in ``deduction_avg`` below; the sign of
        # ``actual_revenue`` disambiguates.
        cof_excluded = _truthy(r.get("cof_excluded"))
        cof_excluded_savings_v2 = (
            cof_excluded and _D(r.get("actual_revenue") or 0) < 0
        )
        weight = Decimal("0") if cof_excluded else Decimal("1") - sd_share
        if cof_excluded and not note:
            note = (
                "CoF excluded (Savings V2 depositor capital; not in cum_alm_usds)"
                if cof_excluded_savings_v2
                else "CoF excluded (already deducted from utilized)"
            )

        # Mid-period SDE end: when the SDE designation covers only part of
        # the period, blend Grove's excess-above-cap during SDE days with
        # full-position post-SDE. See ``_mid_period_sde_blend`` docstring.
        avg_value_pre_override = avg_value
        entry = sde.get(r["venue_id"])
        if entry and not cof_excluded:
            sde_end = entry.get("end_date")
            if sde_end is not None:
                if isinstance(sde_end, str):
                    sde_end = date.fromisoformat(sde_end)
                cap_usd_d = (
                    Decimal(str(entry["cap_usd"]))
                    if entry.get("cap_usd") is not None else None
                )
                blend = _mid_period_sde_blend(
                    value_som=_D(r["value_som"]),
                    value_eom=_D(r["value_eom"]),
                    cap_usd=cap_usd_d,
                    period_start=period_start,
                    period_end=period_end,
                    sde_end=sde_end,
                )
                if blend is not None:
                    new_avg, sde_days_n, non_sde_days_n, total_days_n, grove_excess = blend
                    value_eom_d = _D(r["value_eom"])
                    weight = Decimal("1")
                    avg_value = new_avg
                    note = (note + " — " if note else "") + (
                        f"SDE ended {sde_end.isoformat()}; "
                        f"CoF: excess ${float(grove_excess):,.0f}×{sde_days_n}d "
                        f"+ EoM ${float(value_eom_d):,.0f}×{non_sde_days_n}d "
                        f"/ {total_days_n}d → avg ${float(new_avg):,.0f}"
                    )

        # Utilized deduction (display-only): how much this venue subtracts
        # from utilized on average. Used by xlsx Sky Revenue tab. Savings V2
        # depositor-capital vaults are never in cum_alm_usds, so their
        # utilized deduction is $0 — only idle-ALM ``cof_excluded`` venues
        # contribute to ``total_known_deduction``.
        if cof_excluded and cof_excluded_savings_v2:
            deduction_avg = Decimal("0")
        elif cof_excluded:
            deduction_avg = avg_value_pre_override
        elif lending_idle_tw > 0:
            deduction_avg = lending_idle_tw
        elif sd_share >= Decimal("0.999"):   # fixed SDE
            deduction_avg = avg_value_pre_override
        elif entry is not None and entry.get("kind") == "capped" and entry.get("cap_usd"):
            deduction_avg = min(Decimal(str(entry["cap_usd"])), avg_value_pre_override)
        else:
            deduction_avg = Decimal("0")

        enriched.append({
            "venue_id":      r["venue_id"],
            "label":         r["label"],
            "value_som":     _D(r["value_som"]),
            "value_eom":     _D(r["value_eom"]),
            "avg_value":     avg_value,
            "sd_share":      sd_share,
            "weight":        weight,
            "actual_rev":    _D(r["actual_revenue"]),
            "external":      _D(r.get("external_revenue") or 0),
            "revenue":       _D(r["revenue"]),
            "sd_revenue":    sd_revenue,
            "spread_reimb":  _D(r.get("susds_spread_reimbursement") or 0),
            "deduction_avg": deduction_avg,
            "hide_per_venue_pnl": _truthy(r.get("hide_per_venue_pnl")),
            "note":          note,
        })

    # Synthetic row: 30bps Sky Revenue reduction for Curve LP + PSM3 sUSDS.
    if aggregate_susds_spread != 0:
        enriched.append({
            "venue_id":      "SPREAD",
            "label":         "30bps sUSDS spread (Curve LP + PSM3) — Sky Revenue reduction",
            "value_som":     Decimal("0"),
            "value_eom":     Decimal("0"),
            "avg_value":     Decimal("0"),
            "sd_share":      Decimal("0"),
            "weight":        Decimal("0"),
            "actual_rev":    Decimal("0"),
            "external":      Decimal("0"),
            "revenue":       Decimal("0"),
            "sd_revenue":    Decimal("0"),
            "spread_reimb":  aggregate_susds_spread,
            "deduction_avg": Decimal("0"),
            "note":          "sky-revenue reduction (no CoF; computed outside venue loop)",
        })

    # CoF on Net_Subs = gross BR base minus SDE revenue. headline_sky is
    # net of the sUSDS spread reimbursement; add it back to recover the
    # gross-BR allocation base.
    total_sd_revenue = sum((v["sd_revenue"] for v in enriched), Decimal("0"))
    cof_total = headline_sky + susds_spread_total - total_sd_revenue

    total_weighted = sum(
        (v["avg_value"] * v["weight"] for v in enriched), Decimal("0"),
    )

    # Per-venue Sky Revenue reduction estimate.
    sky_rev_br_reduction = max(Decimal("0"), headline_sky_gross - cof_total)
    total_known_deduction = sum((v["deduction_avg"] for v in enriched), Decimal("0"))
    effective_br_daily = (
        cof_total / total_weighted / Decimal(n_days)
        if total_weighted > 0 and n_days > 0 else Decimal("0")
    )
    total_known_br_est = total_known_deduction * effective_br_daily * Decimal(n_days)
    psm_curve_residual = max(Decimal("0"), sky_rev_br_reduction - total_known_br_est)

    # Second pass: allocate CoF, derive P2S / P2G, estimate sky_rev reduction.
    for v in enriched:
        if total_weighted > 0:
            v["cof_alloc"] = (
                v["avg_value"] * v["weight"] / total_weighted * cof_total
            )
        else:
            v["cof_alloc"] = Decimal("0")
        v["profit_to_sky"]   = v["cof_alloc"] + v["sd_revenue"] - v["spread_reimb"]
        v["profit_to_grove"] = v["revenue"] - v["cof_alloc"]
        utilized_est = (
            v["deduction_avg"] * effective_br_daily * Decimal(n_days)
            if v["deduction_avg"] > 0 else Decimal("0")
        )
        v["sky_rev_reduction_est"] = v["spread_reimb"] + utilized_est

    # Synthetic row for PSM3 USDS + Curve idle USDS residual. Appended
    # AFTER the second pass so its sky_rev_reduction_est isn't overwritten.
    if psm_curve_residual > Decimal("1"):
        enriched.append({
            "venue_id":              "PSM_CURVE_DEDUCT",
            "label":                 "PSM3 USDS + PSM3 USDC (SDE) + Curve idle USDS — BR deduction (unattributed)",
            "value_som":             Decimal("0"),
            "value_eom":             Decimal("0"),
            "avg_value":             Decimal("0"),
            "sd_share":              Decimal("0"),
            "weight":                Decimal("0"),
            "actual_rev":            Decimal("0"),
            "external":              Decimal("0"),
            "revenue":               Decimal("0"),
            "sd_revenue":            Decimal("0"),
            "spread_reimb":          Decimal("0"),
            "cof_alloc":             Decimal("0"),
            "profit_to_sky":         Decimal("0"),
            "profit_to_grove":       Decimal("0"),
            "deduction_avg":         Decimal("0"),
            "sky_rev_reduction_est": psm_curve_residual,
            "note": (
                "BR deduction from PSM3 USDS leg + PSM3 USDC leg (SDE, "
                "≈$0 yield) + Curve idle USDS — not tracked per-venue; "
                "residual = sky_rev_br_reduction − "
                "Σ(known venue deduction_avg × effective_br_rate × n_days)"
            ),
        })

    totals = {
        "sky_revenue":              headline_sky,
        "sky_revenue_gross":        headline_sky_gross,
        "sky_rev_br_reduction":     sky_rev_br_reduction,
        "prime_agent_revenue":      headline_prime,
        "agent_rate":               headline_agent,
        "cof_total":                cof_total,
        "sd_revenue_total":         total_sd_revenue,
        "susds_spread_reimb_total": susds_spread_total,
        "sum_p2s":                  sum((v["profit_to_sky"]   for v in enriched), Decimal("0")),
        "sum_p2g":                  sum((v["profit_to_grove"] for v in enriched), Decimal("0")),
    }
    return enriched, totals
