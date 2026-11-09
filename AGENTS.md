# Monthly MSC preparation

Before calculating a prime's monthly MSC settlement, follow
[the ALM transfer review](docs/pipeline/monthly-alm-transfer-review.md).
This is required even when the configured venues reconcile individually.

Do not start a new monthly settlement calculation without a current local
`.msc-transfer-reviews/<prime>/<YYYY-MM>/review.json` recording a complete,
reviewed ALM transfer inventory. A filename or zero residual alone is not proof:
check its evidence hashes, config hash, chain/wallet coverage and reviewed
transfer classifications. Never create a passing record for an incomplete scan.

Classify both cash and receipt-token movements, including issuers' incentive
payments. Unexpected tokens, destinations or payers must be resolved and mapped
to a venue/accounting treatment before completing the review. For EOA allocation
entrypoints, apply the approved boundary policy; downstream commingled funds do
not need to be traced.

Research, partial scans, tests and read-only comparisons may continue without a
passing review. Do not change previously published settlements or publish API
revisions merely because the scan found omissions; obtain the user's authorized
scope for any restatement or publication. Existing historical-output protections
continue to apply.
