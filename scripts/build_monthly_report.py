"""Render a Grove-spreadsheet-shaped breakdown from a settlement output.

The canonical settlement output (``settlements/{prime}/{month}/venues.csv``)
reports per-venue ``revenue`` (= what flows to the prime) and a single
aggregate ``sky_revenue`` in ``pnl.md``. Grove's PnL workbook splits the
CoF charge across venues by ``avg_value × Grove_weight`` so the reader can
see, per venue, "what did Sky take" and "what did Grove keep".

This script derives that view post-hoc, read-only. No methodology changes —
totals are exact by construction:

    Σ_v Profit_to_Sky_v   ≡ sky_revenue (net, after sUSDS spread reimbursements)
    Σ_v Profit_to_Grove_v ≡ prime_agent_revenue

Inputs:
    settlements/{prime}/{month}/venues.csv      — per-venue rows
    settlements/{prime}/{month}/provenance.json — headline totals + period
    config/sky_direct_exposures.yaml            — fixed/capped SDE entries

Outputs (alongside the inputs):
    grove_sheet.csv  — machine-readable Grove-style breakdown
    grove_sheet.md   — human-readable side-by-side table

Per-venue math:

    sd_share_v        = 1.0                                 (fixed SDE)
                        min(cap, value_eom) / value_eom     (capped SDE,
                                                             EoM-locked —
                                                             read post-hoc as
                                                             sd_revenue / actual)
                        0.0                                 (non-SDE)
    weight_v          = 1 − sd_share_v
    avg_value_v       = max(tw_avg_value_usd,               # principal
                            tw_avg_notional_usd)            # max() picks
                                                            # off-chain notional
                                                            # for cash-distribution
                                                            # venues whose ALM
                                                            # holds $0 on-chain
    sd_revenue_v      = actual_revenue                       (fixed)
                        actual + external − revenue           (capped, post-hoc)
                        0                                    (non-SDE)
    spread_reimb_v    = 30bps × value_som × n_days for sky_savings_token Cat B
                        venues; 0 otherwise (per-venue susds_spread_reimbursement)
    CoF_total         = sky_revenue + Σ spread_reimb_v − Σ sd_revenue_v
                        # sky_revenue is net of the spread reimbursement; add it
                        # back to recover the gross-BR base for allocation
    cof_alloc_v       = avg_value_v × weight_v / Σ_v(avg × weight) × CoF_total
    profit_to_sky_v   = cof_alloc_v + sd_revenue_v − spread_reimb_v
    profit_to_grove_v = revenue_v − cof_alloc_v          # revenue already
                                                          # excludes SDE part

``avg_value_v`` — time-weighted vs SoM/EoM-avg:
    Reads ``tw_avg_value_usd`` from venues.csv when present — the true
    time-weighted mean of daily principal computed by the compute layer
    (``_time_weighted_avg_value`` in compute.prime_agent_revenue). Falls
    back to the SoM/EoM average for legacy venues.csv files written
    before that column was added. The fallback is inaccurate for venues
    with concentrated mid-month inflows/outflows — a $300M deposit on
    day 28 produces a true time-weighted avg of ~$38M but a SoM/EoM avg
    of $150M (3.9× over-stated), inflating that venue's CoF allocation
    and deflating others'. Σ-totals (sky_revenue, prime_agent_revenue,
    sum_p2s, sum_p2g) stay exact regardless — only the per-venue split
    is approximate. Rows using the fallback are tagged ``(CoF approx)``.

    The fallback path can be removed (along with the ``(CoF approx)`` tag)
    once all historical venues.csv files on disk have been regenerated.

Limitations:
* For capped SDE the ``sd_revenue_v`` is read back exactly from
  ``actual + external − revenue`` (the upstream compute layer's EoM-locked
  result, see ``_capped_sd_revenue_eom_locked``). Negative values are
  preserved — e.g. when a capped tranche is destroyed mid-period, Sky
  absorbs the loss and an earlier neg-clamp would have hidden it.
* No SDE inputs from ``sky_direct_exposures.yaml`` are *required* to run —
  the SDE slice can also be inferred as ``sd_revenue_v = actual_revenue_v
  − revenue_v`` (the part of pool yield that didn't flow to the prime).
  We use the YAML for traceability + the capped-window check; if both
  agree, great, and if they don't we surface the discrepancy.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parent.parent


def _D(x) -> Decimal:
    """Parse a CSV / JSON string into Decimal. Treats empty / missing as 0."""
    if x is None or x == "":
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def _load_sde_entries(prime_id: str, period_start: date) -> dict[str, dict]:
    """Return ``{venue_id: {kind, sd_share_hint, ...}}`` for entries that are
    active on ``period_start`` for ``prime_id``. Active = ``start_date <=
    period_start <= end_date`` (end_date null treated as +∞)."""
    path = _REPO / "config" / "sky_direct_exposures.yaml"
    with path.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    out: dict[str, dict] = {}
    for section in ("active", "historical"):
        for entry in cfg.get(section, []) or []:
            if entry.get("prime") != prime_id:
                continue
            if entry.get("kind") == "pattern":
                # Pattern entries apply at the prime/PSM3 level, not to named
                # venues — skip here; they don't show up in venues.csv.
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


def _read_venues(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


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
    the in-period window ``[period_start, period_end)`` — strict upper
    bound because ``sde_end == period_end`` means SDE was active for the
    full period and the override is a no-op).

    Otherwise returns ``(new_avg, sde_days_n, non_sde_days_n, total_days_n,
    grove_excess)`` where::

        total_days_n   = (period_end − period_start).days + 1  (inclusive)
        sde_days_n     = (sde_end    − period_start).days + 1  (inclusive both ends)
        non_sde_days_n = (period_end − sde_end).days           (strictly after)
        grove_excess   = max(0, value_som − cap_usd)           (capped) | 0 (fixed)
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

    ``sd_share`` is the effective fraction of the venue that is Sky-direct
    over this period (used to derive ``weight = 1 − sd_share``).
    ``sd_revenue`` is the part of ``actual_revenue`` that goes to Sky.
    We prefer reading ``sd_revenue`` from the output's own numbers
    (``actual_revenue − revenue − external_revenue``) so post-processing
    matches the canonical compute layer exactly, including capped-SDE
    windows the compute layer already accounted for daily.
    """
    venue_id = row["venue_id"]
    actual = _D(row["actual_revenue"])
    revenue = _D(row["revenue"])
    external = _D(row.get("external_revenue") or 0)
    # revenue = actual − sd_revenue + external_revenue  (post-2026-05-02 model)
    # → sd_revenue = actual + external − revenue
    inferred_sd_revenue = actual + external - revenue

    entry = sde.get(venue_id)
    if entry is None:
        # Non-SDE venue: any non-zero inferred sd_revenue is either numerical
        # noise (clamp silently if tiny) or an indication the compute layer
        # produced SDE revenue without a matching YAML entry (surface as a
        # tagged row so the inconsistency is visible).
        if abs(inferred_sd_revenue) <= Decimal("0.01"):
            return Decimal("0"), Decimal("0"), ""
        return (
            inferred_sd_revenue / actual if actual != 0 else Decimal("0"),
            inferred_sd_revenue,
            "(SDE inferred from numbers; not in YAML)",
        )

    kind = entry["kind"]
    if kind == "fixed":
        # 100 % of the venue is SDE. sd_revenue = whole actual_revenue.
        return Decimal("1"), actual, "SDE (fixed)"
    if kind == "capped":
        # Use compute layer's already-daily-resolved number; derive effective
        # sd_share post-hoc for display. Preserve negative sd_revenue —
        # legitimate when the capped position took a loss (e.g., a tranche
        # was burned mid-period). Earlier versions clamped negatives to 0,
        # which hid Sky's loss in the rendered output.
        share = inferred_sd_revenue / actual if actual != 0 else Decimal("0")
        return share, inferred_sd_revenue, f"SDE (capped @ ${entry['cap_usd']:,.0f})"
    # Unknown kind — be conservative and treat as 0.
    return Decimal("0"), Decimal("0"), f"(unknown SDE kind: {kind})"


