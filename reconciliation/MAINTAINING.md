# Maintaining the reconciliation post

How to refresh/rebuild `reconciliation_post.md` — the reconciliation between Soter's
recomputed settlement primitives (DR + MSC) and the as-paid figures in the Sky forum
MSC summaries — after the source repos or forum posts change.

Rebuilds `reconciliation_post.md` at the repo root from three inputs:

1. **DR workbook** — `dr_comparison_latest.xlsx`, fetched from GitHub (raw URL in
   `sources.json`). Per-prime rollup uses the workbook's **authoritative `group`
   column** on the `Summary` sheet (NOT fixed ref-code ranges). Soter DR =
   `Soter by Ref Code`; as-paid baseline = **`Payouts`** sheet. Both are rolled up
   by the same ref_code→group map. (`Amatsu` and `BA` sheets are also available in
   the file if a different baseline is ever wanted — see `sources.json`.)
2. **MSC primitives** — `agent_rate`, `prime_agent_profit`, `sky_revenue` parsed
   from each `reports/<prime>/<month>/summary.md` (repo `soterlabs/settlement-reports`), fetched from GitHub raw.
3. **Forum (as-paid) numbers** — curated in `forum_data.json` (not auto-derivable):
   - `forum` — mint debt / SubProxy / GAR per prime per month (from the GovOps
     summary reply in each thread).
   - `demand_side_total` — per-prime **Demand Side Total** (DR + SSR/agent-rate) from
     each thread's **detailed OP (post #1)**. Pre-May this equals the SubProxy; from
     MSC#9 the SubProxy adds supply-side revenue (+ GAR for Skybase) so they diverge.
     **Skybase's Demand Side Total excludes GAR.** Powers §6.

Sources 1 & 2 are configured in **`sources.json`** as URLs with a local fallback:
if a URL fetch fails, `extract_soter.py` falls back to the sibling repos under
`nebula/` (`settle-dr-dune`, `settle/msc/settlement-cycle`).

## Fast path — source data changed

```bash
cd <repo>/reconciliation     # scripts + config live flat at the reconciliation root
python3 extract_soter.py     # GitHub (DR xlsx + summaries) -> soter_data.json
python3 generate_post.py     # both inputs -> reconciliation_post.md
```

`extract_soter.py` needs network + `openpyxl` (both available here). It prints the
DR groups it found and any ref codes the `Summary` sheet didn't group (bucketed to
`Other`). Watch for **new groups** (e.g. `Osero`) or codes drifting between groups.

## Forum numbers changed (or a new month/MSC published)

1. Fetch each post in `forum_data.json → urls`; prefer the Discourse **raw**
   endpoint `https://forum.skyeco.com/raw/<topic_id>/<post_number>` for exact figures.
2. Update `forum_data.json → forum` per prime/month: `mint` (null if no mint-debt
   allocator) and `subproxy`. Omit a prime/month that wasn't settled. (GAR is **not**
   a per-`forum`-entry field — it is computed from the top-level `gar` config block
   = rate × base, current month + backlog true-up; CP likewise from the `cp` block.)
   Also update `forum_data.json → demand_side_total` from the **OP (post #1)** "Demand
   Side Total" line per prime (it = SubProxy pre-May; diverges from MSC#9).
3. New month/MSC: append to `window_months`, `msc_label`, `urls`, add `forum`
   entries, then run the two scripts (the new month's local data is picked up
   automatically if its `summary.md` exists).

## After regenerating

- Tables are data-driven; some **interpretive sentences** are templated literals in
  `generate_post.py` (edit the script if a pattern changes). Key facts:
  - **Structure (built DV → SV → Sky):** §1 Purpose · §2 Scope+defs · **§3 Demand-side (DV)**
    [3.1 DR Soter-vs-Payouts · 3.2 GAR & CP · 3.3 DV total vs forum Demand Side Total] ·
    **§4 Supply-side (SV)** · **§5 Sky-side** (additive) · **§6 Net true-up per entity**
    [6.1 primes = `SV + DV`; 6.2 Sky per prime = Σ §5 Δ = Σ(`sky_revenue + minted SV − forum mint`)].
  - **Definitions (§2.1):** `DV = AR + DR + GAR + CP`; `SV = profit − AR − DR`;
    **`Prime profit = DV + SV`**; **`Sky net = sky_revenue − DV`**.
  - **`prime_agent_profit` INCLUDES DR** (upstream commit fa62647); GAR/CP are NOT in it,
    added from `forum_data.json → gar`/`cp`. So `Prime profit = profit + GAR + CP`.
  - **§4 SV** reconciles our SV vs settled = `sv_paid` = `SubProxy − demand settled`,
    where **`demand settled` = `demand_paid()` = Demand Side Total + post-switch CP**
    (CP is demand-side, excluded from the supply figure). 0 through MSC#8 = arrears;
    from MSC#9 = forum Prime Share. Grove May Δ = its token-launch penalty (left
    visible; now **+152,091**, i.e. net of CP). **§5 Sky** additive: `mint = Sky net + DV
    + SV`, SV minted = full SV from MSC#9, 0 before. **§6 true-up** = Σ(SV−sv_paid) +
    Σ(DV−demand_paid) = Prime profit − SubProxy. The DV sum has ONE source — `our_dv()`
    (`minted=True` adds the GAR backlog true-up for §5); the demand baseline has one
    source — `demand_paid()`. **CP washes** (added in `our_dv`, added back in
    `demand_paid`), so it nets to 0 in §6 and never touches the supply side.
  - **GAR** (Skybase only) = 1% × base, current-month + May true-up; **CP** (Grove only,
    Dune dashboard) = MSC#8 backlog + MSC#9 Apr/May. Both fold into DV; reconcile to ~0.
    Grove Apr–May DR was **not settled**, so its DV Δ is mostly unpaid DR.
  - Code **127 is in the Spark group** (no "Other" caveat). No VSR / Savings V2 mention. No §7/§8/§9.
    Settlement flow: Jan–Apr debt-PnL (mint = Sky Share); May Full-PnL (mint = Sky Share + Prime Share).
- `soter_data.json` is a generated cache (safe to overwrite). `forum_data.json` is
  the hand-maintained source of truth for as-paid figures — keep it in git.

## Files

Everything lives flat at the **reconciliation root** (pushable to a repo as-is):

```
reconciliation/
├── README.md               ← overview + quickstart
├── MAINTAINING.md          ← this file
├── reconciliation_post.md  ← generated deliverable
├── sources.json            ← URLs + sheet names for DR xlsx & settlements (edit to retarget)
├── forum_data.json         ← curated as-paid forum figures (edit this)
├── soter_data.json         ← generated cache (extract_soter.py output; gitignored)
├── extract_soter.py        ← GitHub sources -> soter_data.json
└── generate_post.py        ← inputs -> reconciliation_post.md
```

Env overrides: the DR workbook is URL-driven via `sources.json` (no env override).
For the settlements, set `SETTLEMENTS_BASE_URL` or `SETTLEMENTS_DIR` (local).
`FORUM_DATA`, `SOTER_DATA`, `OUTPUT_FILE` are also overridable.
