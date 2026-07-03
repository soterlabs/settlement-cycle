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

## Methodology

Definitions (`DV`, `SV`, `Prime profit`, `Sky net`) and the settlement-flow notes are
in **§2 of [`reconciliation_post.md`](reconciliation_post.md)** itself. See
**[`MAINTAINING.md`](MAINTAINING.md)** for what to update when forum numbers change or a
new MSC is published.
