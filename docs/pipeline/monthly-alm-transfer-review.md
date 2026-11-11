# Monthly ALM transfer review

Required preparation for a new monthly MSC settlement. This is an agent/operator
workflow rule (see `AGENTS.md`), not a newly enforced gate in the cron/API worker.

A position-level balance or debt reconciliation cannot detect missing venues or
cash incentives classified as capital. September 2026 Spark illustrates both:
Sentora and spRLUSD receipt positions were absent from MSC, and six issuer
RLUSD payments totaling 1,373,208 were not configured as revenue.

## Inventory and review

1. Pin opening and closing blocks for the month on every configured ALM chain.
   Include primary/secondary ALMs and configured beneficial custody boundaries.
   Inspect configured sources, not just the tokens already listed as venues.
2. Scan all token transfers into **and out of** those wallets over the pinned
   period. Include receipt tokens and NFT positions. Capture the transaction's
   deposit, withdrawal, mint, burn, swap and bridge events when needed to match
   the economic legs. Record zero-activity chains explicitly; provider failures
   or missing log ranges are not zero activity.
3. Group by `(chain, wallet, token, direction, counterparty)` and review each
   group. Preserve the transaction/log identifiers behind each total. Map each
   transfer to an existing/new venue and capital movement, earned income,
   expense, debt movement, internal/bridge movement, or a documented non-MSC
   treatment. New tokens/payers cannot inherit a default capital/zero-yield
   label merely because the existing pipeline ignores them. Any overrides need
   explicit evidence; no balancing plugs.
4. Check underlying and receipt legs together. A vault deposit is not an
   expense; a reserve-treasury receipt is not a new borrowing. aTokens can emit
   Mint on withdrawal; use their scaled accounting. Existing approved EOA
   boundary rules apply without downstream tracing.
5. Reconcile token balances against net transfers where appropriate; reconcile
   rebasing/scaled shares and NFT ownership with their actual mechanics. Check
   cash-source routing against accrual/receivable rows to prevent double booking.
6. Resolve every unclassified transfer/group. Missing income must be added to
   the correct cash/accrual route, not hidden behind a generic exception. Keep
   genuinely approved non-MSC classifications visible with their justification.

## Local review record

Save raw evidence, grouped classifications and the review record under
`.msc-transfer-reviews/<prime>/<YYYY-MM>/` (gitignored). The record must contain:

- Prime, UTC period, reviewer and review time.
- All chains/wallets and pinned block ranges; explicit complete coverage evidence.
- SHA-256 of the prime configuration and every raw scan/classification artifact.
- Transfer/log counts and a link from each raw transfer to its reviewed group.
- Per-group classification, allocation/account reference, amount and rationale;
  all exclusion decisions must be explicit.
- Results of the applicable opening/closing balance and receipt controls.
- `unclassified_count: 0` and `status: "reviewed"` only after the above passes.

A config change, changed pin, newly discovered wallet, changed evidence or
classification invalidates the record until reviewed again. This is an
attestation of actual review, not a lock used for mutual exclusion. Do not copy
another month's record or issue a passing record from only a partial-token scan.

No September Spark passing review is supplied in this PR: the focused RLUSD
investigation alone does not establish all-token/all-chain coverage. The PR
changes configuration for future calculations; it neither regenerates published
months nor publishes corrected API or settlement amounts.

## Daily independent asset controls

Canonical settlement writes also require the [daily asset control packet](settlement-asset-controls.md).
It binds the reviewed inventory and source artifacts to independent daily
holdings, all allocator ilks, the seven CoF deductions, bridge claims and the
asset/debt roll-forward. The writer rejects missing or failed evidence before
writing artifacts, including when overwrite was explicitly requested. The
native-USDS capture tool does not by itself establish complete token coverage
or issue a passing review.