def build_sheet(prime_id: str, month: str) -> tuple[list[dict], dict]:
    """Render the Grove-style breakdown for ``settlements/{prime}/{month}``."""
    cell = _REPO / "settlements" / prime_id / month
    venues_csv = cell / "venues.csv"
    prov_json  = cell / "provenance.json"
    if not venues_csv.exists() or not prov_json.exists():
        raise SystemExit(f"Missing artifacts under {cell}")

    with prov_json.open(encoding="utf-8") as f:
        prov = json.load(f)
    period_start = date.fromisoformat(prov["period"]["start"])
    period_end   = date.fromisoformat(prov["period"]["end"])
    headline_sky    = _D(prov["results"]["sky_revenue"])
    headline_prime  = _D(prov["results"]["prime_agent_revenue"])
    headline_agent  = _D(prov["results"]["agent_rate"])
    # sky_revenue_gross: what sky_revenue would be with utilized = cum_debt.
    # Zero on legacy provenance files that pre-date this field.
    headline_sky_gross = _D(prov["results"].get("sky_revenue_gross") or 0)
    # Total 30 bps spread deducted from sky_revenue across ALL sky_savings_token
    # paths (Cat B ALM venues + Curve LP sUSDS + PSM3 sUSDS leg). headline_sky
    # is already net of this deduction, so we add it back when computing
    # cof_total to recover the gross-BR allocation base.
    # Falls back to 0 on legacy provenance files written before this field was
    # introduced; also backfill curve+psm3 components if present separately.
    susds_spread_total = _D(prov["results"].get("susds_spread_reimbursement") or 0)
    if susds_spread_total == 0:
        curve_spread = _D(prov["results"].get("curve_susds_spread") or 0)
        psm3_spread  = _D(prov["results"].get("psm3_susds_spread")  or 0)
        susds_spread_total = curve_spread + psm3_spread
    # Curve LP + PSM3 portion of the Sky-Revenue reduction (display-only — used
    # for the synthetic SPREAD row in the per-venue table).
    aggregate_susds_spread = (
        _D(prov["results"].get("curve_susds_spread") or 0)
        + _D(prov["results"].get("psm3_susds_spread") or 0)
    )

    sde = _load_sde_entries(prime_id, period_start)
    rows = _read_venues(venues_csv)

    # First pass: classify each venue, compute avg_value × weight.
    enriched: list[dict] = []
    for r in rows:
        sd_share, sd_revenue, note = _classify(r, sde)
        # Prefer the compute-layer-written time-weighted average; fall back
        # to SoM/EoM avg on legacy venues.csv files. See module docstring
        # "avg_value_v — time-weighted vs SoM/EoM-avg" section for the
        # accuracy implications of the fallback.
        tw_raw = r.get("tw_avg_value_usd")
        if tw_raw not in (None, ""):
            avg_value = _D(tw_raw)
        else:
            avg_value = (_D(r["value_som"]) + _D(r["value_eom"])) / Decimal("2")
            note = (note + " " if note else "") + "(CoF approx)"
        # Cash-distribution-only venues (E21 Galaxy CLO, etc.) have
        # ``tw_avg_value_usd = $0`` on-chain but Sky still charges interest
        # on the off-chain notional principal funding them. Use the larger
        # of on-chain tw_avg and the configured time-weighted notional so
        # those venues participate in the CoF allocation pool.
        #
        # Σ-invariance: this only shifts the per-venue CoF split — sky_revenue,
        # prime_agent_revenue, and monthly_pnl come from the upstream compute
        # layer (which never reads ``tw_avg_notional_usd``) and stay exact
        # whether the field is configured or not.
        notional_raw = r.get("tw_avg_notional_usd")
        if notional_raw not in (None, ""):
            tw_notional = _D(notional_raw)
            if tw_notional > avg_value:
                avg_value = tw_notional
                note = (note + " " if note else "") + "(off-chain notional)"
        # Deduct the lending-idle portion from avg_value before CoF allocation.
        # For Cat C/D venues with lending_idle_usds=true (e.g. S1 spUSDS, S4
        # spDAI), the prime's share of unborrowed underlying is already subtracted
        # from utilized daily. Allocating CoF on the full avg_value would
        # double-charge that idle slice.
        lending_idle_tw = _D(r.get("lending_idle_tw_avg_usd") or 0)
        if lending_idle_tw > 0:
            avg_value = max(Decimal("0"), avg_value - lending_idle_tw)
            note = (note + " " if note else "") + "(avg excl. lending_idle)"
        # cof_excluded venues (idle USDS/USDC at the ALM proxy) are already
        # deducted from `utilized` via cum_alm_usds, so they owe no CoF.
        # Setting weight=0 keeps them out of the allocation denominator,
        # producing profit_to_sky=0 and profit_to_grove=revenue (≈0 for idle).
        cof_excluded = r.get("cof_excluded", "").lower() == "true"
        weight = Decimal("0") if cof_excluded else Decimal("1") - sd_share
        if cof_excluded and not note:
            note = "CoF excluded (already deducted from utilized)"

        # Mid-period SDE end: the SDE designation covers only part of the period.
        # For a capped SDE, Grove held the excess above the cap throughout the SDE
        # days — that slice bears CoF even before the SDE ended. After the end_date,
        # Grove holds the full remaining position (value_eom) and owes CoF on all of
        # it. Override avg_value and weight to reflect both contributions:
        #
        #   avg_value = (grove_excess_above_cap × sde_days
        #                + value_eom × non_sde_days) / total_days
        #   weight    = 1   (avg_value already isolates Grove's portion)
        #
        # For fixed SDE (no cap), Grove has zero excess during the SDE period,
        # so only the post-SDE term contributes.
        #
        # Day-count convention: the pipeline gates SDE-inactive days with
        # ``current > end_date`` (see ``_sde_asset_value_timeseries``), so
        # ``end_date`` itself is the LAST SDE-active day. SDE window is
        # ``[period_start, sde_end]`` (inclusive both ends); non-SDE window
        # is ``(sde_end, period_end]`` (strictly after sde_end).
        #
        # Example: E8 JAAA Eth, Mar 2026 — SDE capped at $325M, ended 2026-03-12.
        #   Grove excess during SDE (Mar 1-12, 12d): $455M - $325M = $130M × 12/31 = $50.3M
        #   Post-SDE (Mar 13-31, 19d):               $128M × 19/31                 = $78.5M
        #   Total CoF-bearing avg ≈ $128.8M   (vs $0 without this override)
        # Preserve the pre-override avg_value for deduction_avg below —
        # the utilized-deduction estimate must reflect what the pipeline
        # actually subtracted from utilized (≈ time-averaged capped value),
        # NOT the post-override Grove-portion blend.
        avg_value_pre_override = avg_value
        entry = sde.get(r["venue_id"])
        if entry and not cof_excluded:
            sde_end = entry.get("end_date")
            if sde_end is not None:
                if isinstance(sde_end, str):
                    sde_end = date.fromisoformat(sde_end)
                # Approximation: ``value_som`` is used as a flat baseline
                # for the uncapped asset value during the SDE-active window.
                # The accurate computation would time-average
                # ``uncapped_value`` from ``sde_daily_breakdown`` over the
                # SDE days, but that series is not threaded into the report
                # builder. The error is bounded by daily price drift across
                # the SDE window (typically <1% on a $300M position); for
                # the JAAA Mar 2026 example the daily uncapped value held
                # within $5M of value_som over the 12 SDE days.
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

        # Utilized deduction: the amount this venue subtracts from utilized.
        # cof_excluded venues → their tw_avg is alm_proxy_usds deduction.
        # lending_idle venues → lending_idle_tw_avg_usd is the deduction.
        # SDE fixed venues   → their avg_value is subtracted as sde_asset_value.
        # All other venues   → do not reduce utilized (they're deployed at BR).
        #
        # ``avg_value_pre_override`` is used in place of ``avg_value`` so
        # mid-period-SDE venues report the pipeline-aligned deduction (≈
        # time-avg capped value), not the post-override Grove-portion blend
        # — the latter would understate the deduction the pipeline actually
        # applied during the SDE-active window.
        if cof_excluded:
            deduction_avg = avg_value_pre_override
        elif lending_idle_tw > 0:
            deduction_avg = lending_idle_tw
        elif sd_share >= Decimal("0.999"):   # fixed SDE (100% to Sky)
            deduction_avg = avg_value_pre_override
        elif entry is not None and entry.get("kind") == "capped" and entry.get("cap_usd"):
            # Capped SDE: the pipeline subtracts min(cap, value) from utilized
            # each day. Use min(cap, avg_value_pre_override) as the
            # utilized-deduction estimate.
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
            "revenue":       _D(r["revenue"]),       # already net of SDE
            "sd_revenue":    sd_revenue,
            # 30 bps spread deducted from Sky Revenue for this venue.
            # Non-zero only for sky_savings_token Cat B venues; read from
            # venues.csv where the compute layer plumbed it through.
            "spread_reimb":  _D(r.get("susds_spread_reimbursement") or 0),
            "deduction_avg": deduction_avg,   # avg amount reducing utilized
            "note":          note,
        })

    # Synthetic row: 30 bps Sky Revenue reduction for Curve LP + PSM3 sUSDS.
    # Consistent with Cat B ALM venues (per d255ed2): the spread reduces Sky
    # Revenue rather than increasing Prime Revenue. ``spread_reimb`` makes
    # ``profit_to_sky`` negative on this row; the deficit is offset by higher
    # ``cof_alloc`` on the regular venues. Weight=0 keeps it out of the CoF
    # pool.
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
            "deduction_avg": Decimal("0"),  # sUSDS not deducted from utilized
            "note":          "sky-revenue reduction (no CoF; computed outside venue loop)",
        })

    # CoF on Net_Subs = gross BR base minus SDE revenue.
    # headline_sky is net of the sUSDS spread reimbursement; add it back to
    # recover the gross-BR allocation base (spread_reimb is a Sky-Revenue
    # reduction applied after CoF calculation, not a reduction of the base).
    total_sd_revenue = sum((v["sd_revenue"] for v in enriched), Decimal("0"))
    cof_total = headline_sky + susds_spread_total - total_sd_revenue

    total_weighted = sum(
        (v["avg_value"] * v["weight"] for v in enriched), Decimal("0"),
    )

    # Second pass: allocate CoF, derive P2S / P2G.
    for v in enriched:
        if total_weighted > 0:
            v["cof_alloc"] = (
                v["avg_value"] * v["weight"] / total_weighted * cof_total
            )
        else:
            v["cof_alloc"] = Decimal("0")
        # profit_to_sky = CoF share + SDE revenue − 30bps spread reimbursement.
        # Σ profit_to_sky ≡ headline_sky (net sky_revenue) by construction.
        v["profit_to_sky"]   = v["cof_alloc"] + v["sd_revenue"] - v["spread_reimb"]
        v["profit_to_grove"] = v["revenue"] - v["cof_alloc"]

    totals = {
        "sky_revenue":              headline_sky,
        "sky_revenue_gross":        headline_sky_gross,
        "prime_agent_revenue":      headline_prime,
        "agent_rate":               headline_agent,
        "cof_total":                cof_total,
        "sd_revenue_total":         total_sd_revenue,
        "susds_spread_reimb_total": susds_spread_total,
        "sum_p2s":                  sum((v["profit_to_sky"]   for v in enriched), Decimal("0")),
        "sum_p2g":                  sum((v["profit_to_grove"] for v in enriched), Decimal("0")),
    }
    return enriched, totals


