# Spark September 2026 capital-movement correction

PR: https://github.com/soterlabs/settlement-cycle/pull/219

## Causes and treatment

On September 6, Spark moved 80,894,745 sUSDS from its Arbitrum ALM into PSM3. The old savings-token calculation only captured mint/burn movements, so this ordinary transfer appeared as a loss. The corrected path captures all holder-net Transfers for non-rebasing L2 Sky savings tokens, reconciles them against opening/closing shares, and prices each day's movement at that day's PPS. Missing capture fails instead of manufacturing an end-of-period balancing movement.

[Migration transaction](https://arbiscan.io/tx/0x86956309b33b505cb5a3fcbec1960f98fd5112891dbda2cde82af1aa2e623b4d): archival RPC confirms the PSM3's sUSDS balance increased by 80,894,745 and the ALM's PSM3 shares increased by 86,359,174.134678689929043875 at block 502434535. The September 6 capital outflow is $89,688,228.433008654036879180 at PPS 1.108702777084081964. Capital remained in PSM3 custody.

On September 11, the Anchorage escrow returned 10,024,418.639471 USDC. The old calculation counted the whole receipt as revenue. The correction classifies 10,000,008.639471 as principal and preserves 24,410 as interest:

- [September 1 funding](https://etherscan.io/tx/0xd2961f325f3b00ed0a3b78ca8120d35fe352dbbcf06931291ea0f0b883ba184b): 10,000,008.639471 USDC.
- [Interest funding](https://etherscan.io/tx/0xbedf1256d84d57d80ddb4bc2d41122c9eb6bfa8e0ff496074c354b64cea45df3): 24,410 USDC.
- [Principal funding](https://etherscan.io/tx/0xd290228201f8ed52545fd88364618ace686323b40ef4847e06aabd8cee9c2a3c): 10,000,000 USDC.
- [Combined receipt](https://etherscan.io/tx/0x0a601288df4f132dec815aff107eed00a908a78e6e9753987bcd774fb96d393b): 10,024,418.639471 USDC.

The optional `capital_amount` override preserves existing full-return behavior. It only matches positive receipts from allowlisted external sources, on the configured day and token, within the existing $1 amount tolerance. Partial capital is bounded by the actual matched receipt; ambiguous matches fail.

## Review and tests

A review after PR creation found that duplicate input DataFrame index labels could cause a row assignment to affect unrelated transfers. Resetting the index before classification fixes it; a regression covers the case. Additional regressions cover actual receipt bounds, legacy full-return tolerance, transfer capture gaps, holder overrides, ordinary transfer directions, actual exit dates, and spread reimbursement.

- Full unit and monthly-compute integration suite: 1,183 passed, 7 skipped (optional dependency/workbook availability).
- Focused checks including worker/API with isolated PostgreSQL: 87 passed.
- New helper/test Ruff checks and `git diff --check` pass. Existing large files retain baseline lint findings.
- GitHub Actions did not start because the account payment/spending limit blocked execution.

## Publication checks

Publication results are recorded in the accompanying JSON. These checks compare externally served API results, rather than only local calculations. Revenue endpoints expose month-to-date estimates; daily changes are successive MTD differences.

Expected correction to every MTD cutoff:

- September 6–10: prime revenue +$89,688,228.433008654036879180.
- September 11–22: prime revenue +$79,688,219.793537654036879180.
- S43 accounts for the former amount; S26 removes $10,000,008.639471 from September 11 onward. Other allocations must remain numerically unchanged.

Spread reimbursement must stop on the actual September 6 exit. Relative to the old calculation, reimbursement decreases and Sky charges increase by $491.159472658158640325598218 per day from September 6. This is separately checked; it is not an unchanged-total-cost assertion. Published monthly settlement files are not regenerated.

Verified on September 23, 2026 at 18:56 UTC against the public API:

| UTC day | Corrected MTD prime revenue | Corrected daily change |
|---|---:|---:|
| September 6 | $1,574,040.19 | $191,005.70 |
| September 11 | $2,881,299.68 | $217,288.36 |

All 17 cutoffs from September 6–22 use corrected revision code `71fa912fe07cee5a417a8e17fd8071c1b5e85656`. September 1–5 retain their original revision IDs. All 17 superseded revisions were fetched explicitly and their complete results equal the saved pre-replay payloads. The latest endpoint selects the corrected September 22 revision and reports a succeeded attempt. No unexpected numerical changes were found in other allocations.

The one-off Railway replay finished successfully. The daily service's start command has been restored to `python -m settle.revenue.worker`, with schedule `17 20 * * *`. The service follows `fix/spark-september-capital-movements` until this PR is merged; after merging, reconnect it to `main` so future deployments follow the main branch. The API web service and other scheduled workers were not changed.
