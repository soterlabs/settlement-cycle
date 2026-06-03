"""Render an MSC-native settlement xlsx from a settlement output.

Four tabs:

  1. Summary           — Prime side / Sky side / Grove-comparable Σ P2G / period info
  2. Venues            — per-venue P&L breakdown with CoF re-attribution
  3. Sky Revenue       — how sky_revenue is built (CoF + SDE) + subsidy params
  4. Sky Direct        — active Sky-Direct entries this period

Inputs:
  settlements/{prime}/{month}/venues.csv
  settlements/{prime}/{month}/provenance.json
  settlements/{prime}/{month}/grove_sheet.csv   (post-processor output: P2S/P2G/CoF alloc)
  config/{prime}.yaml
  config/sky_direct_exposures.yaml
  config/subsidy_reference_rates.yaml (for the ref-rate readout in Sky Revenue tab)

Output:
  settlements/{prime}/{month}/{prime}_settlement_{month_name}_{year}.xlsx
  e.g. grove_settlement_april_2026.xlsx
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

_REPO = Path(__file__).resolve().parent.parent

# Styling.
_BOLD   = Font(bold=True)
_TITLE  = Font(bold=True, size=14)
_MUTED  = Font(color="666666", italic=True)
_HEADER_FILL = PatternFill("solid", fgColor="DDE6F1")
_SUBTLE_FILL = PatternFill("solid", fgColor="F4F7FB")
_USD     = '"$"#,##0.00;"−$"#,##0.00;"$"0.00'
_USD0    = '"$"#,##0;"−$"#,##0;"$"0'
_PCT     = '0.000%'
_THIN    = Side(style="thin", color="C5C9CC")
_BOX     = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _D(x) -> Decimal:
    if x is None or x == "":
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def _set_widths(ws, widths: dict[int, int]) -> None:
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def _header_row(ws, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row, c)
        cell.font = _BOLD
        cell.fill = _HEADER_FILL


# --------------------------------------------------------------------------
# IO
# --------------------------------------------------------------------------

def _read_provenance(cell: Path) -> dict:
    with (cell / "provenance.json").open() as f:
        return json.load(f)


def _read_venues(cell: Path) -> list[dict]:
    with (cell / "venues.csv").open() as f:
        return list(csv.DictReader(f))


def _read_grove_sheet(cell: Path) -> list[dict]:
    p = cell / "grove_sheet.csv"
    if not p.exists():
        return []
    with p.open() as f:
        return list(csv.DictReader(f))


def _read_prime_yaml(prime_id: str) -> dict:
    with (_REPO / "config" / f"{prime_id}.yaml").open() as f:
        return yaml.safe_load(f)


def _read_sde(prime_id: str, period_start: date) -> list[dict]:
    with (_REPO / "config" / "sky_direct_exposures.yaml").open() as f:
        cfg = yaml.safe_load(f)
    out: list[dict] = []
    for section in ("active", "historical"):
        for e in cfg.get(section, []) or []:
            if e.get("prime") != prime_id:
                continue
            if e.get("kind") == "pattern":
                # Pattern entries apply at the prime/PSM3 level — not per-venue.
                # Skipping here keeps the SDE tab focused on venue-level entries.
                continue
            start = e["start_date"]
            end = e.get("end_date")
            if isinstance(start, str): start = date.fromisoformat(start)
            if isinstance(end, str):   end   = date.fromisoformat(end)
            active = start <= period_start and (end is None or end >= period_start)
            out.append({**e, "_start": start, "_end": end, "_active": active})
    return out


# --------------------------------------------------------------------------
# Tab writers
# --------------------------------------------------------------------------

def _write_summary(ws, prov: dict, sheet_rows: list[dict]) -> None:
    ws.title = "Summary"
    prime = prov["prime_id"].upper()
    month = prov["month"]
    res   = prov["results"]
    par   = _D(res["prime_agent_revenue"])
    ar    = _D(res["agent_rate"])
    par_t = _D(res.get("prime_agent_total_revenue", par + ar))
    sky   = _D(res["sky_revenue"])
    sd    = sum((_D(r["sd_revenue"]) for r in sheet_rows), Decimal("0"))
    cof   = sky - sd
    sum_p2g = sum((_D(r["profit_to_grove"]) for r in sheet_rows), Decimal("0"))
    monthly_pnl = par + ar - sky
    # New display-only surfaces from PR #104 — see provenance schema.
    sky_gross    = _D(res.get("sky_revenue_gross") or 0)
    spread_reimb = _D(res.get("susds_spread_reimbursement") or 0)

    ws.append([f"{prime} — Monthly settlement {month}"])
    ws["A1"].font = _TITLE
    ws.append([])

    def _block(title: str, rows: list[tuple[str, Decimal]], total: Decimal) -> None:
        """Three-block layout: header → addends → blank-label total row."""
        ws.append([title, "USD"])
        _header_row(ws, ws.max_row, 2)
        for lbl, val in rows:
            ws.append([lbl, float(val)])
            ws.cell(ws.max_row, 2).number_format = _USD
        # Total row — no label (matches the cleaned reference layout)
        ws.append(["", float(total)])
        cell = ws.cell(ws.max_row, 2)
        cell.number_format = _USD
        cell.font = _BOLD
        cell.border = Border(top=_THIN)

    _block(
        "Prime side",
        rows=[
            ("prime_agent_revenue (gross venue yield to prime)", par),
            ("+ agent_rate (subproxy USDS / sUSDS yield)",       ar),
        ],
        total=par_t,
    )
    ws.append([])

    _block(
        "Sky side",
        rows=[
            ("CoF on utilized (BR × Net_Subs)",          cof),
            ("+ SDE revenue (Sky-Direct, full to Sky)",  sd),
        ],
        total=sky,
    )
    ws.append([])

    # Sky Revenue (max) — display-only ceiling on BR (no idle/SDE deductions).
    # NOT a true ceiling on sky_revenue: actual sky_revenue adds sde_revenue
    # on top of BR-on-utilized, so for primes with material SDE positions
    # sky_revenue can exceed sky_revenue_gross. The subsidised BR (ref_rate
    # ramp) is already applied — the rate matches actual sky_revenue, not
    # the raw Maker base rate.
    if sky_gross > 0:
        _block(
            "Sky Revenue (max) — BR × full ilk debt, no deductions",
            rows=[
                ("CoF on Net_Subs (actual BR × utilized)",         cof),
                ("reduction from idle/SDE deductions",             -(sky_gross - cof)),
            ],
            total=sky_gross,
        )
        ws.append([])

    # sUSDS spread — credited to prime_agent_revenue, NOT deducted from
    # sky_revenue (Sky charges full BR on the underlying utilized; the
    # 30 bps lands in Prime Revenue). Surfaced for Grove-side audit only.
    if spread_reimb != 0:
        ws.append(["sUSDS spread (Curve LP + PSM3) — credited to prime_agent_revenue", float(spread_reimb)])
        ws.cell(ws.max_row, 2).number_format = _USD
        ws.cell(ws.max_row, 1).font = _MUTED
        ws.append([])

    _block(
        'Comparison (Grove-style "Profit to Grove")',
        rows=[
            ("prime_agent_revenue",                    par),
            ("− CoF (deducted per-venue in display)",  -cof),
        ],
        total=sum_p2g,
    )
    ws.append([])

    # Period info
    ws.append(["Period",     f"{prov['period']['start']} → {prov['period']['end']} "
                              f"({prov['period']['n_days']} days)"])
    ws.append(["Pin blocks", ", ".join(f"{k}={v}" for k, v in (prov.get('pin_blocks_eom') or {}).items())])
    ws.append(["Generated",  prov.get("generated_at_utc", "")])
    ws.append(["Pipeline",   prov.get("settle_version", "")])

    _set_widths(ws, {1: 60, 2: 22})


def _write_venues(ws, sheet_rows: list[dict], prime_cfg: dict) -> None:
    ws.title = "Venues"
    # Index venues from prime config to enrich with chain + category.
    by_id = {v.get("id"): v for v in prime_cfg.get("venues", []) or []}

    cols = [
        "Venue", "Label", "Chain", "Pricing cat.",
        "Position SoM", "Position EoM", "Period inflow",
        "actual_revenue", "external_revenue", "revenue (to prime)",
        "sd_revenue (to Sky)",
        "Avg value", "Weight", "CoF alloc", "Profit to Sky", "Profit to Grove",
        "Utilized Deduction (avg)", "Spread Reimb",
        "Notes",
    ]
    ws.append(cols)
    _header_row(ws, 1, len(cols))

    # Sort by absolute Profit to Sky desc so Grove-large positions surface first.
    for r in sorted(sheet_rows, key=lambda x: abs(float(x["profit_to_sky"])), reverse=True):
        vid = r["venue_id"]
        v = by_id.get(vid, {})
        ws.append([
            vid,
            r["label"],
            v.get("chain", ""),
            v.get("pricing_category", ""),
            float(r["value_som"]),
            float(r["value_eom"]),
            float(_D(r["value_eom"]) - _D(r["value_som"])),   # period_inflow proxy
            float(r["actual_rev"]),
            float(r["external"]),
            float(r["revenue"]),
            float(r["sd_revenue"]),
            float(r["avg_value"]),
            float(r["weight"]),
            float(r["cof_alloc"]),
            float(r["profit_to_sky"]),
            float(r["profit_to_grove"]),
            float(_D(r.get("deduction_avg") or 0)),
            float(_D(r.get("spread_reimb")  or 0)),
            r.get("note", ""),
        ])

    # Number formats: USD cols are 5–12, 14–18. Pct col is 13. Last col is text.
    for row in range(2, ws.max_row + 1):
        for c in (5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 18):
            ws.cell(row, c).number_format = _USD0
        ws.cell(row, 13).number_format = _PCT

    _set_widths(ws, {
        1: 7, 2: 50, 3: 11, 4: 13,
        5: 16, 6: 16, 7: 16, 8: 14, 9: 14, 10: 16, 11: 16,
        12: 16, 13: 9, 14: 14, 15: 16, 16: 16,
        17: 22, 18: 14, 19: 30,
    })
    ws.freeze_panes = "C2"


def _write_sky_revenue(ws, prov: dict, sheet_rows: list[dict], prime_cfg: dict) -> None:
    ws.title = "Sky Revenue"
    res = prov["results"]
    sky = _D(res["sky_revenue"])
    sd  = sum((_D(r["sd_revenue"]) for r in sheet_rows), Decimal("0"))
    cof = sky - sd
    sky_gross    = _D(res.get("sky_revenue_gross") or 0)
    spread_reimb = _D(res.get("susds_spread_reimbursement") or 0)

    ws.append(["How Sky's monthly take is built"])
    ws["A1"].font = _TITLE
    ws.append([])

    ws.append(["Component", "USD"])
    _header_row(ws, ws.max_row, 2)
    rows = [
        ("CoF on utilized debt (= Σ_d max(utilized_d,0) × (1+BR_d)^(1/365)−1)", cof),
        ("+ Sky-Direct revenue (full venue yield on fixed/capped SDE)",         sd),
        ("equals sky_revenue (total Sky take this month)",                       sky),
    ]
    for lbl, val in rows:
        ws.append([lbl, float(val)])
        ws.cell(ws.max_row, 2).number_format = _USD

    ws.append([])
    ws.append(["Utilized base = cum_debt − idle USDS − PSM USDS − Σ SDE asset value − Curve idle − lending idle"])
    ws.cell(ws.max_row, 1).font = _MUTED
    ws.append([])

    # Sky Revenue (max) reconciliation — pure BR × cum_debt vs actual CoF.
    # Display-only diagnostic that surfaces how much the idle/SDE/PSM/Curve
    # deductions reduce the BR component of sky_revenue.
    if sky_gross > 0:
        ws.append(["Sky Revenue (max) — BR × full ilk debt (no deductions)", float(sky_gross)])
        ws.cell(ws.max_row, 2).number_format = _USD
        ws.cell(ws.max_row, 1).font = _BOLD
        ws.append(["    ↳ actual CoF on Net_Subs (BR × utilized)",            float(cof)])
        ws.cell(ws.max_row, 2).number_format = _USD
        ws.append(["    ↳ reduction from idle/SDE deductions",                -float(sky_gross - cof)])
        ws.cell(ws.max_row, 2).number_format = _USD
        ws.append([
            "Note: sky_revenue can EXCEED sky_revenue_gross for primes with "
            "active SDE positions — sky_revenue also adds sde_revenue on top "
            "of BR-on-utilized. The subsidised BR (ref_rate ramp) is already "
            "applied here; this is NOT the raw Maker base rate."
        ])
        ws.cell(ws.max_row, 1).font = _MUTED
        ws.cell(ws.max_row, 1).alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[ws.max_row].height = 60
        ws.append([])

    # sUSDS spread — Prime Revenue line, not a Sky deduction.
    if spread_reimb != 0:
        ws.append([
            "sUSDS spread (Curve LP + PSM3) — 30 bps × value × n_days "
            "credited to prime_agent_revenue. Sky still charges full BR on "
            "the underlying utilized; this row is the prime's offsetting "
            "pickup on the share-price-appreciation accounting (SSR + BR + "
            "30 bps nets to zero economically).",
            float(spread_reimb),
        ])
        ws.cell(ws.max_row, 2).number_format = _USD
        ws.cell(ws.max_row, 1).font = _MUTED
        ws.cell(ws.max_row, 1).alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[ws.max_row].height = 60
        ws.append([])

    # Subsidy params if enabled.
    sub_cfg = prime_cfg.get("subsidy") or {}
    if sub_cfg.get("enabled"):
        ws.append(["Subsidy", "Value"])
        _header_row(ws, ws.max_row, 2)
        ws.append(["enabled",        "true"])
        ws.append(["cap_usd",        sub_cfg.get("cap_usd", "")])
        ws.cell(ws.max_row, 2).number_format = _USD0
        ws.append(["program_start",  sub_cfg.get("program_start", "")])
        ws.append(["ramp_months",    sub_cfg.get("ramp_months", "")])
        ws.append(["ref_rate_kind",  sub_cfg.get("ref_rate_kind", "")])

        # T value for this period
        from settle.domain.subsidy import months_elapsed_since
        pstart = sub_cfg.get("program_start")
        if isinstance(pstart, str):
            pstart = date.fromisoformat(pstart)
        period_start = date.fromisoformat(prov["period"]["start"])
        period_end   = date.fromisoformat(prov["period"]["end"])
        t_start = months_elapsed_since(period_start, pstart)
        t_end   = months_elapsed_since(period_end,   pstart)
        ws.append(["T this period",  f"{t_start} (SoM) → {t_end} (EoM)"])
        ws.append([
            "Formula",
            "subsidised_apy = ref_rate + (base_apy − ref_rate) × min(T, 24) / 24, "
            "applied to first cap_usd of utilized; excess at full base_apy",
        ])
        ws.cell(ws.max_row, 1).font = _MUTED

    ws.append([])
    ws.append(["Base rate composition: base_apy = (1 + SSR)(1 + 30bps) − 1 (multiplicative)"])
    ws.cell(ws.max_row, 1).font = _MUTED

    _set_widths(ws, {1: 80, 2: 22})


def _write_sde(ws, sde: list[dict], sheet_rows: list[dict]) -> None:
    ws.title = "Sky Direct"
    ws.append(["Sky-Direct Exposures applied this period"])
    ws["A1"].font = _TITLE
    ws.append([])

    cols = ["Venue", "Kind", "Cap (USD)", "Start", "End", "Active?", "Source", "Label",
            "actual_revenue", "sd_revenue (to Sky)"]
    ws.append(cols)
    _header_row(ws, ws.max_row, len(cols))

    by_id = {r["venue_id"]: r for r in sheet_rows}
    for e in sde:
        vid = e.get("venue_id", "")
        row = by_id.get(vid, {})
        ws.append([
            vid,
            e.get("kind", ""),
            e.get("cap_usd", "") or "",
            str(e["_start"]) if e.get("_start") else "",
            str(e["_end"]) if e.get("_end") else "—",
            "YES" if e["_active"] else "no",
            e.get("source", ""),
            e.get("label", ""),
            float(_D(row.get("actual_rev", 0))) if row else 0,
            float(_D(row.get("sd_revenue", 0))) if row else 0,
        ])
        if isinstance(e.get("cap_usd"), (int, float)):
            ws.cell(ws.max_row, 3).number_format = _USD0
        ws.cell(ws.max_row, 9).number_format  = _USD0
        ws.cell(ws.max_row, 10).number_format = _USD0

    _set_widths(ws, {1: 8, 2: 8, 3: 14, 4: 12, 5: 12, 6: 8, 7: 50, 8: 25, 9: 16, 10: 18})


def _write_off_protocol_holdings(ws, prov: dict, prime_cfg: dict) -> None:
    """Off-protocol holdings — display-only venues. Surfaced for visibility
    but excluded from prime_agent_revenue, sky_revenue, and the NAV cost-
    basis invariant. Realized P&L on any round-trip lands at the anchor
    venue when the cash arrives at the ALM — see
    ``_cat_a_capital_inflow_timeseries`` paired-principal-cap classifier.
    """
    ws.title = "Off-protocol holdings"
    rows = prov.get("display_only_breakdown") or []
    ws.append(["Off-protocol holdings (tracked, not in prime revenue)"])
    ws["A1"].font = _TITLE
    ws.append([])
    ws.append([
        "These positions sit with external counterparties (e.g. OOB OTC "
        "venues, off-protocol custodians). The balances below are visible "
        "for monthly-NAV reporting only — they do NOT contribute to "
        "prime_agent_revenue or sky_revenue. Any realized gain or loss on "
        "the round-trip is booked at the anchor venue when the cash "
        "settles back at the ALM proxy."
    ])
    ws.append([])

    if not rows:
        ws.append(["(no display-only venues active this period)"])
        _set_widths(ws, {1: 90})
        return

    by_id = {v.get("id"): v for v in prime_cfg.get("venues", []) or []}

    cols = [
        "Venue", "Label", "Chain", "Counterparty (paired_source)",
        "Anchor venue (paired_with)",
        "Outstanding SoM (USD)", "Outstanding EoM (USD)", "Δ over period",
        "Status",
    ]
    ws.append(cols)
    _header_row(ws, ws.max_row, len(cols))

    for r in rows:
        vid = r["venue_id"]
        cfg = by_id.get(vid, {})
        chain = cfg.get("chain", "")
        paired_source = (cfg.get("paired_source") or "")
        paired_with = (cfg.get("paired_with") or "")
        som = _D(r["value_som"])
        eom = _D(r["value_eom"])
        delta = eom - som
        if eom == 0 and som > 0:
            status = "fully returned"
        elif eom == som and som > 0:
            status = "outstanding (no change)"
        elif eom < som:
            status = "partial return"
        elif eom > som:
            status = "new principal-out"
        else:
            status = "—"
        ws.append([
            vid, r.get("label", ""), chain, paired_source, paired_with,
            float(som), float(eom), float(delta), status,
        ])
        for col in (6, 7, 8):
            ws.cell(ws.max_row, col).number_format = _USD0

    ws.append([])
    ws.append([
        "Note: realized gain on returns (the round-trip spread) is "
        "recognized as revenue at the anchor venue via the paired-"
        "principal-cap classifier — see provenance.json venue_breakdown "
        "for the anchor's actual_revenue / external_revenue split."
    ])

    _set_widths(ws, {
        1: 8, 2: 50, 3: 12, 4: 46, 5: 20,
        6: 18, 7: 18, 8: 16, 9: 22,
    })


def _write_sde_daily(ws, prov: dict) -> None:
    """Per-day Sky / Grove / In-flight NAV / Total breakdown for capped
    SDE venues with an on-chain burn event (``burn_date`` set).

    Phase rule per day:
      * day < burn_date            → Sky = cum_value (cap), Grove = uncapped − cum_value, in-flight = 0
      * burn_date ≤ day ≤ in_flight_end → Sky = 0, Grove = uncapped (residual), in-flight = cum_value
      * day > in_flight_end        → Sky = 0, Grove = uncapped, in-flight = 0

    where ``in_flight_end = usdc_settlement_date or end_date``. The total
    column = sum of the three (= the prime+Sky combined NAV attributable
    to this venue on that day).
    """
    breakdown = prov.get("sde_daily_breakdown") or []
    venues_with_burn = [b for b in breakdown if b.get("burn_date")]
    if not venues_with_burn:
        # No burn events this period — skip the tab entirely.
        return
    ws.title = "SDE daily"
    ws.append(["SDE per-day breakdown (capped venues with on-chain burn)"])
    ws["A1"].font = _TITLE
    ws.append([])
    ws.append([
        "For each capped SDE venue with an on-chain burn, the daily on-"
        "chain position is decomposed into Sky's slice (cap-protected, "
        "pre-burn), Grove's residual (above the cap, surviving the burn), "
        "and the in-flight USDC settling back to the ALM (post-burn / "
        "pre-settlement). The three columns sum to the total NAV "
        "attributable to this venue on that day."
    ])
    ws.append([])

    for b in venues_with_burn:
        cap_str = b.get("cap_usd") or ""
        burn = date.fromisoformat(b["burn_date"])
        in_flight_end_raw = b.get("usdc_settlement_date") or b.get("end_date")
        in_flight_end = (
            date.fromisoformat(in_flight_end_raw) if in_flight_end_raw else None
        )
        end_date_str = b.get("end_date") or "—"
        settle_str = b.get("usdc_settlement_date") or "—"

        ws.append([f"{b['venue_id']} — {b.get('label', '')}"])
        ws.cell(ws.max_row, 1).font = _BOLD
        ws.append([
            f"Cap: ${float(_D(cap_str)):,.0f}" if cap_str else "Cap: —",
            f"burn_date: {b['burn_date']}",
            f"usdc_settlement_date: {settle_str}",
            f"end_date: {end_date_str}",
        ])
        ws.append([])
        ws.append(["Date", "Sky position", "Grove position", "In-flight NAV", "Total"])
        _header_row(ws, ws.max_row, 5)

        for r in b.get("daily", []):
            d = date.fromisoformat(r["block_date"])
            cum = _D(r["cum_value"])
            unc = _D(r["uncapped_value"])
            if d < burn:
                # Pre-burn: Sky and Grove both visible on-chain. Sky's
                # share is the cap-protected slice; Grove's is the excess.
                sky = cum
                grove = max(Decimal("0"), unc - cum)
                inflight = Decimal("0")
            elif in_flight_end is not None and d <= in_flight_end and cum > 0:
                # In-flight: Sky's shares are destroyed (so uncapped only
                # reflects Grove's residual). Cap-preserved cum_value is
                # the in-flight USDC pending settlement.
                sky = Decimal("0")
                grove = unc
                inflight = cum
            else:
                # Post-settlement / post-end: Sky's slice is recognised
                # at $0 (the USDC has landed at the ALM and joined idle
                # alm_usds for separate accounting). Grove's residual
                # remains on-chain.
                sky = Decimal("0")
                grove = unc
                inflight = Decimal("0")
            total = sky + grove + inflight
            ws.append([
                d.isoformat(), float(sky), float(grove), float(inflight), float(total),
            ])
            for c in (2, 3, 4, 5):
                ws.cell(ws.max_row, c).number_format = _USD0

        ws.append([])  # blank row between venues

    _set_widths(ws, {1: 12, 2: 18, 3: 18, 4: 18, 5: 18})


# --------------------------------------------------------------------------
# Entrypoint
# --------------------------------------------------------------------------

_MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


def _output_filename(prime_id: str, month: str) -> str:
    """Return ``{prime}_settlement_{month_name}_{year}.xlsx``."""
    year, m = month.split("-")
    return f"{prime_id}_settlement_{_MONTH_NAMES[int(m) - 1]}_{year}.xlsx"


def build_xlsx(prime_id: str, month: str) -> Path:
    cell_dir = _REPO / "settlements" / prime_id / month
    prov     = _read_provenance(cell_dir)
    sheet    = _read_grove_sheet(cell_dir)
    cfg      = _read_prime_yaml(prime_id)
    sde      = _read_sde(prime_id, date.fromisoformat(prov["period"]["start"]))

    wb = Workbook()
    _write_summary(wb.active, prov, sheet)
    _write_venues(wb.create_sheet(), sheet, cfg)
    _write_sky_revenue(wb.create_sheet(), prov, sheet, cfg)
    _write_sde(wb.create_sheet(), sde, sheet)
    if prov.get("display_only_breakdown"):
        # Only emit the off-protocol-holdings tab when the prime has display-
        # only venues this period. Avoids an empty tab cluttering monthly
        # reports for primes with no off-protocol positions.
        _write_off_protocol_holdings(wb.create_sheet(), prov, cfg)
    if any(b.get("burn_date") for b in (prov.get("sde_daily_breakdown") or [])):
        # Only emit the "SDE daily" tab when at least one SDE venue had a
        # burn this period — otherwise the three-column decomposition is
        # uninteresting (Sky = full position, Grove = 0 throughout).
        _write_sde_daily(wb.create_sheet(), prov)

    out = cell_dir / _output_filename(prime_id, month)
    wb.save(out)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prime", default="grove")
    parser.add_argument("--month", default="2026-04")
    args = parser.parse_args()
    import sys
    sys.path.insert(0, str(_REPO / "src"))
    out = build_xlsx(args.prime, args.month)
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
