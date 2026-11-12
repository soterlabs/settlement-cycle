# October 2026 replay with Reconciliation IV

**Reports rendered; no settlement executed.** Scoped replay executed on November 11.
October configuration and reports now include the historical replacement
adjustments. No API revision or payment was made.
The figures below preserve the verified ordinary October inputs and include
PR277's replacement historical adjustments once. Published months are untouched.

| Prime | Mint USDS | Send USDS |
|---|---:|---:|
| Spark | 14,048,690 | 4,188,099 |
| Grove | 8,866,792 | 2,275,969 |
| Obex | 1,463,217 | 205,907 |
| Osero | 15,812 | 42,396 |
| Keel | 0 | 34,034 |
| Skybase | 0 | 145,439 |

## Reproduction and scope

```sh
PYTHONPATH=src python scripts/replay_october_with_reconciliation.py \
  --output /tmp/october-with-reconciliation.json
```

The replay blocks network access, recomputes all 186 retained daily CoF rows
through production formulas, verifies the six original mint/send instructions,
replays Grove E19/E23/E43 exact cash income, replays the historical ledger and
then derives adjusted instructions through the production MSC formula. It is
not a fresh extraction/revaluation of every venue. Non-MSC and consolidated Sky
net revenue are outside this retained-component comparison.

Spark's +536,094.04 historical Sky adjustment replaces the old -255,869.48
credit. Its 75,063.88 ordinary October bridge CoF credit is **already reflected**
in the retained daily calculations and is not added again. All 31 October native
USDS wallet and bridge deductions match the independent retained observations.
Grove's existing credits and Osero's 5,514 send-only refund are included once.
Reporting-only issuer accrual from PR283 does not enter MSC income.

Full precision, source hashes and checks:
[with-historical-reconciliation.json](with-historical-reconciliation.json).

## Fresh ALM inventory scan

A new indexer capture covers all configured active ALMs, holder overrides,
SubProxies and Grove PAU/Basin custody. All 120,185 previously reviewed transfer
entries are present. Expanded custody adds 87 entries, for 120,272 entries total.
All retained review evidence and current prime configuration hashes match.
Full joined raw captures are retained locally at `/data/october-alm-rescan`;
[the committed inventory](alm-rescan-2026-11-11.json) records source hashes,
page completeness, wallet/block coverage and every additional transfer.
This capture does not replace independent native traces or daily valuations.

Two genuine USDC transfers from FalconX to Grove's SubProxy on October 16–17
total **1,484,740.589999 USDC** (10.001514 + 1,484,730.588485). RPC receipts
confirm direct USDC transfers; sender identity alone cannot establish whether
these are allocation income, returned principal or unrelated treasury funding.
The operator confirmed that SubProxy transfers are outside this ALM scan.
Their purpose is not a settlement blocker; no revenue adjustment is inferred. The exact
receipts are retained in [extra-custody-receipts.json](extra-custody-receipts.json).

The expanded capture also includes FalconX commingled Ethereum activity, which
must follow the documented EOA allocation boundary, and SubProxy treasury
movements. The operator excluded these additional custody entries from the required ALM
scan. All original reviewed ALM entries matched; the 87 additions do not block
settlement. No passing daily asset-control packet is implied.

## Daily controls deferred by the operator

The operator explicitly deferred independent daily asset controls. Empty
`asset-controls.json` placeholders now opt out for all six October primes;
populated files still validate, and missing files still fail. Each final report
will record `status: skipped`, not `passed`. The separate ALM reviews remain
required and their current evidence/configuration hashes were rechecked.
See [daily-controls-deferral.json](daily-controls-deferral.json).

The earlier retained replay's missing-packet blocker describes the pre-deferral
state. It no longer blocks report writing. All six reports have now been rendered from verified retained inputs using the
canonical writer. Each summary and workbook includes a separate MSC instruction
section; ordinary financial cells are unchanged apart from the generation
timestamp. The workbook includes an `MSC instructions` sheet. Provenance and
that sheet explicitly mark the daily controls as skipped, not independently
validated. See [finalization.json](finalization.json) and
[verification.json](verification.json).

To reproduce into a fresh staging directory:

```sh
PYTHONPATH=src python scripts/finalize_october_retained_close.py \
  --output-root /tmp/october-scoped-close
```

The `retained-close` package preserves the original input provenance, reports,
preview and configuration, with hashes. Only the historical components and
Grove cash-method comparisons are recalculated; unchanged positions are reused.

## June 20–21 idle correction

The updated #281 comparison adds 21,452.86 USDS of historical credit to Spark.
Reconciliation IV now supplies sky_adj +536,094.04. Spark's whole-token mint
falls from 14,070,143 to 14,048,690 USDS; its send remains 4,188,099.
The updated wallet evidence adds only the two June dates; every previously
retained wallet observation, including October, is unchanged. Only Spark's
report files were promoted from the scoped staging run. Other agents' report
files and all ordinary financial values remain unchanged.

## Stack after merging #281

The active stack is `main -> #277 -> #275`. PR #281 is merged into main as
`cc317e61bcbd86efc2c3a7a843a1ab992d3b222a`. The selective rebases preserve all
settlement, ledger, configuration and instruction bytes. Main's reporting API
update is inherited without changing the financial calculations.

The retained-source verifier now uses `retained-source-seal.json`, containing
the immutable original preview hash and source-tree blob identities. This
preserves the source-change restrictions in shallow CI checkouts, where the old
pre-rebase commit and `origin/main` ref may be absent. A replaced seal or an
unapproved financial source change still fails; no historical Git fetch is
required during offline replay. Seven focused tests passed, including actual
end-to-end report rendering, missing-ref simulation, seal tampering and unchanged
Osero refund semantics.

## Partner rebate replacement — 12 November 2026

Run `python scripts/calculate_october_partner_rebate.py` first. The committed
daily evidence produces Sky **1,246,289.50** and Grove **810,713.50**; all JTRSY
is attributed to Sky, including Spark custody. The executable recipe and its
assumptions are in `PARTNER_REBATE_RECIPE.md`. The October replay overlays these
amounts on the historical-only adjustment totals; it never changes the parent
Jan–September ledger or adds the receipts to venue income a second time.

The finalizer rendered all retained reports in staging to validate propagation;
only Grove's canonical reports were promoted. Other Prime files remain intact.
Grove mint remains 8,866,792 and send becomes 2,170,826 USDS.

## MSC #14 forum draft and review after Reconciliation IV merge

PR #277 is merged at `6482439`. Its final publication edits change no financial
ledger fields. The scoped October replay has been repeated against merged main.

`PYTHONPATH=src:scripts python scripts/build_october_forum_draft.py` writes
`forum_posts/msc-2026-10.md`. It reads the tracked Prime summaries and instruction
preview, verifies their hashes, and checks every adjustment against the merged
Reconciliation IV ledger plus Partner Rebate. The post separates ordinary
October revenue from historical adjustments, and displays only the agreed
**Partner Rebate** label and aggregate Sky/Grove amounts. Osero's refund is
carried once without increasing debt.

The October treasury close is documented in [treasury/README.md](treasury/README.md).
The forum builder also verifies the retained non-MSC, consolidated Sky and TMF
results and their source hashes. Figures remain proposed settlement instructions,
not a statement that payment or parameter execution has occurred.
