#!/usr/bin/env python3
"""Generate the reconciliation forum post from soter_data.json + forum_data.json.

Run (after extract_soter.py):  python3 generate_post.py
Writes OUTPUT_FILE (default: reconciliation/reconciliation_post.md).
"""
import json, os, re
from pathlib import Path


def reflow(lines):
    """Collapse hard-wrapped lines so each paragraph / bullet / blockquote is one
    line. Tables (`|`), headers (`#`), and rules (`---`) stay one-per-line."""
    out, block = [], []

    def flush():
        if not block:
            return
        if any(l.lstrip().startswith("|") for l in block):
            out.extend(block)                                  # table rows: verbatim
            return
        head = block[0].lstrip()
        if head.startswith("#") or head.startswith("---"):
            out.extend(block)                                  # headers / rules: verbatim
            return
        if head.startswith(">"):                               # blockquote: one line
            out.append("> " + " ".join(re.sub(r"^\s*>\s?", "", l).strip() for l in block))
            return
        cur = None                                             # prose + list items
        for l in block:
            if re.match(r"^\s*([-*]|\d+\.)\s", l):             # new bullet / number
                if cur is not None:
                    out.append(cur)
                cur = l.rstrip()
            else:
                cur = l.strip() if cur is None else cur + " " + l.strip()
        if cur is not None:
            out.append(cur)

    for line in lines:
        if line.strip() == "":
            flush(); block = []; out.append("")
        else:
            block.append(line)
    flush()
    return out

ROOT = Path(__file__).resolve().parent          # reconciliation/ — scripts, config, output

FORUM_DATA = Path(os.environ.get("FORUM_DATA", ROOT / "forum_data.json"))
SOTER_DATA = Path(os.environ.get("SOTER_DATA", ROOT / "soter_data.json"))
OUT = Path(os.environ.get("OUTPUT_FILE", ROOT / "reconciliation_post.md"))

fd = json.loads(FORUM_DATA.read_text())
sd = json.loads(SOTER_DATA.read_text())
src = json.loads((ROOT / "sources.json").read_text())

def _blob(raw):  # raw.githubusercontent URL -> human-clickable github blob
    return raw.replace("raw.githubusercontent.com/", "github.com/", 1).replace("/main/", "/blob/main/", 1)
DR_URL = _blob(src["dr_xlsx"]["url"])
SET_URL = _blob(src["settlements"]["base_url"])

MONTHS = fd["window_months"]
MSC = fd["msc_label"]
URLS = fd["urls"]
_bad = [m for m in MONTHS if m not in MSC or m not in URLS]
if _bad:
    raise SystemExit(f"forum_data.json: window_months entries missing from "
                     f"msc_label/urls: {_bad} — add them in all three places")
FORUM = fd["forum"]
DST = fd.get("demand_side_total", {})
SUPPLY_FROM = fd.get("supply_side_paid_from")   # first month mint includes prime supply-side
GARCFG = fd.get("gar", {})                      # GAR = rate × base, one prime only
CPCFG = fd.get("cp", {})                        # Chronicle Points, one prime only
PRIMES = sd["primes"]

# The supply-side switch month drives both the SV/mint gating and the switch-month
# prose (SWITCH/THROUGH). If it isn't in-window the two would silently disagree.
if SUPPLY_FROM and SUPPLY_FROM not in MONTHS:
    raise SystemExit(f"forum_data.json: supply_side_paid_from {SUPPLY_FROM!r} is not in "
                     f"window_months {MONTHS} — the SV/mint columns gate on it while the "
                     f"narrative falls back to the last month, so they would contradict each "
                     f"other. Add that month to window_months or adjust supply_side_paid_from.")
if SUPPLY_FROM and SUPPLY_FROM == MONTHS[0]:
    print(f"WARNING: supply_side_paid_from == first window month ({SUPPLY_FROM}) — there is "
          f"no pre-switch month in the window, so the 'through <prev MSC>' narrative is degenerate.")

