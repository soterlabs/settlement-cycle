"""Render a Grove-spreadsheet-shaped breakdown from a settlement output.

The canonical settlement output (``settlements/{prime}/{month}/venues.csv``)
reports per-venue ``revenue`` (= what flows to the prime) and a single
aggregate ``sky_revenue`` in ``pnl.md``. Grove's PnL workbook splits the
CoF charge across venues by ``avg_value × Grove_weight`` so the reader can
see, per venue, "what did Sky take" and "what did Grove keep".

This script derives that view post-hoc, read-only. No methodology changes —
totals are exact by construction:

    Σ_v Profit_to_Sky_v   ≡ sky_revenue
    Σ_v Profit_to_Grove_v ≡ prime_agent_revenue

Inputs:
    settlements/{prime}/{month}/venues.csv      — per-venue rows
    settlements/{prime}/{month}/provenance.json — headline totals + period
    config/sky_direct_exposures.yaml            — fixed/capped SDE entries

Outputs (alongside the inputs):
    grove_sheet.csv  — machine-readable Grove-style breakdown
    grove_sheet.md   — human-readable side-by-side table

Per-venue math:

    sd_share_v        = 1.0 for fixed SDE; 0.0 otherwise (capped SDE not active
                        for Grove in 2026-04 — the only historical capped
                        entry was JAAA E8 ending 2026-03-12).
    weight_v          = 1 − sd_share_v
    avg_value_v       = (value_som + value_eom) / 2     # SoM/EoM avg
    sd_revenue_v      = actual_revenue for fixed SDE; 0 otherwise
    CoF_total         = sky_revenue − Σ_v sd_revenue_v   # BR on Net_Subs
    cof_alloc_v       = avg_value_v × weight_v / Σ_v(avg × weight) × CoF_total
    profit_to_sky_v   = cof_alloc_v + sd_revenue_v
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
* Capped SDE windows (e.g. JAAA E8 ≤ 2026-03-12) are not handled here —
  for those months the daily ``sd_share_d = min(cap, v_d)/v_d`` matters and
  the right number to plug in is ``actual_revenue × sd_share_avg`` which the
  upstream compute layer already wrote to ``provenance.json``. Falls back
  to using ``actual_revenue − revenue`` as ``sd_revenue_v`` when the
  SDE entry kind is capped, which is exact post-hoc.
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
            }
    return out


def _read_venues(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


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
    if inferred_sd_revenue < 0:
        # Numerical noise on a non-SDE venue. Clamp to 0.
        inferred_sd_revenue = Decimal("0")

    entry = sde.get(venue_id)
    if entry is None:
        # No SDE record in YAML for this venue × period.
        if inferred_sd_revenue > Decimal("0.01"):
            # Compute layer claims some SDE revenue but the YAML doesn't —
            # flag in the output rather than silently hide.
            return inferred_sd_revenue / actual if actual > 0 else Decimal("0"), \
                   inferred_sd_revenue, "(SDE inferred from numbers; not in YAML)"
        return Decimal("0"), Decimal("0"), ""

    kind = entry["kind"]
    if kind == "fixed":
        # 100 % of the venue is SDE. sd_revenue = whole actual_revenue.
        return Decimal("1"), actual, "SDE (fixed)"
    if kind == "capped":
        # Use compute layer's already-daily-resolved number; derive effective
        # sd_share post-hoc for display.
        share = inferred_sd_revenue / actual if actual > 0 else Decimal("0")
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
    headline_sky    = _D(prov["results"]["sky_revenue"])
    headline_prime  = _D(prov["results"]["prime_agent_revenue"])
    headline_agent  = _D(prov["results"]["agent_rate"])
    # 30 bps Prime Revenue components computed outside the venue loop
    # (PRD §17.11). Missing on settlements written before
    # ``curve_susds_spread`` / ``psm3_susds_spread`` were surfaced in
    # provenance.json — fall back to 0 for those legacy files.
    curve_spread = _D(prov["results"].get("curve_susds_spread") or 0)
    psm3_spread  = _D(prov["results"].get("psm3_susds_spread")  or 0)
    aggregate_susds_spread = curve_spread + psm3_spread

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
        enriched.append({
            "venue_id":   r["venue_id"],
            "label":      r["label"],
            "value_som":  _D(r["value_som"]),
            "value_eom":  _D(r["value_eom"]),
            "avg_value":  avg_value,
            "sd_share":   sd_share,
            "weight":     weight,
            "actual_rev": _D(r["actual_revenue"]),
            "external":   _D(r.get("external_revenue") or 0),
            "revenue":    _D(r["revenue"]),       # already net of SDE
            "sd_revenue": sd_revenue,
            "note":       note,
        })

    # Synthetic row: 30 bps Prime Revenue components computed outside the
    # venue loop (Curve LP sUSDS + PSM3 sUSDS leg). Required so that
    # Σ vr.revenue ≡ prime_agent_revenue — without it the reconciliation
    # footer drifts by the spread amount for any prime holding sUSDS in
    # Curve LP pools or PSM3 (Spark today; future primes likewise).
    # Weight=0 keeps it out of the CoF allocation pool. Note column flags
    # the aggregate so the row's prime-only attribution is explicit.
    if aggregate_susds_spread != 0:
        enriched.append({
            "venue_id":   "SPREAD",
            "label":      "30bps sUSDS spread (Curve LP + PSM3 aggregate)",
            "value_som":  Decimal("0"),
            "value_eom":  Decimal("0"),
            "avg_value":  Decimal("0"),
            "sd_share":   Decimal("0"),
            "weight":     Decimal("0"),
            "actual_rev": aggregate_susds_spread,
            "external":   Decimal("0"),
            "revenue":    aggregate_susds_spread,
            "sd_revenue": Decimal("0"),
            "note":       "prime-only (no CoF; computed outside venue loop)",
        })

    # CoF on Net_Subs = sky_revenue minus the SDE-revenue portion that flows
    # to Sky directly. Allocated proportionally to (avg_value × weight).
    total_sd_revenue = sum((v["sd_revenue"] for v in enriched), Decimal("0"))
    cof_total = headline_sky - total_sd_revenue

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
        v["profit_to_sky"]   = v["cof_alloc"] + v["sd_revenue"]
        v["profit_to_grove"] = v["revenue"] - v["cof_alloc"]

    totals = {
        "sky_revenue":            headline_sky,
        "prime_agent_revenue":    headline_prime,
        "agent_rate":             headline_agent,
        "cof_total":              cof_total,
        "sd_revenue_total":       total_sd_revenue,
        "sum_p2s":                sum((v["profit_to_sky"]   for v in enriched), Decimal("0")),
        "sum_p2g":                sum((v["profit_to_grove"] for v in enriched), Decimal("0")),
    }
    return enriched, totals


def _emit_csv(rows: list[dict], out: Path) -> None:
    cols = [
        "venue_id", "label", "value_som", "value_eom", "avg_value",
        "sd_share", "weight", "actual_rev", "external", "revenue",
        "sd_revenue", "cof_alloc", "profit_to_sky", "profit_to_grove", "note",
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
    lines.append(f"| Σ Profit to Sky ≡ `sky_revenue` | {_fmt_usd(totals['sky_revenue'])} |")
    lines.append(f"| &nbsp;&nbsp;↳ CoF on Net_Subs (BR × utilized) | {_fmt_usd(totals['cof_total'])} |")
    lines.append(f"| &nbsp;&nbsp;↳ SDE revenue (full flow to Sky) | {_fmt_usd(totals['sd_revenue_total'])} |")
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
        "| Venue | Label | avg_value | weight | Profit to Sky | Revenue | Grove Net Payment | CoF alloc | SDE rev | Note |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---|")
    # Sort by Profit to Sky desc to make the sheet read like Grove's.
    for r in sorted(rows, key=lambda v: float(v["profit_to_sky"]), reverse=True):
        lines.append(
            f"| {r['venue_id']} | {r['label']} | {_fmt_usd(r['avg_value'])} | "
            f"{_fmt_pct(r['weight'])} | {_fmt_usd(r['profit_to_sky'])} | "
            f"{_fmt_usd(r['revenue'])} | {_fmt_usd(r['profit_to_grove'])} | "
            f"{_fmt_usd(r['cof_alloc'])} | "
            f"{_fmt_usd(r['sd_revenue'])} | {r['note']} |"
        )
    lines.append("")
    lines.append("## Formulas\n")
    lines.append(
        "```\n"
        "sd_share_v        = 1.0 for fixed SDE; daily-derived for capped (post-hoc)\n"
        "weight_v          = 1 − sd_share_v\n"
        "avg_value_v       = venues.csv:tw_avg_value_usd        # time-weighted (preferred)\n"
        "                    or  (value_som + value_eom) / 2    # legacy fallback (CoF approx)\n"
        "sd_revenue_v      = actual_revenue + external_revenue − revenue\n"
        "cof_total         = sky_revenue − Σ_v sd_revenue_v\n"
        "cof_alloc_v       = avg_value_v × weight_v / Σ_v(avg × weight) × cof_total\n"
        "profit_to_sky_v      = cof_alloc_v + sd_revenue_v\n"
        "grove_net_payment_v  = revenue_v − cof_alloc_v\n"
        "```\n"
    )
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prime", default="grove")
    parser.add_argument("--month", default="2026-04")
    args = parser.parse_args()

    rows, totals = build_sheet(args.prime, args.month)
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