def _emit_csv(rows: list[dict], out: Path) -> None:
    cols = [
        "venue_id", "label", "value_som", "value_eom", "avg_value",
        "sd_share", "weight", "actual_rev", "external", "revenue",
        "sd_revenue", "spread_reimb", "deduction_avg",
        "cof_alloc", "profit_to_sky", "profit_to_grove", "note",
    ]
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: str(r[c]) for c in cols})


def _fmt_usd(x: Decimal) -> str:
    return f"${float(x):>14,.2f}"


def _fmt_pct(x: Decimal) -> str:
    return f"{float(x) * 100:>6.2f}%"


def _emit_markdown(
    rows: list[dict], totals: dict, prime_id: str, month: str, out: Path,
) -> None:
    lines: list[str] = []
    lines.append(f"# {prime_id.upper()} — Grove-sheet-shaped breakdown {month}\n")
    lines.append(
        "Post-processor that re-attributes the aggregate `sky_revenue` "
        "across venues by `avg_value × Grove_weight`, matching the layout "
        "of Grove's PnL workbook. Totals are exact by construction.\n"
    )
    sum_p2g = totals["sum_p2g"]
    lines.append("## Headline\n")
    lines.append("| Component | Amount (USD) |")
    lines.append("|---|---:|")
    lines.append(f"| Σ Profit to Sky ≡ `sky_revenue` (net) | {_fmt_usd(totals['sky_revenue'])} |")
    lines.append(f"| &nbsp;&nbsp;↳ CoF on Net_Subs (BR × utilized) | {_fmt_usd(totals['cof_total'])} |")
    lines.append(f"| &nbsp;&nbsp;↳ SDE revenue (full flow to Sky) | {_fmt_usd(totals['sd_revenue_total'])} |")
    if totals["susds_spread_reimb_total"] != 0:
        lines.append(
            f"| &nbsp;&nbsp;↳ sUSDS spread reimb. (−Sky Revenue, sky_savings_token Cat B) "
            f"| −{_fmt_usd(totals['susds_spread_reimb_total'])} |"
        )
    if totals["sky_revenue_gross"] > 0:
        lines.append(f"| **Sky Revenue (max) — BR × full ilk debt, no deductions** | **{_fmt_usd(totals['sky_revenue_gross'])}** |")
        lines.append(f"| &nbsp;&nbsp;↳ CoF on Net_Subs (actual BR × utilized) | {_fmt_usd(totals['cof_total'])} |")
        sky_rev_br_reduction = max(Decimal("0"), totals["sky_revenue_gross"] - totals["cof_total"])
        lines.append(f"| &nbsp;&nbsp;↳ reduction from idle/SDE deductions | −{_fmt_usd(sky_rev_br_reduction)} |")
    lines.append(f"| Σ Grove Net Payment (= `prime_agent_revenue` − CoF) | {_fmt_usd(sum_p2g)} |")
    lines.append(f"| &nbsp;&nbsp;↳ `prime_agent_revenue` (per-venue revenue total) | {_fmt_usd(totals['prime_agent_revenue'])} |")
    lines.append(f"| &nbsp;&nbsp;↳ CoF deducted by Grove (= cof_total above) | -{_fmt_usd(totals['cof_total'])} |")
    lines.append(f"| `agent_rate` (subproxy yield, off-sheet) | {_fmt_usd(totals['agent_rate'])} |")
    lines.append("")
    # Reconciliation identities — by construction:
    #   Σ P2S            ≡ sky_revenue
    #   Σ P2G + cof_total ≡ prime_agent_revenue
    # (Σ P2G is what Grove's sheet calls "Net to Grove" — already excludes
    # the CoF charge, hence the +cof_total to round-trip to prime_agent_revenue.)
    drift_p2s = totals["sum_p2s"] - totals["sky_revenue"]
    drift_p2g = totals["sum_p2g"] + totals["cof_total"] - totals["prime_agent_revenue"]
    lines.append("**Reconciliation (totals exact by construction):**")
    lines.append(f"- Σ Profit to Sky ≡ sky_revenue → drift {_fmt_usd(drift_p2s)} {'✓' if abs(drift_p2s) < Decimal('0.01') else '✗'}")
    lines.append(f"- Σ Grove Net Payment + CoF_total ≡ prime_agent_revenue → drift {_fmt_usd(drift_p2g)} {'✓' if abs(drift_p2g) < Decimal('0.01') else '✗'}")
    lines.append("")
    lines.append(
        "_Note: Grove's \"Grove Net Payment\" (Σ GNP) is **after** subtracting the "
        "per-venue CoF allocation. Adding back `cof_total` returns the canonical "
        "`prime_agent_revenue` from `pnl.md`._"
    )
    lines.append("")

    lines.append("## Per-venue breakdown\n")
    lines.append(
        "| Venue | Label | avg_value | weight | Profit to Sky | Revenue | Grove Net Payment | "
        "CoF alloc | SDE rev | Spread Reimb | Utilized Deduction (avg) | Note |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    # Sort by Profit to Sky desc to make the sheet read like Grove's.
    for r in sorted(rows, key=lambda v: float(v["profit_to_sky"]), reverse=True):
        lines.append(
            f"| {r['venue_id']} | {r['label']} | {_fmt_usd(r['avg_value'])} | "
            f"{_fmt_pct(r['weight'])} | {_fmt_usd(r['profit_to_sky'])} | "
            f"{_fmt_usd(r['revenue'])} | {_fmt_usd(r['profit_to_grove'])} | "
            f"{_fmt_usd(r['cof_alloc'])} | "
            f"{_fmt_usd(r['sd_revenue'])} | {_fmt_usd(r['spread_reimb'])} | "
            f"{_fmt_usd(r['deduction_avg'])} | {r['note']} |"
        )
    lines.append("")
    lines.append("## Formulas\n")
    lines.append(
        "```\n"
        "sd_share_v        = 1.0 for fixed SDE; for capped SDE inferred\n"
        "                     post-hoc as sd_revenue / actual_revenue (EoM-locked\n"
        "                     upstream — see _capped_sd_revenue_eom_locked)\n"
        "weight_v          = 1 − sd_share_v\n"
        "avg_value_v       = venues.csv:tw_avg_value_usd        # time-weighted (preferred)\n"
        "                    or  (value_som + value_eom) / 2    # legacy fallback (CoF approx)\n"
        "sd_revenue_v      = actual_revenue + external_revenue − revenue\n"
        "spread_reimb_v    = venues.csv:susds_spread_reimbursement   # non-zero for sky_savings_token Cat B only\n"
        "cof_total         = sky_revenue + Σ_v spread_reimb_v − Σ_v sd_revenue_v\n"
        "                    # (sky_revenue is net; add back spread to recover gross-BR base)\n"
        "cof_alloc_v       = avg_value_v × weight_v / Σ_v(avg × weight) × cof_total\n"
        "profit_to_sky_v      = cof_alloc_v + sd_revenue_v − spread_reimb_v\n"
        "grove_net_payment_v  = revenue_v − cof_alloc_v\n"
        "# Invariants: Σ profit_to_sky ≡ sky_revenue   Σ GNP + cof_total ≡ prime_agent_revenue\n"
        "\n"
        "# Utilized deduction column (display-only, no settlement effect):\n"
        "deduction_avg_v = tw_avg_value if cof_excluded\n"
        "                  else lending_idle_tw_avg if lending_idle > 0\n"
        "                  else avg_value if fixed SDE\n"
        "                  else min(cap_usd, avg_value) if capped SDE\n"
        "                  else 0\n"
        "# Shows how much each venue reduces 'utilized' on average, i.e. the\n"
        "# principal that is NOT subject to BR charges. Exact per-venue.\n"
        "```\n"
    )
    out.write_text("\n".join(lines), encoding="utf-8")


def _debug_venue(venue_id: str, rows: list[dict], totals: dict) -> None:
    """Print a detailed math trace for one venue to stdout."""
    match = [r for r in rows if r["venue_id"] == venue_id]
    if not match:
        print(f"[debug] venue '{venue_id}' not found in sheet rows")
        print(f"[debug] available ids: {[r['venue_id'] for r in rows]}")
        return
    v = match[0]

    cof_total   = totals["cof_total"]
    total_w     = sum(_D(r["avg_value"]) * _D(r["weight"]) for r in rows)
    sky_revenue = totals["sky_revenue"]
    sd_total    = totals["sd_revenue_total"]
    spread_total = totals["susds_spread_reimb_total"]

    print(f"\n{'═'*72}")
    print(f"  DEBUG: {venue_id} — {v['label']}")
    print(f"{'═'*72}")
    print(f"\n  ── Inputs from venues.csv ────────────────────────────────────────")
    print(f"  value_som          = {_fmt_usd(_D(v['value_som']))}")
    print(f"  value_eom          = {_fmt_usd(_D(v['value_eom']))}")
    print(f"  avg_value (tw_avg) = {_fmt_usd(_D(v['avg_value']))}")
    print(f"  actual_revenue     = {_fmt_usd(_D(v['actual_rev']))}")
    print(f"  revenue (→ prime)  = {_fmt_usd(_D(v['revenue']))}")
    print(f"  external_revenue   = {_fmt_usd(_D(v['external']))}")
    print(f"  sd_revenue         = {_fmt_usd(_D(v['sd_revenue']))}  (= actual − revenue − external)")
    print(f"  sd_share (eff.)    = {_fmt_pct(_D(v['sd_share']))}")
    print(f"  spread_reimb       = {_fmt_usd(_D(v['spread_reimb']))}")
    print(f"  weight             = {_fmt_pct(_D(v['weight']))}  (= 1 − sd_share)")

    print(f"\n  ── Aggregate context (all venues) ───────────────────────────────")
    print(f"  sky_revenue (net)       = {_fmt_usd(sky_revenue)}")
    print(f"  susds_spread_reimb_total= {_fmt_usd(spread_total)}")
    print(f"  sd_revenue_total        = {_fmt_usd(sd_total)}")
    print(f"  cof_total               = {_fmt_usd(cof_total)}")
    print(f"    = sky_revenue + spread_total − sd_total")
    print(f"    = {_fmt_usd(sky_revenue)} + {_fmt_usd(spread_total)} − {_fmt_usd(sd_total)}")
    print(f"  Σ(avg × weight)         = {_fmt_usd(total_w)}")

    print(f"\n  ── Per-venue grove-sheet math ────────────────────────────────────")
    avg_v = _D(v["avg_value"])
    wt    = _D(v["weight"])
    cof_alloc = avg_v * wt / total_w * cof_total if total_w > 0 else _D("0")
    p2s   = cof_alloc + _D(v["sd_revenue"]) - _D(v["spread_reimb"])
    p2g   = _D(v["revenue"]) - cof_alloc
    print(f"  avg_value × weight      = {_fmt_usd(avg_v * wt)}")
    print(f"  cof_alloc               = avg×wt / Σ(avg×wt) × cof_total")
    print(f"                          = {_fmt_usd(avg_v * wt)} / {_fmt_usd(total_w)} × {_fmt_usd(cof_total)}")
    print(f"                          = {_fmt_usd(cof_alloc)}")
    print(f"  profit_to_sky           = cof_alloc + sd_revenue − spread_reimb")
    print(f"                          = {_fmt_usd(cof_alloc)} + {_fmt_usd(_D(v['sd_revenue']))} − {_fmt_usd(_D(v['spread_reimb']))}")
    print(f"                          = {_fmt_usd(p2s)}")
    print(f"  revenue (prime keeps)   = {_fmt_usd(_D(v['revenue']))}")
    print(f"  grove_net_payment       = revenue − cof_alloc")
    print(f"                          = {_fmt_usd(_D(v['revenue']))} − {_fmt_usd(cof_alloc)}")
    print(f"                          = {_fmt_usd(p2g)}")

    # Cross-check against sheet values
    drift_p2s = _D(v["profit_to_sky"]) - p2s
    drift_gnp = _D(v["profit_to_grove"]) - p2g
    drift_cof = _D(v["cof_alloc"])      - cof_alloc
    print(f"\n  ── Cross-check (sheet vs recalc) ─────────────────────────────────")
    print(f"  P2S   sheet={_fmt_usd(_D(v['profit_to_sky']))}  recalc={_fmt_usd(p2s)}  drift={_fmt_usd(drift_p2s)}")
    print(f"  GNP   sheet={_fmt_usd(_D(v['profit_to_grove']))}  recalc={_fmt_usd(p2g)}  drift={_fmt_usd(drift_gnp)}")
    print(f"  CoF   sheet={_fmt_usd(_D(v['cof_alloc']))}  recalc={_fmt_usd(cof_alloc)}  drift={_fmt_usd(drift_cof)}")
    print(f"{'═'*72}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prime", default="grove")
    parser.add_argument("--month", default="2026-04")
    parser.add_argument(
        "--debug-venue",
        metavar="VENUE_ID",
        help="Print a detailed math trace for this venue ID (e.g. E8) and exit.",
    )
    args = parser.parse_args()

    rows, totals = build_sheet(args.prime, args.month)

    if args.debug_venue:
        _debug_venue(args.debug_venue, rows, totals)
        return 0

    out_dir = _REPO / "settlements" / args.prime / args.month
    csv_out = out_dir / "grove_sheet.csv"
    md_out  = out_dir / "grove_sheet.md"
    _emit_csv(rows, csv_out)
    _emit_markdown(rows, totals, args.prime, args.month, md_out)

    drift_p2s = totals["sum_p2s"] - totals["sky_revenue"]
    drift_p2g = totals["sum_p2g"] + totals["cof_total"] - totals["prime_agent_revenue"]
    print(f"Wrote {csv_out}")
    print(f"Wrote {md_out}")
    print(f"Reconciliation: Σ P2S ≡ sky_revenue                       → drift ${float(drift_p2s):,.4f}")
    print(f"                Σ P2G + cof_total ≡ prime_agent_revenue   → drift ${float(drift_p2g):,.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