# GAR's current-month component is rate × gar.base[m]; a window month >= settle_from with
# no base entry would be silently treated as 0 (understating that prime's demand-side & true-up).
if GARCFG.get("prime") and GARCFG.get("settle_from"):
    _sf, _base = GARCFG["settle_from"], GARCFG.get("base", {})
    _missing_base = [m for m in MONTHS if m >= _sf and m not in _base]
    if _missing_base:
        raise SystemExit(f"forum_data.json: gar.base is missing window month(s) {_missing_base} "
                         f"(>= settle_from {_sf}) — current-month GAR for {GARCFG['prime']} would "
                         f"be silently treated as 0. Add the gar.base entries.")

PROFIT = sd["prime_agent_profit"]
SKY = sd["sky_revenue"]
AR = sd["agent_rate"]
DR_SOTER = sd["dr_soter"]
DR_BASE = sd["dr_baseline"]                       # as-paid DR (Payouts sheet)
DR_BASE_NAME = sd.get("dr_baseline_name", "Payouts")

def g(d, p, m):
    return d.get(p, {}).get(m)

def forum_val(p, m, key):
    cell = FORUM.get(p, {}).get(m)
    return None if cell is None else cell.get(key)

def sv(p, m):
    """Supply-side revenue (SV) = profit − AR − DR (= net_revenue + SDE)."""
    pr, ar = g(PROFIT, p, m), g(AR, p, m)
    return None if (pr is None or ar is None) else pr - ar - (g(DR_SOTER, p, m) or 0.0)

# ---- GAR (Governance Accessibility Rewards) = rate × base; one prime only ----
_GAR_RATE = GARCFG.get("rate", 0.0)
_GAR_BASE = GARCFG.get("base", {})
def _is_gar_prime(p):
    return GARCFG.get("prime") == p
def gar_monthly(p, m):
    """That month's own GAR = rate × base."""
    return _GAR_BASE.get(m, 0.0) * _GAR_RATE if _is_gar_prime(p) else 0.0
def gar_in_dst(p, m):
    """Current-month GAR bundled into the Demand Side Total — only once GAR began being
    settled (`settle_from`)."""
    sf = GARCFG.get("settle_from")
    return gar_monthly(p, m) if (_is_gar_prime(p) and sf and m >= sf) else 0.0
def gar_trueup(p, m):
    """One-time backlog true-up paid in `trueup_paid_month` = rate × Σ base over period."""
    if not _is_gar_prime(p) or m != GARCFG.get("trueup_paid_month"):
        return 0.0
    s, e = GARCFG.get("trueup_period", [None, None])
    return sum(v * _GAR_RATE for k, v in _GAR_BASE.items() if s and s <= k <= e)
def gar_in_subproxy(p, m):
    """Total GAR in that month's SubProxy = current-month GAR + any backlog true-up."""
    return gar_in_dst(p, m) + gar_trueup(p, m)

def cp_in_subproxy(p, m):
    """Chronicle Points paid in that month's SubProxy (Grove only; demand-side)."""
    return CPCFG.get("subproxy", {}).get(m, 0.0) if CPCFG.get("prime") == p else 0.0

def our_dv(p, m, *, minted=False):
    """Our demand-side revenue (DV) = AR + DR + GAR + CP. Single source of truth for
    the DV sum (used by §3.3, §5, §6). `minted=True` (the §5 Surplus-Buffer mint
    decomposition) uses GAR-in-SubProxy — current month + the one-time backlog
    true-up; the demand reconciliation (minted=False) uses the current-month GAR."""
    gar = gar_in_subproxy(p, m) if minted else gar_in_dst(p, m)
    return (g(AR, p, m) or 0.0) + (g(DR_SOTER, p, m) or 0.0) + gar + cp_in_subproxy(p, m)

def demand_side(p, m):
    """Full DV as minted into the Surplus Buffer (GAR backlog true-up + CP included)."""
    return our_dv(p, m, minted=True)

def demand_paid(p, m):
    """Forum demand-side actually settled. Post-switch the itemized Demand Side Total
    excludes CP (CP is a separate SubProxy line), so add it back; pre-switch the
    un-itemized DST already contains CP. None if the prime/month wasn't settled."""
    dst = DST.get(p, {}).get(m)
    if dst is None:
        return None
    cp = cp_in_subproxy(p, m) if (SUPPLY_FROM and m >= SUPPLY_FROM) else 0.0
    return dst + cp

def minted_sv(p, m):
    """SV actually minted into the Surplus Buffer that month (0 before the switch)."""
    return (sv(p, m) or 0.0) if (SUPPLY_FROM and m >= SUPPLY_FROM) else 0.0

