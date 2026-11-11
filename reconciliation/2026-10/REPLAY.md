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
