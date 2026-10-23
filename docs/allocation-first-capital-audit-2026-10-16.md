# Allocation-by-allocation capital audit: first two facilities

PR #215 now has an independent, transaction-level funding-origin audit. It
starts with Grove E42 (Galaxy Warehouse) and Spark S23 (Anchorage). The pinned
history ends August 31, 2026; this is not a September or full-year result.
Pre-2026 flows establish opening capital only. No settlement or API changes.

## Results

| USD / USDS-equivalent | Grove E42 | Spark S23 |
|---|---:|---:|
| Historical deposits through August | 304,000,001.00 | 290,404,455.98 |
| Classified principal returned | 0.00 | 81,575,229.29 |
| Closing cash-principal claim | **304,000,001.00** | **208,829,226.69** |
| Direct draw origin remaining after conservatively releasing returns | **303,957,394.58** | **148,274,582.04** |
| Closing origin needing more tracing | 42,606.42 | 60,554,644.66 |
| Outstanding debt floor after assigning all subsequent same-ilk repayments here first | **141,686,399.55** | **0.00** |

These are different questions. We can establish where original funding came
from without proving which surviving investments should carry debt after a
repayment made using other cash. The debt floor is deliberately conservative,
not a claim that Anchorage is debt-free. Exact current borrowing costs still
require the funding/refinancing attribution policy.

The cash-principal claims are equivalent in size to 11.25% of Grove's and 7.09%
of Spark's August-end total ilk debt, respectively. Those ratios prioritize the
work; they are **not validated debt coverage**. The independently bounded Grove
floor is about 5.24% of Grove's combined debt (5.27% of BLOOM-A). It is conditional
on complete and correctly classified normalized movements, not a fresh RPC audit.

## Galaxy Warehouse: original funding nearly completely identified

Nine observed USDC deposits sum to 304,000,001. Eight have a direct, same-batch
ilk draw; the July 21 one-dollar test used existing cash. July 21's large deposit
also consumes 42,605.42118 of existing ALM USDC. Together those two existing-cash
components explain the entire 42,606.42118 source-attribution remainder.

Examples:

- [July 21 mixed funding](https://etherscan.io/tx/0x8181996e100fba29940b9b9f4d14d213b50a107a78ccde05c3fd0be2d87f0f9d):
  47,457,393.57882 newly borrowed + 42,605.42118 existing USDC = 47,499,999 deposited.
- [July 22 direct funding](https://etherscan.io/tx/0x80cd51c4d960a1cee1f0bec1b7d90c121ce72c8f55c6aa13f76d0fc185bf56d2):
  47,500,000 newly borrowed and deposited.

The saved USDC logs independently match all nine normalized deposit amounts,
token, sender and recipient. We stop at the configured Galaxy entrypoint; its
onward wallet activity is irrelevant to this audit.

The old global replay assigns only 303,240,986.99 of borrowed basis at August
end, despite no Warehouse principal return. This is not evidence of missing
deposits: that ledger can proportionally refinance all remaining accounts when
another source of cash repays the ilk. Do not replace the proven 304m facility
claim with that modeled borrowed-basis number, or treat their difference as a
missing transfer.

## Anchorage: published principal treatment retained

This audit uses the existing MSC classification accepted by the operator:
the full July 16 receipt of 10,036,438 and August 17 receipt of 51,267,944 are
principal. It also applies the reviewed July 21 duplicate-disbursement refund.
The older 10m/50m diagnostic scenario is not used. This is an accepted accounting
classification, not a new claim about independently verified loan terms.

Direct draws linked to deposits total 229,849,811.32 historically. After charging
all classified returns against that funding first, 148,274,582.04 remains.
Mixed transactions are treated conservatively: all other destinations may use
the draw first, and only the unavoidable remainder is linked to Anchorage.
This avoids counting a reallocation as another fresh draw.

The JSON lists every deposit and return, including the source-attribution queue.
The largest unlinked deposits include the two 5m payments on May 10 and May 20;
several December 2025 deposits also need source tracing to establish 2026's
opening position. None is assigned borrowed funding merely to close a gap.

## Next largest allocations, by saved modeled August-end borrowed basis

These are prioritization estimates, not newly certified results. Spark's saved
ranking comes from the old 10m/50m scenario and must be refreshed before an
exact coverage claim. No historical financing JSON was overwritten.

| Prime | Allocation | Modeled closing borrowed basis |
|---|---|---:|
| Grove | E9 — JTRSY | 825.10m |
| Grove | E10 — BUIDL | 643.59m |
| Grove | E20 — JAAA, Avalanche | 244.76m |
| Spark | S1 — SparkLend spUSDS | 640.83m |
| Spark | S32 — sUSDS / POL | 538.16m |
| Spark | S34 — Morpho USDC, Base | 275.10m |

Grove E9/E10 and Spark S1/S32 offer the largest next increments. Their evidence
must handle subscriptions/redemptions or vault shares, valuation gains and
reallocations. The facility audit intentionally rejects market-value accounts;
it must not be applied to share-price growth as though it were a cash deposit.

## Reproduction and safeguards

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_facility_funding.py \
  --history tests/fixtures/grove_warehouse_funding_history.jsonl.gz \
  --venue E42 --start 2026-01-01 --end 2026-08-31 --output /tmp/warehouse-audit.json

PYTHONPATH=src .venv/bin/python scripts/audit_facility_funding.py \
  --history /tmp/pr215-spark-v1-history.jsonl.gz \
  --venue S23 --anchorage-published-principal \
  --start 2026-01-01 --end 2026-08-31 --output /tmp/anchorage-audit.json
```

The compact Grove fixture retains every facility movement and every negative
per-ilk draw from the full snapshot. It is sufficient for this audit only, not
for a general capital replay. Spark currently requires the saved full snapshot;
its hash and the applied adapter hashes are recorded in the output.

Regression tests cover mixed sources, competing destinations, gains, debt
rounding, duplicate or inconsistent movements, principal returns, reallocations,
repayments outside the venue and repayments on another ilk. Actual Warehouse
deposits are checked against saved Transfer logs.

Artifacts: `reconciliation/grove_warehouse_funding_origins_2026_08.json` and
`reconciliation/spark_anchorage_funding_origins_2026_08.json`. Daily rows retain
the facility claim, surviving direct-origin amount and per-ilk outstanding debt
floor separately. These diagnostics never promote an entire allocation to the
production eligible-cost subtotal.