def sv_paid(p, m):
    """Supply share actually transferred to the prime = `SubProxy − demand settled`
    (0 before the switch; minting primes only). CP is demand-side, so it is excluded
    from the supply figure. For Grove May this is net of the token-launch penalty, so
    the SV Δ surfaces that penalty."""
    if not (SUPPLY_FROM and m >= SUPPLY_FROM) or forum_val(p, m, "mint") is None:
        return 0.0
    sub, dp = forum_val(p, m, "subproxy"), demand_paid(p, m)
    return (sub - dp) if (sub is not None and dp is not None) else 0.0

def f(x):
    if x is None:
        return "—"
    if -0.5 < x < 0.5:            # avoid a misleading "-0"
        x = 0.0
    return f"{x:,.0f}"

def fdiff(x):
    if x is None:
        return "—"
    if -0.5 < x < 0.5:            # near-zero residual prints unsigned "0"
        return "0"
    s = f"{x:,.0f}"
    return f"+{s}" if x > 0 else s

# warn (don't fail — a freshly-added month may not be settled yet) if a window month
# has no as-paid forum entries, so blank columns aren't mistaken for "Sky paid nothing".
_noforum = [m for m in MONTHS
            if all(forum_val(p, m, "subproxy") is None and forum_val(p, m, "mint") is None
                   for p in PRIMES)]
if _noforum:
    print(f"WARNING: forum_data.json has no mint/SubProxy entries for window month(s) "
          f"{_noforum} — as-paid columns will render blank ('—'), not $0")

# The dangerous inverse: the forum settled a prime-month (it has a SubProxy) but the Soter
# recompute is entirely missing (summary.md fetch failed with no local fallback). Such a row
# renders blank '—' and silently drops out of the §6 true-up — flag it loudly, distinct from
# a genuinely unsettled month above.
_settled_no_soter = [(p, m) for p in PRIMES for m in MONTHS
                     if forum_val(p, m, "subproxy") is not None
                     and g(PROFIT, p, m) is None and g(SKY, p, m) is None and g(AR, p, m) is None]
if _settled_no_soter:
    print(f"WARNING: forum settled but Soter recompute MISSING (data gap, not $0) for "
          f"{_settled_no_soter} — these rows render '—' and drop out of the §6 totals; "
          f"check the summary.md fetch for those prime-months")

L = []
def w(s=""):
    L.append(s)

first, last = MONTHS[0], MONTHS[-1]
mon_name = {"01": "January", "02": "February", "03": "March", "04": "April",
            "05": "May", "06": "June", "07": "July", "08": "August",
            "09": "September", "10": "October", "11": "November", "12": "December"}
def mname(m):
    return mon_name[m[5:]]
def num(m):
    return MSC[m].replace("MSC#", "").replace("MSC", "")
# "MSC #5–#9"
RANGE = f"MSC #{num(first)}–#{num(last)}"
# the month the payout basis switched to full profit = last month;
# "through <prev>" = the month before it.
# the supply-side regime switch is governed by SUPPLY_FROM, not the window edges
SWITCH_M = SUPPLY_FROM if (SUPPLY_FROM in MONTHS) else last
_si = MONTHS.index(SWITCH_M)
prev = MONTHS[_si - 1] if _si > 0 else SWITCH_M
SWITCH = MSC[SWITCH_M]        # e.g. MSC#9
THROUGH = MSC[prev]          # the month before the switch, e.g. MSC#8

# ---------- header ----------
w(f"# Settlement Reconciliation — {RANGE} "
  f"({mname(first)}–{mname(last)} {last[:4]})")
w()
w("*Prepared by Soter Labs. Reconciles our independent recompute of the monthly")
w("settlement primitives against the amounts published in the "
  f"{RANGE} settlement summaries.*")
w()
w("Source posts: " + " · ".join(f"[{MSC[m]}]({URLS[m]})" for m in MONTHS))
w()
w("> **Shorthand:** *\"Surplus Buffer\"* is short for *mint the debt and transfer Sky")
w("> profit to the Surplus Buffer*; *\"SubProxy\"* is short for *transfer the prime's")
w("> payment to its SubProxy*.")
w()
w("---")
w()
w("**Scope note — DR cases not yet addressed.** This reconciliation does not yet")
w("cover the following distribution-rewards cases:")
w()
w("1. **Aggregators** (e.g. Yearn, Velora, LazySummer) — Skybase only.")
w("2. **Morpho vaults and markets** — Skybase and Grove.")
w("3. **Ref code 0 for PSM3 on L2s** — Skybase only.")
w()
w("---")
w()

