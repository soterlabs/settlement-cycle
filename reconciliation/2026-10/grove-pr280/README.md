# Grove-only October update after PR #280

Base: main `703c66863a0e99edf839658081026926f038c70b`.

The replay recomputes E19/E23/E43 exact ERC4626 cash capital using production
components and the retained, pinned raw logs/RPC responses in
`reconciliation/grove-base-cash-2026/inputs.json`. It also retires E12's stale
notional. All other October inputs are reconstructed from the previously
verified financial snapshot, with lossless financial-field round-trip checks.
This is a scoped replay, not a new full-chain extraction of all venues.

| Headline | Before | After |
|---|---:|---:|
| Grove ordinary October net | 1,340,492.57 | 1,348,557.53 |
| Grove borrowing cost | 4,435,995.50 | 4,435,995.50 |
| MSC mint | 8,879,062 | 8,887,127 |
| MSC send | 2,256,350 | 2,264,415 |

Income rises **8,064.96 USDS**. Whole-USDS payment rounding increases mint/send
by 8,065 each. Existing approved partner adjustments remain unchanged; January–
September changes belong to PR #277 and are not newly added to this October close.

`before-summary.md` and `before-workbook.xlsx` are the prior PR #275 Grove
artifacts. Their hashes match `before-preview.json`; `before-provenance.json`
reproduces their ordinary MSC totals. `after-provenance.json` and `replay.json`
record the fresh results, source/review hashes and derived Grove instructions.
The canonical summary/workbook are under `settlements/grove/2026-10/`.

The complete local October review was revalidated against the updated Grove
configuration; the original 58 groups, 1,854 transfers, all wallets/chains and
balance/NFT controls remain covered. Exact Base cash controls supplement it.
Review hash: `ae0e69cf6e097ecc66b36c07f705d4199d67c01ecb36a7dff16ff4fdd417c5c6`.
Raw review evidence remains local. The script refuses an absent/stale review.

After completing that review, run from repository root:

```sh
PYTHONPATH=src python scripts/replay_october_grove_pr280.py --output-dir /tmp/grove-october-pr280-fresh
```

The script blocks network access while replaying retained RPC/log inputs,
validates full daily borrowing costs and the final DR snapshot, and preserves
the normal output overwrite guard. A second fresh replay reproduced all
financial/provenance fields, summary text and workbook cells; only generation
timestamps differed. Tests independently compare Grove payment instructions
against the production MSC formula. Non-MSC inputs are isolated only in that
unit test; the actual replay calculates no unrelated prime/non-MSC leg.

Spark, Obex, Osero, Keel and Skybase reports and preview rows were verified
unchanged from the previous remote #275 head `0277d12`.
