# October 2026 replay with Reconciliation IV

**Draft; not a finalized settlement.** Replay executed on November 11. No API
revision, canonical workbook, settlement configuration or payment was changed.
The figures below preserve the verified ordinary October inputs and include
PR277's replacement historical adjustments once. Published months are untouched.

| Prime | Mint USDS | Send USDS |
|---|---:|---:|
| Spark | 14,070,143 | 4,188,099 |
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

Spark's +557,546.90 historical Sky adjustment replaces the old -255,869.48
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
state. It no longer blocks report writing. Final reports still need to be
rendered from the approved financial inputs; the earlier workbooks have not
been silently relabelled as independently validated.