# ---------- 1. purpose ----------
w("## 1. Purpose")
w()
w("Our independent recompute of each prime agent's settlement primitives vs. the")
w(f"figures actually settled on the forum, {MSC[first]}–{MSC[last]} "
  f"({mname(first)}–{mname(last)} {last[:4]}) — **per prime, per month**.")
w()
w("We also reconcile **distribution rewards** specifically — our recompute against the")
w("DR actually paid out (§3.1) — alongside the demand-, supply- and Sky-side primitives.")
w()
w("Two demand-side primitives are **not yet in the `settlement-reports` repo** and are")
w("added here from external sources: **GAR** (Governance Accessibility Rewards, Skybase) and")
w("**CP** (Chronicle Points, Grove). Both reconcile to 0 — see §3.2.")
w()
w(f"**Why this post exists.** From {MSC[first]} to {THROUGH} ({mname(prev)} 2026) the settlements")
w("paid each prime only its **demand-side** entitlement (`agent_rate` + DR); the")
w("prime's **supply-side revenue share** was **not transferred**. That is known")
w("technical debt in the early MSC cycles, and truing it up is the point of this")
w("reconciliation — the tables below quantify the unpaid gap per prime and month.")
w()
w("**Headline:** the **Sky-side** claim (*mint debt → Surplus Buffer*) reproduces")
w("our `sky_revenue` closely. The **prime-payout side** (*Send to SubProxy*) carries")
w("the material, *structural* differences:")
w()
w(f"1. **From {MSC[first]} to {THROUGH} ({mname(prev)}), the SubProxy payout excluded net")
w(f"   trading revenue** — it tracked only `agent_rate + distribution rewards`. From")
w(f"   **{SWITCH} ({mname(SWITCH_M)}) it moved toward full `prime_agent_profit`**.")
w(f"2. **Our DR recompute reconciles closely with the DR actually paid** (the")
w(f"   `{DR_BASE_NAME}` figures) once the workbook's own prime grouping is used — residuals")
w("   are modest (see §3.1). Keel is the exception: it accrued DR before any was paid.")
w()
w("---")
w()

# ---------- 2. scope ----------
w("## 2. Scope & method")
w()
w("### 2.1. Details")
w()
w(f"- **Primes:** {', '.join(PRIMES)}.")
w(f"- **Window:** {first} … {last} ({RANGE}).")
w("- **Sky-side** — our `sky_revenue` vs forum **mint debt / Surplus Buffer**.")
w("- **Prime-payout** — our **Prime profit** vs forum **Send to SubProxy**.")
w("- **DR rollup** — per prime via the workbook's authoritative `group` column")
w("  (`Summary` sheet), not fixed ref-code ranges.")
w(f"- **DR baseline** — as-paid DR is the **`{DR_BASE_NAME}`** sheet (actual distributions).")
w("- All figures USDS. **Δ = Soter − forum** (positive = our recompute is higher).")
w()
w("**Definitions.**")
w("- **AR** — agent rate (`SSR + 20bps` on subproxy USDS; see §3.3 note).")
w("- **DR** — distribution rewards (active referral codes); Obex earns none.")
w("- **GAR** — Governance Accessibility Rewards (Skybase only; see §3.2).")
w("- **CP** — Chronicle Points (Grove only; see §3.2).")
w("- **DV** — demand-side revenue = `AR + DR + GAR + CP`.")
w("- **SV** — supply-side revenue = net trading revenue + SDE = `profit − AR − DR` (only AR")
w("  and DR are netted out — they're the demand-side items already inside `prime_agent_profit`;")
w("  GAR/CP aren't in `profit`, they sit in DV, so they are **not** subtracted here).")
w("- **Prime profit** = `DV + SV` (= `prime_agent_profit` + GAR + CP) — what the prime should receive.")
w("- **Sky net** = `sky_revenue − DV` — Sky's revenue net of the demand side it pays the prime.")
w()
w("**Sources** (every figure is reproducible):")
w("- Forum as-paid figures — the five MSC posts linked above.")
w(f"- Our MSC primitives — each prime's [`reports/<prime>/<month>/summary.md`]({SET_URL}).")
w(f"- DR (Soter + {DR_BASE_NAME}) — `Summary` / `Soter by Ref Code` / `{DR_BASE_NAME}` sheets of "
  f"[`dr_comparison_latest.xlsx`]({DR_URL}).")
