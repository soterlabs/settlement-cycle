# Grove October 2026 recalculation

Prepared against main `9b290a3` (including the merged Grove accounting fixes and
bridge-transit idle USDS exemption). PR #261 was independently rebased onto this
main commit; its new head is `372b368`.

This dedicated change prepares October inputs and the Grove-only runner. It does
not contain freshly calculated settlement amounts yet. Existing estimates and
prior audit booking snapshots must not be presented as results from latest main.
No Spark or other prime is calculated, and no API output is published.

## Inputs

- `settle-dr-dune`: `d53dd1317bd8f52f22a3400aebcb5a095689a812`, merged PR #34.
- October DR CSV and ref-code rollups: 210 rows, all 32 shards complete.
  Grove DR: **$22,572.84943285862405046758602**.
- The October-only DR workbook is derived from the published rollup because the
  upstream comparison workbook ends in September. The manifest documents its
  source and hashes. It is not represented as an upstream October workbook.
- Official October SOFR snapshot: full period coverage, supplied explicitly to
  avoid carrying the repository's older last-known rate through October.
- Approved partner adjustments remain sourced from the merged configuration;
  the partner receipts must not also be added as venue interest.

## Required transfer review

`AGENTS.md` and `docs/pipeline/monthly-alm-transfer-review.md` require a complete,
current reviewed inventory before starting the monthly calculation. The local
`.msc-transfer-reviews/grove/2026-10/review.json` is deliberately incomplete.
All 30 inherited evidence artifacts passed SHA-256 verification. Outstanding:

1. Refresh grouped transfer classifications against current configuration,
   including E43/E44 and the approved E36 boundary policy.
2. Extend the prior primary/secondary ALM coverage to Basin beneficial custody
   and escrow boundaries, with appropriate share and balance controls.
3. Resolve the purpose and accounting treatment of **76,133.73 AVAX** received
   October 9 from `0x9f1d8d40d9d8e6b69e0d9aa66b51c3007adc1c2f`, transaction
   `0x79abefd7a236396bfbbff35bb09911173e617f0124d86c78a93593703efd93d2`.
   Operator clarification has been requested; no income/principal/non-MSC
   classification has been assumed.

After the review passes, run `scripts/run_october_grove_close.py` with normal
credentials. It calculates only Grove October 2026 and writes the summary and
workbook under `settlements/grove/2026-10/`. Existing output protections apply.
`--check-inputs` validates the reference-rate and DR inputs without calculating.

## Validation

Input preflight passed. The DR mapping/close and reference-rate/disclosure unit
tests passed: **53 tests**. No fresh monthly calculation has been started.
