"""Generate ``preparation/doc.md`` from its data sources.

The MSC#9 preparation doc is a set of per-prime revenue tables, each
backed by a public data source. This script re-derives every number and
re-renders the whole file, so when a source changes (a settlement report
is re-published, the DR workbook is re-exported, a Dune query is
refreshed) the doc is updated by re-running one command:

    set -a; source .env; set +a       # provides DUNE_API_KEY
    python3 preparation/scripts/generate_doc.py

Data sources (all public):

1. **Settlement reports** — ``summary.md`` per (prime, month) in
   https://github.com/soterlabs/settlement-reports → supply-side rows
   (“prime agent net revenue” + “prime side sky direct exposure”),
   “sky revenue”, and “agent rate”.
2. **Distribution Rewards** — via ``dr_aggregate.py`` (same folder):
   ``dr_comparison_2026.xlsx`` (GitHub, sheet “Soter Data”) × the
   referral-code → prime mapping from the published payouts sheet.
3. **Chronicle points** — latest result of Dune query 7696411
   (needs ``DUNE_API_KEY``; the results endpoint costs no credits).
4. **Pioneer rewards** — latest result of Dune query 7696529 (same).
5. **Forum finals** — the per-month numbers published in the MSC#5–8
   forum posts, transcribed in ``FORUM_SKY`` / ``FORUM_DEMAND`` below.
   These are immutable history: edit the constants only if a forum
   post is amended.

Everything else (notes, warnings, pending items, the Notion header) is
static template text in this file — edit it here, then re-run.

Caching: remote fetches go through ``dr_aggregate._fetch`` (24h TTL in
/tmp, announced on stderr). Delete the /tmp files to force re-fetch.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dr_aggregate  # noqa: E402 — sibling module
from dr_aggregate import MONTHS, _fetch  # noqa: E402

_PREP = Path(__file__).resolve().parent.parent
OUT = _PREP / "doc.md"

# ───────────────────────── shared links ─────────────────────────
MSC_REPO = "https://github.com/soterlabs/settlement-cycle"
DR_REPO = "https://github.com/stablewatch-io/settle-dr-dune"
DR_XLSX = f"{DR_REPO}/blob/main/dune-results/dr_comparison_2026.xlsx"
REPORTS_RAW = "https://raw.githubusercontent.com/soterlabs/settlement-reports/main/reports"
REPORTS_WEB = "https://github.com/soterlabs/settlement-reports/blob/main/reports"
SHEET = ("https://docs.google.com/spreadsheets/d/e/"
         "2PACX-1vR-dLvndU-DM1j_8gxYIhfYOtoIgyEJ9Jg5R0RcV-ZRVGdOJdmwIysO4P9yfacw-CkBGJjXPgwbC6WB/pubhtml")
SHEET_CSV = ("https://docs.google.com/spreadsheets/d/e/"
             "2PACX-1vR-dLvndU-DM1j_8gxYIhfYOtoIgyEJ9Jg5R0RcV-ZRVGdOJdmwIysO4P9yfacw-CkBGJjXPgwbC6WB/"
             "pub?gid=753291318&single=true&output=csv")
ATLAS_GAR = "https://sky-atlas.io/#b3f97303-4d41-497c-b931-9337c518bd7e"
CHR_REPO = "https://github.com/soterlabs/chronicle-points-dune-dash"
CHR_DASH = "https://dune.com/openmsc/chronicle-points-monthly-summary"
CHR_QUERY_ID = 7696411
CHR_QUERY = "https://dune.com/queries/7696411/11654800"
PIO_REPO = "https://github.com/soterlabs/pioneer-rewards-dune-dash"
PIO_DASH = "https://dune.com/openmsc/keel-pioneer-rewards"
PIO_QUERY_ID = 7696529
PIO_QUERY = "https://dune.com/queries/7696529"

FORUM = {
    "MSC#5": "https://forum.skyeco.com/t/msc-5-settlement-summary-january-2026-spark-and-grove/27709",
    "MSC#6": "https://forum.skyeco.com/t/msc-6-settlement-summary-february-2026/27778",
    "MSC#7": "https://forum.skyeco.com/t/msc-7-settlement-summary-march-2026/27844",
    "MSC#8": "https://forum.skyeco.com/t/msc-8-settlement-summary-april-2026/27888",
}
MONTH_LABEL = {"2026-01": "Jan 2026", "2026-02": "Feb 2026", "2026-03": "Mar 2026",
               "2026-04": "Apr 2026", "2026-05": "May 2026"}
MONTH_SHORT = {"2026-01": "Jan", "2026-02": "Feb", "2026-03": "Mar",
               "2026-04": "Apr", "2026-05": "May"}
MONTH_POST = {"2026-01": "MSC#5", "2026-02": "MSC#6", "2026-03": "MSC#7",
              "2026-04": "MSC#8", "2026-05": None}

# ─────────────────── forum finals (immutable history) ───────────────────
# Sky-share (supply-side) finals per the linked posts; None = not published.
FORUM_SKY = {
    "spark": {"2026-01": 8079210, "2026-02": 7746811, "2026-03": 7662339, "2026-04": 9179021},
    "grove": {"2026-01": 6205320, "2026-02": 6346829, "2026-03": 6290684, "2026-04": 9385986},
    "obex":  {"2026-01": 2095775, "2026-02": 1948422, "2026-03": 2075648, "2026-04": 1969499},
}
# Lumped “Demand Side Total” finals per prime; None = prime absent from post.
FORUM_DEMAND = {
    "spark":   {"2026-01": 1387824, "2026-02": 1265132, "2026-03": 1725726, "2026-04": 1512762},
    "grove":   {"2026-01": 6090, "2026-02": 5630, "2026-03": 138412, "2026-04": 241690},
    "obex":    {"2026-01": 71342, "2026-02": 65719, "2026-03": 69793, "2026-04": 64862},
    "skybase": {"2026-02": 203134, "2026-03": 225299, "2026-04": 201469},
    "keel":    {"2026-03": 30241, "2026-04": 52915},
}

# ───────────────────────── helpers ─────────────────────────


def fmt(x: Decimal | int | None) -> str:
    return "N/A" if x is None else f"{x:,.0f}"


def report_link(prime: str, ym: str) -> str:
    return f"[{MONTH_SHORT[ym]} report]({REPORTS_WEB}/{prime}/{ym}/summary.md)"


def forum_link(ym: str) -> str:
    post = MONTH_POST[ym]
    return f"[{post}]({FORUM[post]})" if post else "N/A"


def fetch_summary(prime: str, ym: str) -> dict[str, Decimal]:
    """Parse the named headline rows out of a settlement report."""
    raw = _fetch(f"{REPORTS_RAW}/{prime}/{ym}/summary.md",
                 f"gen_doc_{prime}_{ym}_summary.md").decode("utf-8")
    out: dict[str, Decimal] = {}
    for key in ("agent rate", "prime agent net revenue",
                "prime side sky direct exposure", "sky revenue"):
        m = re.search(rf"\| \**{re.escape(key)}\** \| \**(-?[\d,]+\.?\d*)\**", raw)
        if not m:
            raise ValueError(f"row {key!r} not found in {prime}/{ym}/summary.md")
        out[key] = Decimal(m.group(1).replace(",", ""))
    return out


def dune_latest(query_id: int) -> list[dict]:
    """Latest stored result rows of a Dune query (no execution, no credits)."""
    key = os.environ.get("DUNE_API_KEY")
    if not key:
        raise SystemExit("DUNE_API_KEY is required (set -a; source .env; set +a)")
    req = urllib.request.Request(
        f"https://api.dune.com/api/v1/query/{query_id}/results?limit=50",
        headers={"X-Dune-API-Key": key},
    )
    with urllib.request.urlopen(req) as r:
        payload = json.load(r)
    return payload["result"]["rows"]


def table(header: list[str], rows: list[list[str]]) -> str:
    ind = "    "
    lines = [ind + "| " + " | ".join(f"**{h}**" for h in header) + " |",
             ind + "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        lines.append(ind + "| " + " | ".join(row) + " |")
    return "\n".join(lines)


def totals_cells(vals: dict[str, Decimal]) -> tuple[str, str]:
    """(Jan-to-Apr, Jan-to-May) totals from unrounded values."""
    return (fmt(sum(vals[m] for m in MONTHS[:4])),
            fmt(sum(vals[m] for m in MONTHS)))


# ─────────────────── section builders ───────────────────


def msc_table(prime: str, vals: dict[str, Decimal],
              forum_vals: dict[str, int] | None) -> str:
    rows = []
    for ym in MONTHS:
        f = fmt(forum_vals.get(ym)) if forum_vals else "N/A"
        rows.append([MONTH_LABEL[ym], fmt(vals[ym]), report_link(prime, ym),
                     f, forum_link(ym)])
    t4, t5 = totals_cells(vals)
    f4 = (f"**{fmt(sum(forum_vals[m] for m in MONTHS[:4]))}**"
          if forum_vals and all(m in forum_vals for m in MONTHS[:4]) else "N/A")
    rows.append(["**Total (Jan to Apr)**", f"**{t4}**", "N/A", f4, "N/A"])
    rows.append(["**Total (Jan to May)**", f"**{t5}**", "N/A", "N/A", "N/A"])
    return table(["Month", "OpenMSC rev", "OpenMSC report", "Forum rev", "Forum post"], rows)


def dr_links() -> str:
    return (f"Results: [dr_comparison_2026.xlsx]({DR_XLSX}) · "
            f"Past payouts: [DR monthly payouts spreadsheet]({SHEET})")


def dr_table(dr: dict[str, Decimal], payouts: dict[str, Decimal]) -> str:
    rows = []
    for ym in MONTHS:
        po = fmt(payouts[ym]) if ym in payouts else "N/A"
        ref = f"[link]({SHEET})" if MONTH_POST[ym] else "N/A"
        rows.append([MONTH_LABEL[ym], fmt(dr[ym]), f"[link]({DR_XLSX})", po, ref])
    t4, t5 = totals_cells(dr)
    po_known = [m for m in MONTHS[:4] if m in payouts]
    po_cell = (f"**{fmt(sum(payouts[m] for m in po_known))} "
               f"({MONTH_SHORT[po_known[0]]} to {MONTH_SHORT[po_known[-1]]})**"
               if po_known else "N/A")
    rows.append(["**Total (Jan to Apr)**", f"**{t4}**", "N/A", po_cell, "N/A"])
    rows.append(["**Total (Jan to May)**", f"**{t5}**", "N/A", "N/A", "N/A"])
    return table(["Month", "OpenMSC rev", "OpenMSC ref", "Past payout", "Reference"], rows)


def recon_table(dr: dict[str, Decimal], ar: dict[str, Decimal],
                forum_demand: dict[str, int],
                extra: tuple[str, dict[str, Decimal | None], dict[str, str]] | None = None) -> str:
    """Reconciliation table. ``extra`` = (column name, values, cell-suffix labels)."""
    header = ["Month", "OpenMSC DR", "OpenMSC Agent Rate"]
    if extra:
        header.append(f"OpenMSC {extra[0]}")
    header += ["OpenMSC total", "Forum demand-side", "Forum post"]
    rows = []
    for ym in MONTHS:
        tot = dr[ym] + ar[ym]
        cells = [MONTH_LABEL[ym], fmt(dr[ym]), fmt(ar[ym])]
        if extra:
            v = extra[1].get(ym)
            label = extra[2].get(ym, "")
            cells.append((fmt(v) + (f" {label}" if label else "")) if v is not None
                         else (label or "N/A"))
            tot += v or 0
        fd = forum_demand.get(ym)
        fl = forum_link(ym) if fd is not None else "N/A"
        cells += [fmt(tot), fmt(fd), fl]
        rows.append(cells)

    def tot_row(months: list[str], name: str, with_forum: bool) -> list[str]:
        cells = [f"**{name}**", f"**{fmt(sum(dr[m] for m in months))}**",
                 f"**{fmt(sum(ar[m] for m in months))}**"]
        run = sum(dr[m] + ar[m] for m in months)
        if extra:
            ev = sum(extra[1][m] or 0 for m in months if extra[1].get(m) is not None)
            cells.append(f"**{fmt(ev)}**")
            run += ev
        cells.append(f"**{fmt(run)}**")
        if with_forum:
            known = [m for m in months if forum_demand.get(m) is not None]
            if known and len(known) < len([m for m in months]):
                cells.append(f"**{fmt(sum(forum_demand[m] for m in known))}** "
                             f"({MONTH_SHORT[known[0]]} to {MONTH_SHORT[known[-1]]})")
            elif known:
                cells.append(f"**{fmt(sum(forum_demand[m] for m in known))}**")
            else:
                cells.append("N/A")
        else:
            cells.append("N/A")
        cells.append("N/A")
        return cells

    rows.append(tot_row(MONTHS[:4], "Total (Jan to Apr)", True))
    rows.append(tot_row(MONTHS, "Total (Jan to May)", False))
    return table(header, rows)


# ─────────────────── data assembly ───────────────────


def collect() -> dict:
    data: dict = {"summaries": {}, "dr": {}, "payouts": {}}
    for prime in ("spark", "grove", "obex", "skybase", "keel"):
        data["summaries"][prime] = {ym: fetch_summary(prime, ym) for ym in MONTHS}

    mapping = dr_aggregate.load_code_to_prime()
    results = dr_aggregate.load_results("Soter Data")
    for prime in ("spark", "grove", "skybase", "keel"):
        totals = {m: Decimal("0") for m in MONTHS}
        codes: set[str] = set()
        for label, by_month in results:
            code = dr_aggregate._code_of(label)
            if mapping.get(code, "").lower() != prime:
                continue
            codes.add(code)
            for m, v in by_month.items():
                totals[m] += v
        # Keel's code 4001 has no results rows but IS the prime's code.
        if prime == "keel":
            codes |= {c for c, p in mapping.items() if p.lower() == "keel"}
        data["dr"][prime] = totals
        data["payouts"][prime] = dr_aggregate.load_past_payouts(prime, mapping)
        data.setdefault("codes", {})[prime] = sorted(codes, key=lambda c: (len(c), c))
    data["dr"]["obex"] = {m: Decimal("0") for m in MONTHS}

    # Chronicle points (Dune): rows keyed by the bucket label.
    chr_rows = {r["period"]: r for r in dune_latest(CHR_QUERY_ID)}
    data["chronicle"] = {
        "2026-01": Decimal(str(chr_rows["Jan 2026 (cumulative since 24 Jul 2025)"]["chronicle_points"])),
        "2026-02": Decimal(str(chr_rows["Feb 2026"]["chronicle_points"])),
        "2026-03": Decimal(str(chr_rows["Mar 2026"]["chronicle_points"])),
        "2026-04": Decimal(str(chr_rows["Apr 2026"]["chronicle_points"])),
        "2026-05": Decimal(str(chr_rows["May 2026"]["chronicle_points"])),
    }

    # Pioneer rewards (Dune): Apr is cumulative since 30 Mar; Jan-Mar None.
    pio_rows = {r["period"]: r for r in dune_latest(PIO_QUERY_ID)}
    data["pioneer"] = {
        "2026-01": None, "2026-02": None, "2026-03": None,
        "2026-04": Decimal(str(pio_rows["Apr 2026 (cumulative since 30 Mar 2026)"]["pioneer_rewards"])),
        "2026-05": Decimal(str(pio_rows["May 2026"]["pioneer_rewards"])),
    }
    return data


def supply_side(prime: str, data: dict) -> dict[str, Decimal]:
    return {ym: data["summaries"][prime][ym]["prime agent net revenue"]
            + data["summaries"][prime][ym]["prime side sky direct exposure"]
            for ym in MONTHS}


def row_of(prime: str, data: dict, key: str) -> dict[str, Decimal]:
    return {ym: data["summaries"][prime][ym][key] for ym in MONTHS}


# ─────────────────── document template ───────────────────

SUPPLY_NOTE = ("Note: To calculate OpenMSC rev, we sum rows “prime agent net revenue” and "
               "“prime side sky direct exposure” from the files summary.md. A negative value means "
               "the prime’s venues underperformed (MtM losses exceeding yield) that month.")
SKY_NOTE = ("Note: To calculate OpenMSC rev, we take the row “sky revenue” (= “prime cost of "
            "funds” + “sky side sky direct exposure”) from the files summary.md.")
AR_NOTE_STD = ("Note: OpenMSC rev is the row “agent rate” from the files summary.md. We can’t "
               "compare past Agent Rate because Amatsu used to sum the two demand-side primitives: "
               "Distribution Rewards and Agent Rate.")
AR_NOTE_SKYBASE = ("Note: OpenMSC rev is the row “agent rate” from the files summary.md. We can’t "
                   "compare past Agent Rate because the forum demand-side totals sum the two "
                   "demand-side primitives: Distribution Rewards and Agent Rate (e.g. Feb 2026 "
                   "Skybase demand-side total: 203,134).")
AR_NOTE_KEEL = ("Note: OpenMSC rev is the row “agent rate” from the files summary.md. We can’t "
                "compare past Agent Rate because the forum demand-side totals sum the two "
                "demand-side primitives: Distribution Rewards and Agent Rate (e.g. Mar 2026 Keel "
                "demand-side total: 30,241). The Keel subproxy was funded with $10M USDS around "
                "Mar 29, 2026 — hence the near-zero agent rate before April.")
GAR_SHEET = "https://docs.google.com/spreadsheets/d/15KPrgUtaiUMifLVzAVxWr2wQ1Q_4bTOqAM34fOsX0P8/edit?gid=0#gid=0"


def build(data: dict) -> str:
    p = []

    p.append(f"""# Preparation for MSC#9