w()
w("### 2.2. Venue support")
w()
w("We have **not** included Distribution Rewards for **aggregators** (1inch, Verlora, etc.),")
w("**Morpho Vaults/Markets**, and **PSM3 on L2s** *(to be confirmed)*. These can trigger")
w("double-counting, and we want clean frameworks before including them.")
w()
w("---")
w()

# ---------- 3. demand-side (DV) ----------
w("## 3. Demand-side (DV) reconciliation")
w()
w("The prime's demand-side revenue **`DV = AR + DR + GAR + CP`**. We reconcile each part")
w("against what was paid, then the DV total against the forum **Demand Side Total**.")
w()
w(f"### 3.1. Distribution rewards — Soter vs {DR_BASE_NAME} (as-paid)")
w()
w("> DR **excludes the venue classes flagged in §2.2**, pending clean frameworks.")
w()
w(f"Δ = Soter − {DR_BASE_NAME}. The `{DR_BASE_NAME}` sheet covers only the months shown.")
w()
dr_primes = [p for p in PRIMES if DR_BASE.get(p) or DR_SOTER.get(p)]
for p in dr_primes:
    rows = []
    for m in MONTHS:
        a = g(DR_BASE, p, m)
        if a is None:
            continue
        s = g(DR_SOTER, p, m) or 0.0
        rows.append(f"| {MSC[m]} {m} | {f(s)} | {f(a)} | {fdiff(s - a)} |")
    if not rows:                          # prime has no as-paid baseline month — skip
        continue
    w(f"**{p}**")
    w()
    w(f"| Month | Soter DR | {DR_BASE_NAME} (paid) | Δ |")
    w("|---|--:|--:|--:|")
    for r in rows:
        w(r)
    w()
# Surface DR in workbook groups not mapped to any prime (excluded venues per §2.2 — NOT
# attributed to a prime and excluded from this reconciliation), so it is never silently dropped.
_nonprime = {grp: DR_SOTER[grp] for grp in DR_SOTER if grp not in PRIMES}
_np_total = sum(sum(v.values()) for v in _nonprime.values())
if _np_total:
    w(f"> **Excluded (non-prime).** A further **{f(_np_total)}** of Soter-computed DR over the "
      f"window sits in workbook groups not mapped to any prime "
      f"(`{'`, `'.join(sorted(_nonprime))}` — the excluded venue classes in §2.2). It is "
      f"intentionally **not** attributed to any prime and excluded from this reconciliation.")
    w()
w("### 3.2. GAR & CP — primitives outside `settlement-reports`")
w()
w("Not yet in the `settlement-reports` repo; added from external sources. Both are paid via")
w("the SubProxy and reconcile to **0**.")
w()
w("**GAR — Governance Accessibility Rewards (Skybase).**")
w()
gp = GARCFG.get("prime")
if gp and _GAR_BASE:
    s, e = GARCFG.get("trueup_period", ["", ""])
    tu_month = GARCFG.get("trueup_paid_month")
    cur = gar_in_dst(gp, tu_month)
    tu = gar_trueup(gp, tu_month)
    w(f"GAR = **{_GAR_RATE:.0%} of a monthly base**, **only {gp}**. First settled in "
      f"{MSC.get(tu_month, tu_month)} ({mname(tu_month)} {tu_month[:4]}): the month's own GAR + a")
    w(f"one-time backlog true-up ({mname(s)} {s[:4]} – {mname(e)} {e[:4]}). We add the same, so it reconciles to **0**.")
    w()
    w("| Component | Period | Amount |")
    w("|---|---|--:|")
    w(f"| Current-month GAR | {mname(tu_month)} {tu_month[:4]} | {f(cur)} |")
    w(f"| Backlog true-up | {mname(s)} {s[:4]} – {mname(e)} {e[:4]} | {f(tu)} |")
    w(f"| **Total GAR in {MSC.get(tu_month, tu_month)} SubProxy** | | **{f(cur + tu)}** |")
