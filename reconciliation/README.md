# Sky MSC Settlement Reconciliation

Soter Labs' independent reconciliation of the Sky Ecosystem **Monthly Settlement
Cycle (MSC)**. For each prime agent (Spark, Grove, Obex, Skybase, Keel) we recompute
the settlement primitives from source data and reconcile them against the as-paid
figures published in the Sky forum MSC summaries, for **MSC#5–#9 (Jan–May 2026)**.

## Deliverable

- **[`reconciliation_post.md`](reconciliation_post.md)** — the generated forum post.
  This file is **regenerated, not hand-edited** — edit the generator, not the output.

## How it's built

Two scripts at the repo root turn three inputs into the post:

```
            ┌─ DR workbook (GitHub)         ┐
extract_soter.py ─ MSC summaries (GitHub)   ┼─→ soter_data.json ─┐
            └─ (local repos = fallback)     ┘                    ├─→ generate_post.py ─→ reconciliation_post.md
                                              forum_data.json ───┘   (as-paid, hand-maintained)
```

| File | Role |
|---|---|
| `extract_soter.py` | Fetches the DR workbook + per-prime `summary.md`, writes `soter_data.json` (generated cache). |
| `generate_post.py` | Combines `soter_data.json` + `forum_data.json` → `reconciliation_post.md`. |
| `sources.json` | URLs + sheet names for the Soter-side sources (URL primary, local fallback). |
| `forum_data.json` | Curated **as-paid** forum figures (mint / SubProxy / Demand Side Total / GAR / CP). The hand-maintained source of truth. |
| `soter_data.json` | Generated cache — safe to overwrite (git-ignored). |

## Refresh

```bash
cd reconciliation           # this folder
python3 extract_soter.py     # sources -> soter_data.json   (needs network + openpyxl)
python3 generate_post.py     # both inputs -> reconciliation_post.md
```

The DR rollup uses the workbook's authoritative `group` column (`Summary` sheet), not
fixed ref-code ranges. Soter DR = `Soter by Ref Code`; as-paid baseline = `Payouts` sheet.
`extract_soter.py` prints the DR groups it found — watch for **new groups** (e.g. `Osero`)
or ref codes drifting between groups (bucketed to `Other`).

Forum numbers changed / new month: update `forum_data.json → forum` (mint / SubProxy per
prime/month, from the GovOps summary reply) and `demand_side_total` (per-prime Demand Side
Total from the OP, post #1). GAR/CP come from the top-level `gar`/`cp` config blocks. For a
new month, also append to `window_months`, `msc_label`, `urls`.

## Methodology

Definitions (`DV`, `SV`, `Prime profit`, `Sky net`) and the settlement-flow notes are
in **§2 of [`reconciliation_post.md`](reconciliation_post.md)** itself. Key invariants when
editing the generator (tables are data-driven; some interpretive sentences are templated
literals in `generate_post.py`):

- `DV = AR + DR + GAR + CP`; `SV = profit − AR − DR`; `Prime profit = DV + SV`;
  `Sky net = sky_revenue − DV`.
- `prime_agent_profit` **includes DR** (upstream commit fa62647); GAR/CP are added from
  `forum_data.json`. So `Prime profit = profit + GAR + CP`.
- §6 true-up = `Σ(SV − sv_paid) + Σ(DV − demand_paid) = Prime profit − SubProxy`. DV has one
  source (`our_dv()`); the demand baseline one source (`demand_paid()`). **CP washes**
  (added in both), so it nets to 0 in §6 and never touches the supply side.
- GAR (Skybase only) = 1% × base, current-month + May true-up; CP (Grove only) = MSC#8
  backlog + MSC#9 Apr/May. Both fold into DV, reconcile to ~0.