Category: Monthly Settlement Cycle (MSC)
Related Projects: Sky Eco: Monthly Settlement Cycle (https://app.notion.com/p/Sky-Eco-Monthly-Settlement-Cycle-321d79b5de3880bcbfd3fa1453a569bd?pvs=21), Spark: OEA Ops (https://app.notion.com/p/Spark-OEA-Ops-31ed79b5de3880ac9505d2826a27c98f?pvs=21)
Type: Working Doc
Org: Soter Labs (https://app.notion.com/p/Soter-Labs-2b6d79b5de388049b103fb522aa8e136?pvs=21)
Last edited time: June 10, 2026 11:59 PM
Created by: lakonema2000
Created: April 23, 2026 1:41 PM
Group: Content

Note: OpenMSC = @Brett Cocoa + @lakonema2000

Note: all totals are computed from unrounded underlying values — summing the displayed rounded cells can differ by ±1.

Note: this file is GENERATED by preparation/scripts/generate_doc.py — edit the template/constants there, then re-run (see preparation/README.md).

# Content of MSC#9 per Prime Agent
""")

    def std_prime(prime: str, label: str, warning: str | None, ar_note: str) -> None:
        """Spark/Grove/Obex shape (full supply side + agent rate + DR)."""
        p.append(f"## {label}\n")
        if warning:
            p.append(warning + "\n")
        p.append(f"""### Supply-side revenue

#### {label} revenue

- OpenMSC calculated it using its [MSC repo]({MSC_REPO})
    - {SUPPLY_NOTE}

{msc_table(prime, supply_side(prime, data), None)}

- BA Labs calculated it as well

#### Sky revenue

- OpenMSC calculated it using its [MSC repo]({MSC_REPO})
    - {SKY_NOTE}

{msc_table(prime, row_of(prime, data, "sky revenue"), FORUM_SKY[prime])}

- BA Labs calculated it as well

### Demand-side revenue

#### Agent rate (SSR + 20bps on SubProxy)

- OpenMSC calculated it using its [MSC repo]({MSC_REPO})
    - {ar_note}

{msc_table(prime, row_of(prime, data, "agent rate"), None)}

- BA Labs calculated it as well

#### Distribution rewards
""")
        if prime == "obex":
            p.append(f"""- No active demand-side primitive instances for Obex (no referral codes in the [DR monthly payouts spreadsheet]({SHEET}); confirmed in [MSC#5]({FORUM["MSC#5"]}) through [MSC#8]({FORUM["MSC#8"]}) forum posts).
""")
        else:
            p.append(f"""- OpenMSC calculated it using its [DR repo]({DR_REPO})
    - {dr_links()}

{dr_table(data["dr"][prime], data["payouts"][prime])}

- BA Labs calculated it as well
""")

    # ── Spark ──
    std_prime("spark", "Spark", None, AR_NOTE_STD)
    p.append(f"""#### [Historical Reconciliation] Demand-side primitives

- Note: OpenMSC total = Agent rate + Distribution rewards. The forum publishes one lumped “Demand Side Total” (DR + SSR on treasury). Only Jan 2026 had an explicit split: 1,387,824 = 1,284,583 DR + 103,241 treasury SSR.

{recon_table(data["dr"]["spark"], row_of("spark", data, "agent rate"), FORUM_DEMAND["spark"])}
""")

    # ── Grove ──
    std_prime(
        "grove", "Grove",
        "⚠️ **Important note: numbers provided by OpenMSC have not applied TGE penalty yet**\n",
        AR_NOTE_STD,
    )
    chr_vals = data["chronicle"]
    chr_rows = []
    for ym, label in (("2026-01", "Jan 2026 (cumulative since 24 Jul 2025)"),
                      ("2026-02", "Feb 2026"), ("2026-03", "Mar 2026"),
                      ("2026-04", "Apr 2026"), ("2026-05", "May 2026")):
        chr_rows.append([label, fmt(chr_vals[ym]), f"[query]({CHR_QUERY})", "N/A", forum_link(ym)])
    chr_t4, chr_t5 = totals_cells(chr_vals)
    chr_rows.append(["**Total (Jan to Apr)**", f"**{chr_t4}**", "N/A", "N/A", "N/A"])
    chr_rows.append(["**Total (Jan to May)**", f"**{chr_t5}**", "N/A", "N/A", "N/A"])
    p.append(f"""#### Chronicle points

- OpenMSC calculated it using its [Chronicle dashboard repo]({CHR_REPO}) ([Dune dashboard]({CHR_DASH}))

{table(["Month", "OpenMSC rev", "OpenMSC ref", "Forum rev", "Forum post"], chr_rows)}

- BA Labs can calculate it
    - status: to be filled

#### [Historical Reconciliation] Demand-side primitives

- Note: OpenMSC total = Agent rate + Distribution rewards + Chronicle points. ⚠️ The Jan 2026 Chronicle figure is cumulative since 24 Jul 2025, so the Jan total overstates January itself.

{recon_table(data["dr"]["grove"], row_of("grove", data, "agent rate"), FORUM_DEMAND["grove"],
             extra=("Chronicle points", dict(chr_vals), {"2026-01": "(cumulative since 24 Jul 2025)"}))}
""")

    # ── Obex ──
    std_prime("obex", "Obex", None, AR_NOTE_STD)
    p.append(f"""#### [Historical Reconciliation] Demand-side primitives

- Note: OpenMSC total = Agent rate + Distribution rewards. Obex has no active Distribution Rewards, so the forum “Demand Side Total” is the treasury SSR (agent rate) alone — directly comparable to OpenMSC’s Agent Rate.

{recon_table(data["dr"]["obex"], row_of("obex", data, "agent rate"), FORUM_DEMAND["obex"])}
""")

    # ── Skybase ──
    p.append(f"""## Skybase

### Supply-side revenue

#### Skybase revenue

- No active supply-side primitive instances for Skybase (no allocator ilk; confirmed in [MSC#6]({FORUM["MSC#6"]}) through [MSC#8]({FORUM["MSC#8"]}) forum posts — “Supply Side Total: Sky Share: N/A”).

#### Sky revenue

- N/A — no supply-side primitives, so no Sky supply-side revenue for this prime.

### Demand-side revenue

#### Agent rate (SSR + 20bps on SubProxy)

- OpenMSC calculated it using its [MSC repo]({MSC_REPO})
    - {AR_NOTE_SKYBASE}

{msc_table("skybase", row_of("skybase", data, "agent rate"), None)}

- BA Labs calculated it as well

#### Distribution rewards

- OpenMSC calculated it using its [DR repo]({DR_REPO})
    - {dr_links()}

{dr_table(data["dr"]["skybase"], data["payouts"]["skybase"])}

- BA Labs calculated it as well

#### GAR (Governance Accessibility Rewards)

- OpenMSC cannot calculate it.
- BA Labs calculated it:
    - Amount: **1,523,225 USDS**
    - Period: from May 19, 2025 to May 31, 2026
    - Reference: {GAR_SHEET}

#### [Historical Reconciliation] Demand-side primitives

- Note: OpenMSC total = Agent rate + Distribution rewards. GAR (BA Labs: 1,523,225 USDS for May 19, 2025 → May 31, 2026) is NOT included in the monthly totals — no per-month split is available. Skybase does not appear in the MSC#5 (Jan) post — Forum demand-side starts Feb 2026.

{recon_table(data["dr"]["skybase"], row_of("skybase", data, "agent rate"), FORUM_DEMAND["skybase"])}
""")

    # ── Keel ──
    pio = data["pioneer"]
    pio_rows = [
        ["Jan 2026", "N/A (program starts 30 Mar 2026)", f"[query]({PIO_QUERY})", "N/A", forum_link("2026-01")],
        ["Feb 2026", "N/A (program starts 30 Mar 2026)", f"[query]({PIO_QUERY})", "N/A", forum_link("2026-02")],
        ["Mar 2026", "N/A (Mar 30–31 rolled into the Apr row)", f"[query]({PIO_QUERY})", "N/A", forum_link("2026-03")],
        ["Apr 2026 (cumulative since 30 Mar 2026)", fmt(pio["2026-04"]), f"[query]({PIO_QUERY})", "N/A", forum_link("2026-04")],
        ["May 2026", fmt(pio["2026-05"]), f"[query]({PIO_QUERY})", "N/A", "N/A"],
        ["**Total (Jan to Apr)**", f"**{fmt(pio['2026-04'])}**", "N/A", "N/A", "N/A"],
        ["**Total (Jan to May)**", f"**{fmt(pio['2026-04'] + pio['2026-05'])}**", "N/A", "N/A", "N/A"],
    ]
    p.append(f"""## Keel

### Supply-side revenue

#### Keel revenue

- No active supply-side primitive instances for Keel (no allocator ilk; confirmed in [MSC#7]({FORUM["MSC#7"]}) and [MSC#8]({FORUM["MSC#8"]}) forum posts — “Supply Side Total: Sky Share: N/A”).

#### Sky revenue

- N/A — no supply-side primitives, so no Sky supply-side revenue for this prime.

### Demand-side revenue

#### Agent rate (SSR + 20bps on SubProxy)

- OpenMSC calculated it using its [MSC repo]({MSC_REPO})
    - {AR_NOTE_KEEL}

{msc_table("keel", row_of("keel", data, "agent rate"), None)}

- BA Labs calculated it as well

#### Distribution rewards

- OpenMSC calculated it using its [DR repo]({DR_REPO})
    - {dr_links()}

{dr_table(data["dr"]["keel"], data["payouts"]["keel"])}

- BA Labs calculated it as well

#### Pioneer rewards (to be clarified with @Retro _)

- OpenMSC calculated it using its [Pioneer rewards repo]({PIO_REPO}) ([Dune dashboard]({PIO_DASH}))

{table(["Month", "OpenMSC rev", "OpenMSC ref", "Forum rev", "Forum post"], pio_rows)}

- BA Labs can calculate it
    - status: to be filled

#### [Historical Reconciliation] Demand-side primitives

- Note: OpenMSC total = Agent rate + Distribution rewards + Pioneer rewards. ⚠️ The Apr 2026 Pioneer figure is cumulative since 30 Mar 2026, and Pioneer rewards accrue to the Pioneer cohort (the forum never includes them in Keel’s demand-side total, which reconciles to DR + treasury SSR alone). Keel first appears in MSC#7 (Mar). OpenMSC DR is 0 because referral code 4001 (Solana Bridge) is not yet tracked by the DR repo — Mar 30,241 ≈ 29,062 DR payout + ~1.2K treasury SSR.

{recon_table(data["dr"]["keel"], row_of("keel", data, "agent rate"), FORUM_DEMAND["keel"],
             extra=("Pioneer rewards", dict(pio),
                    {"2026-03": "N/A (rolled into Apr)", "2026-04": "(cumulative since 30 Mar 2026)"}))}
""")

    p.append("""# Pending items

- Grove
    - Cumulative Chronicle points to Grove?
    - We paid Maple DR —> double check whether double payment?
    - Repayment of March Expenses to CC Buffer - https://etherscan.io/tx/0x48c1c90b6193e4d23823930b93315478900a6d9fac801d183c4dd23940b9861e
- Skybase
    - Cumulative GAR? ([ref](https://soterlabs-workspace.slack.com/archives/C0ALAHCKUN5/p1776944451409729?thread_ts=1776859175.003419&cid=C0ALAHCKUN5)) + it applies from May 19, 2025 ([ref](https://sky-atlas.io/#b3f97303-4d41-497c-b931-9337c518bd7e))
    - Double check DR for sUSDC in Arbitrum ([ref](https://soterlabs-workspace.slack.com/archives/C0AKVJFQ5NJ/p1778165664609449))
    - DR difference of 100k on MSC#8? raised by Saba (not clear if true or not)
- Spark
    - Cumulative DR from Aave? ([ref](https://soterlabs-workspace.slack.com/archives/C0ADCL2NR61/p1776787480157539))
    - Find start date
    - DR was overpaid over 2026 on Savings Vaults V2 due to code issue. Reconcile?
    - DR was overpaid over 2026 due to using boosted rates on Savings Vaults V1 incorrectly. Reconcile?
"""
)
    return "\n".join(p)


def main() -> int:
    data = collect()
    doc = build(data)
    OUT.write_text(doc)
    print(f"wrote {OUT} ({len(doc.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