else:
    w("No prime earned GAR in this window.")
w()
w("**CP — Chronicle Points (Grove).**")
w()
cpp = CPCFG.get("prime")
cpsub = CPCFG.get("subproxy", {})
if cpp and cpsub:
    w(f"CP = 20% of the base rate on the Chronicle Farm USDS balance ([dashboard]"
      f"(https://dune.com/lakonema2000_/chronicle-points-monthly-summary)); **only {cpp}**. Paid")
    w(f"Surplus Buffer → SubProxy: {THROUGH} settled the backlog (program start → Mar 2026), {SWITCH}")
    w("settled Apr + May. We add the same, so it reconciles to **0**.")
    w()
    w("| Settled in | CP |")
    w("|---|--:|")
    for m in sorted(cpsub):
        w(f"| {MSC.get(m, m)} ({mname(m)} {m[:4]}) | {f(cpsub[m])} |")
    w(f"| **Total** | **{f(sum(cpsub.values()))}** |")
else:
    w("No prime earned CP in this window.")
w()
w("### 3.3. DV total vs forum Demand Side Total")
w()
w("Our `DV = AR + DR + GAR + CP` vs the forum **demand settled** = Demand Side Total + any")
w("CP paid via the SubProxy (so CP appears on both sides and washes out). Δ = ours − settled.")
w()
w("> **Agent-rate definition.** The settlements compute AR as *\"SSR on subproxy USDS\"*; per")
w("> Atlas it is **`SSR + 20bps`** — the 20bps was understated. Our `AR` uses `SSR + 20bps`.")
w()
w("> **Grove (Apr–May).** DR was **not settled** these months, so the demand budget covers")
w("> `AR + CP` first (April leaves only ~13k for DR; May none). Grove's Δ is essentially unpaid DR.")
w()
for p in PRIMES:
    if not DST.get(p):
        continue
    has_gar = _is_gar_prime(p)
    has_cp = (CPCFG.get("prime") == p)
    extra_hdr = ("| GAR " if has_gar else "") + ("| CP " if has_cp else "")
    w(f"**{p}**")
    w()
    w(f"| Month | agent_rate | DR (Soter) {extra_hdr}| ours | Forum demand settled | Δ |")
    w("|---|--:|--:|" + ("--:|" if has_gar else "") + ("--:|" if has_cp else "") + "--:|--:|--:|")
    for m in MONTHS:
        dp = demand_paid(p, m)
        if dp is None:
            continue
        ar = g(AR, p, m) or 0.0
        dr = g(DR_SOTER, p, m) or 0.0
        gd = gar_in_dst(p, m)
        cp = cp_in_subproxy(p, m)
        ours = our_dv(p, m)
        extra = (f"{f(gd)} | " if has_gar else "") + (f"{f(cp)} | " if has_cp else "")
        w(f"| {MSC[m]} {m} | {f(ar)} | {f(dr)} | {extra}{f(ours)} | {f(dp)} | {fdiff(ours - dp)} |")
    w()

# ---------- 4. supply-side (SV) ----------
mint_primes = [p for p in PRIMES if any(forum_val(p, m, "mint") is not None for m in MONTHS)]
w("## 4. Supply-side (SV) reconciliation")
w()
w("The prime's supply-side revenue **`SV = profit − AR − DR`** (net trading revenue + SDE).")
w(f"Through {THROUGH} the supply side was **not settled** — reconciled against 0 (the unpaid")
w(f"arrears). From {SWITCH} it was settled as the forum **Prime Share** (`SubProxy − demand")
w("settled`, i.e. net of AR + DR + CP). Δ = our SV − settled. (Primes with no allocation module have no SV.)")
w()
for p in mint_primes:
    w(f"**{p}**")
    w()
    w("| Month | our SV | settled (Prime Share) | Δ |")
    w("|---|--:|--:|--:|")
    for m in MONTHS:
        our_sv = sv(p, m)
        if our_sv is None:
            w(f"| {MSC[m]} {m} | — | — | — |")
            continue
        settled = sv_paid(p, m)
        w(f"| {MSC[m]} {m} | {f(our_sv)} | {f(settled)} | {fdiff(our_sv - settled)} |")
    w()
