# preparation/ — MSC#9 preparation doc

`doc.md` is **generated** — don't edit it by hand. Edit the template /
constants in `scripts/generate_doc.py` and re-run.

## Regenerate doc.md

```bash
# from the repo root
set -a; source .env; set +a        # provides DUNE_API_KEY
python3 preparation/scripts/generate_doc.py
```

That's it. The script fetches every data source, recomputes every table
cell, and rewrites `preparation/doc.md` (~400 lines). Re-run it whenever
any source changes.

## Data sources

| What | Source | Used for |
|---|---|---|
| Settlement reports | [soterlabs/settlement-reports](https://github.com/soterlabs/settlement-reports) `reports/{prime}/{YYYY-MM}/summary.md` | supply-side revenue (= "prime agent net revenue" + "prime side sky direct exposure"), "sky revenue", "agent rate" |
| DR results | [dr_comparison_2026.xlsx](https://github.com/stablewatch-io/settle-dr-dune/blob/main/dune-results/dr_comparison_2026.xlsx) (sheet `Soter Data`) | Distribution Rewards per referral code per month |
| DR payouts + code→prime mapping | [published Google sheet](https://docs.google.com/spreadsheets/d/e/2PACX-1vR-dLvndU-DM1j_8gxYIhfYOtoIgyEJ9Jg5R0RcV-ZRVGdOJdmwIysO4P9yfacw-CkBGJjXPgwbC6WB/pubhtml) ([CSV export](https://docs.google.com/spreadsheets/d/e/2PACX-1vR-dLvndU-DM1j_8gxYIhfYOtoIgyEJ9Jg5R0RcV-ZRVGdOJdmwIysO4P9yfacw-CkBGJjXPgwbC6WB/pub?gid=753291318&single=true&output=csv)) | referral code → prime mapping; "Past payout" columns |
| Chronicle points | Dune query [7696411](https://dune.com/queries/7696411/11654800) (latest stored result, via Dune API — no credits) | Grove 2.e |
| Pioneer rewards | Dune query [7696529](https://dune.com/queries/7696529) (latest stored result) | Keel 2.d |
| Forum finals | MSC#5–8 forum posts, transcribed into `FORUM_SKY` / `FORUM_DEMAND` constants in the script | "Forum rev" / "Forum demand-side" columns |

Forum finals are immutable history — they live as constants in the
script (with the post links) and only change if a forum post is amended.

## What lives where in generate_doc.py

- `FORUM_SKY` / `FORUM_DEMAND` — forum finals per prime per month.
- `SUPPLY_NOTE`, `SKY_NOTE`, `AR_NOTE_*`, `dr_links()`, `GAR_SHEET` and
  the inline section strings — all the prose (notes, ⚠️ warnings, the
  BA-Labs GAR figures, Pending items). Edit prose here.
- `collect()` — the fetch layer (reports, DR, Dune).
- `build()` — the document assembly (`##` prime / `###` category /
  `####` primitive headings; reconciliation tables computed from the
  components).

## Caching & offline

Remote fetches cache in `/tmp` with a 24h TTL and announce themselves on
stderr (`[cache] using … delete to force re-fetch`). To force a full
refresh: `rm -f /tmp/dr_* /tmp/gen_doc_*`.

`dr_aggregate.py` (the standalone DR aggregator, also used by
`generate_doc.py`) accepts `--local` to read the snapshot files
`dr_results.xlsx` / `dr_payouts.xlsx` in this folder instead of the
remote sources. ⚠️ The snapshots are frozen copies (last refreshed
2026-06-11) — remote and `--local` diverge once upstream changes.

## Standalone DR queries

```bash
python3 preparation/scripts/dr_aggregate.py --prime spark   # or grove/skybase/keel
python3 preparation/scripts/dr_aggregate.py --list-primes
```

## Rounding convention

Cells are exact values rounded half-even; totals are computed from
unrounded sums — summing displayed cells can differ by ±1 (noted in
doc.md itself).