_gpen = (sv("Grove", SWITCH_M) or 0.0) - sv_paid("Grove", SWITCH_M)
if "Grove" in mint_primes and abs(_gpen) >= 0.5:
    w(f"> Grove's {SWITCH} Δ ({fdiff(_gpen)}) is its **token-launch penalty** — the penalty reduced")
    w("> the transferred Prime Share below our SV; left visible.")
w()

# ---------- 5. sky-side ----------
w("## 5. Sky-side — Surplus Buffer = `Sky net + DV + SV`")
w()
w("Sky **mints to the Surplus Buffer**, keeps its **net revenue**, and transfers `DV + SV` to")
w("the SubProxy. So the mint decomposes additively as `Sky net + DV + SV`, **Sky net =")
w(f"`sky_revenue − DV`**. Δ = our mint − forum mint. Through {THROUGH} SV wasn't minted (shows")
w(f"as 0; trued up in §6); from {SWITCH} SV is minted, so Δ → ~0.")
w()
for p in mint_primes:
    w(f"**{p}**")
    w()
    w("| Month | Sky net | DV | SV | = our mint | forum mint | Δ |")
    w("|---|--:|--:|--:|--:|--:|--:|")
    for m in MONTHS:
        mn = forum_val(p, m, "mint")
        if mn is None:
            w(f"| {MSC[m]} {m} | — | — | — | — | — | — |")
            continue
        dv = demand_side(p, m)
        sv_m = minted_sv(p, m)
        sky_net = (g(SKY, p, m) or 0.0) - dv
        our_mint = sky_net + dv + sv_m
        w(f"| {MSC[m]} {m} | {f(sky_net)} | {f(dv)} | {f(sv_m)} | {f(our_mint)} | "
          f"{f(mn)} | {fdiff(our_mint - mn)} |")
    w()

# ---------- 6. true-up ----------
w("## 6. Net true-up per entity")
w()
w("### 6.1. Primes — `SV + DV`")
w()
w("Per prime, the Jan–May sum of each reconciled side (= `Prime profit − SubProxy`).")
w("**Positive = transfer owed to the prime; negative = grab back.**")
w()
w(f"- **Supply-side (SV)** — Σ (our SV − settled): the unpaid arrears through {THROUGH} plus any {SWITCH} shortfall (§4).")
w("- **Demand-side (DV)** — Σ (our DV − forum demand settled) (§3.3); GAR/CP folded in (CP washes).")
w()
w("| Prime | Supply-side (SV) | Demand-side (DV) | **Net true-up** |")
w("|---|--:|--:|--:|")
for p in PRIMES:
    # SV applies only to minting primes (those with an allocation module); §4 lists
    # exactly these. Non-minting primes have no supply side, only demand-side DV.
    minting = p in mint_primes
    sv_tot = sum((sv(p, m) or 0.0) - sv_paid(p, m) for m in MONTHS) if minting else 0.0
    dv_tot = sum(our_dv(p, m) - dp
                 for m in MONTHS if (dp := demand_paid(p, m)) is not None)
    net = sv_tot + dv_tot
    sv_cell = fdiff(sv_tot) if minting else "—"
    w(f"| {p} | {sv_cell} | {fdiff(dv_tot)} | **{fdiff(net)}** |")
w()
w("### 6.2. Sky — per prime")
w()
w("Sky's own true-up: per minting prime, the Jan–May sum of the §5 mint reconciliation")
w("(Σ `our mint − forum mint`). Since DV cancels in the mint, this is just Σ (`sky_revenue +")
w("minted SV − forum mint`). **Positive = Sky under-minted to the Surplus Buffer vs our recompute;")
w("negative = over-minted.**")
w()
w("| Prime | Sky-side residual (Σ §5 Δ) |")
w("|---|--:|")
for p in mint_primes:
    sky_tot = sum(((g(SKY, p, m) or 0.0) + minted_sv(p, m)) - mn
                  for m in MONTHS if (mn := forum_val(p, m, "mint")) is not None)
    w(f"| {p} | {fdiff(sky_tot)} |")
w()

L = reflow(L)
OUT.write_text("\n".join(L) + "\n")
print(f"wrote {OUT}  ({len(L)} lines)")
