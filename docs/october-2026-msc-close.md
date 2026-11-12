> **Current replay:** October reports have been rendered with Reconciliation IV adjustments. Independent daily checks are explicitly skipped by operator instruction; ALM reviews remain required. See [replay details](../reconciliation/2026-10/REPLAY.md).

# October 2026 MSC — all prime agents

PR #275 adds the October summaries and canonical settlement workbooks for Spark,
Obex, Osero, Keel and Skybase alongside Grove. The branch is rebased onto main `703c668`, including PR #280. Only Grove was
replayed for this update; the other five agents retain their verified reports
and figures from `bda6858`. Grove ordinary income increases by **8,064.96 USDS**;
its borrowing costs, DR and approved settlement adjustments are unchanged.

The runs use credentials from the supplied local `.env` without copying them
into the repository. They calculate October 2026 only. The official October
SOFR snapshot is passed explicitly to subsidized borrowing calculations, and
the finalized October DR snapshot remains pinned to settle-dr-dune PR #34
(`d53dd13`). No earlier settlement or API output is published or restated.

## Settlement results

Ordinary net includes supply-side revenue after borrowing costs plus agent rate,
DR and other active demand-side components. Historical adjustments are separate.

| Prime | Ordinary net (USDS) | October DR | Sky borrowing charge | Sky SDE |
|---|---:|---:|---:|---:|
| Spark | 3,938,957.75 | 739,155.52 | 10,215,829.41 | 8,887.41 |
| Grove | 1,348,557.53 | 22,572.85 | 4,435,995.50 | 1,246,963.82 |
| Obex | 479,415.46 | 0.00 | 1,341,558.34 | 0.00 |
| Osero | 36,881.63 | 1,228.31 | 13,463.65 | 0.00 |
| Keel | 34,033.81 | 0.00 | 0.00 | 0.00 |
| Skybase | 145,438.86 | 102,991.76 | 0.00 | 0.00 |

### Derived prime-level MSC instructions

| Prime | Supply adjustment | Sky adjustment | Send credit | Mint (USDS) | Send (USDS) |
|---|---:|---:|---:|---:|---:|
| Spark | 249,141.52 | 536,094.04 | 0.00 | 14,048,690 | 4,188,099 |
| Grove | 822,268.24 | 1,135,869.94 | 0.00 | 8,866,792 | 2,170,826 |
| Obex | -273,508.90 | 0.00 | 0.00 | 1,463,217 | 205,907 |
| Osero | 0.00 | 0.00 | 5,514.00 | 15,812 | 42,396 |
| Keel | 0.00 | 0.00 | 0.00 | 0 | 34,034 |
| Skybase | 0.00 | 0.00 | 0.00 | 0 | 145,439 |

These instructions include Reconciliation IV once, replacing earlier adjustment
totals. Grove retains the approved 810,713.50 supply and 1,246,289.50 Sky partner
rebates, plus historical supply +11,554.74 and Sky -110,419.56. Spark carries
historical supply +249,141.52 and Sky +536,094.04; Obex supply is -273,508.90.
The original October operating revenue and daily borrowing costs are unchanged.

Osero also receives the existing **5,514 USDS send-only credit** for duplicate
July–September shortfall deductions. It increases the send to 42,396 USDS
after rounding and creates no additional debt; ordinary October net remains
36,881.63 USDS. Its existing reconciliation is preserved.

The October formula finances an ilk prime's ordinary supply-side shortfall
before applying adjustments; it does not subtract that shortfall again from
demand-side payment. Keel/Skybase have no allocator ilk and mint no debt.
These are **prime legs**, excluding DSB/Core Council/non-MSC flows; this is not
a consolidated Sky Net Revenue calculation or an assertion of execution.

Full-precision inputs, derived amounts and canonical report hashes:
[prime_msc_preview.json](../reconciliation/2026-10/prime_msc_preview.json).

Each prime's summary and canonical workbook are under
`settlements/<prime>/2026-10/`. Existing reports for earlier months are untouched.

## Inputs found by the October review

- **Osero O3:** Osero x Gauntlet USDC Prime,
  `0x802148d518a6de2af866f9a61ffb5e5c39156db2`. On-chain `asset()` is USDC,
  shares have 18 decimals, opening shares are zero, deposits total 6,000,000
  USDC, and redemptions return 1,000,065.119301 USDC. The closing holding is
  about 5.01 million USDC. It is now included in MSC venue revenue.
- **Spark NFTs:** three October mints were absent from the V4 inventory.
  ID **460903** belongs to RLUSD/USDS (S66); IDs **485852** and **489097**
  belong to USDT/USDS (S62). The primary Spark ALM owns all three at closing.
  Their opening owner queries explicitly report `NOT_MINTED`; this is
  distinguished from a missing/failed provider read. Their pool keys are
  matched to the configured pools, and they are added to those inventories.
- **Maple queue upgrade:** Spark S14/S15 and Obex V1 now also recognize
  implementation `0x9dafee2bfc8eeb46c4fa80c998bc0c7e4804c644`. The old
  implementation remains allowed. The new runtime exactly matches the
  [verified source](https://sourcify.dev/server/v2/contract/1/0x9dafee2bfc8eeb46c4fa80c998bc0c7e4804c644?fields=all).
  Owner-specific queued/manual share getters, storage slots and pool identities
  were reviewed; the existing ownership formula remains unchanged. The checks
  still reject other unreviewed implementations.

Pinned RPC reads, NFT pool keys, source/runtime hashes and review manifests are
in [additional_prime_inventory.json](../reconciliation/2026-10/additional_prime_inventory.json).
Reports use current main valuation methods with the S32 holder-transfer fix
documented below. Independent cash
and `convertToAssets` diagnostics are not substituted as balancing adjustments;
Grove now uses PR #280's exact cash capital; its remaining endpoint valuation diagnostics are documented in its close note.

## Transfer coverage

Each prime has a reviewed local inventory under
`.msc-transfer-reviews/<prime>/2026-10/`, with pinned ranges, config/evidence
hashes, grouped classifications and balance/ownership controls. Those raw
captures are intentionally local; compact review hashes are committed above.

| Prime | Reviewed groups | Transfer entries | Coverage |
|---|---:|---:|---|
| Spark | 1,521 | 118,304 | Seven chains; primary ALMs, Anchorage escrow and Savings V2 custody |
| Grove | 58 | 1,854 | Five chains plus Diamond ALM and Basin custody; existing completed review |
| Obex | 0 | 0 | Complete zero-activity October ALM scan; share balances agree |
| Osero | 12 | 24 | Primary ALM, SparkLend and newly identified USDC vault |
| Keel | 1 | 1 | No ALM/venues; subproxy treasury receipts and balances reviewed |
| Skybase | 2 | 2 | No ALM/venues; subproxy treasury receipts and balances reviewed |

Spark's 69 token/scaled balance controls and two Curve LP controls pass. Twenty
V4 position IDs have pinned ownership/pool reads. One 10.000005 USDC self-transfer
is explicitly netted as both a debit and a credit to the same wallet. All 5,387
CCTP USDC mints match authenticated on-chain message bodies with the actual
recipient, minted amount less fee, and a source Spark ALM. They are internal
bridge movements, not issuer income. Open bridge claims are retained by the
normal bridge adapters.

Specific counterfeit/unsolicited transfers are excluded with contract metadata
and transaction-sender evidence. Nonstable WETH Savings routing stays outside
MSC under the operator's stablecoin scope. The configured retail Savings,
USDG and loan-accrual reporting-only treatments remain explicit; no unknown
receipt is silently called zero-yield capital.

October earned cash includes Spark's 1,588,290 RLUSD issuer receipts,
1,430,849.95 PYUSD rewards and 967,807 USDC Anchorage interest. Approved
reserve-treasury spToken receipts are external earned income, separate from
supplier-index interest, and are not new capital. Reporting-only incentive or
loan accrual is not added again to those actual cash receipts.

Keel's DR retirement and Skybase's GAR retirement remain effective. Skybase
still receives its October DR under the current referral-code attribution.

## Validation

- **243 tests passed** across async ownership/issuer redemptions, DR, reference
  rates, Basin deductions, V4 pricing, configuration, venue views, MSC accrual,
  incentive cutoff isolation, holder-share capital controls, issuer summary
  rendering and the new review-integrity guard.
- The runner validates the full selected batch's rates, DR source, local review
  integrity and destination protections before calculating its first prime.
- Each run independently recomputes the canonical daily borrowing-cost formula
  and requires successful canonical workbook generation.

Spark's reporting-only incentive schedules replay history from November 2024.
Their NFT reader now fetches one complete history at the report pin and caches
it within the run. Every daily ownership result still filters at that day's
block, including transfers out and re-entry; the cutoff-isolation test queries
days out of order. These schedules do not alter MSC cash revenue.

The S32 capital review also identifies **100,000,000 sUSDS shares** transferred
from the Spark ALM to its configured SubProxy on **2026-10-29**, block
26298859, transaction `0x8075abed11525daa47909008d54964e3656bf771bfc0d48f329d1a886abeb8b5`.
Ethereum Sky-savings capital now uses the same holder-net Transfer method as
L2 savings shares. It includes ordinary transfers alongside mints/burns, prices
each day at that day's PPS, and fails if total shares disagree with either
archive boundary. This prevents a capital return from becoming a synthetic
loss; the daily depositor-SSR deduction remains separate.

The summary renderer also supports the merged issuer-replay cash schema:
only current-period receipts are shown, with their transaction/log identity.
It preserves the legacy fee-variance table and infers no carrying value from
an issuer payment row.

The independent Spark control prices the 100M shares at October 29's pinned
archive `convertToAssets` quote and confirms a **111,454,244.5695909906 USDS**
capital outflow. Every other venue's revenue and boundary values, all 31 Sky
borrowing rows, agent rate, DR and reporting-only accruals are unchanged.
See [spark_susds_capital_control.json](../reconciliation/2026-10/spark_susds_capital_control.json).

Current-method display diagnostics remain visible: Spark S26 is a cash-routing
venue whose time-weighted value clamps to zero; its actual 967,807 USDC
Anchorage interest remains revenue. October 31 is a Saturday, so the official
SOFR series carries October 30's print through the weekend. Neither diagnostic
adds a financial adjustment.

All six reports pass independent DR reconciliation, venue/SDE rollups, full
31-day borrowing-cost coverage, workbook headline checks and saved-output
protection checks. The Grove-only PR #280 replay reproduces all financial values in a second fresh output directory; the other five reports remain byte-for-byte unchanged.

## Reproducing the close

Complete the local transfer review required by `AGENTS.md` first; the compact
committed hashes do not replace a complete raw scan and classification review.
With the normal credentials loaded and dependencies installed, use a fresh
output root so existing settlement artifacts remain protected:

```sh
HYPERSYNC_CHECKPOINT_BLOCKS=20000000 python scripts/run_october_close.py \
  --primes spark,grove,obex,osero,keel,skybase \
  --output-root /tmp/msc-october-2026
```

The larger checkpoint batch only speeds first-time historical extraction;
block pins, completeness checks and accounting are unchanged. The normal input
cache can reuse actual block headers and archive balance responses captured
during the review; no dates or balances are inferred to warm it.

## Latest Grove-only replay after PR #280

The full local Grove ALM review was revalidated against the new configuration.
The replay reconstructs the verified prior October snapshot, recomputes only
E19/E23/E43 and E12 using pinned retained on-chain inputs, and independently
validates the unchanged daily borrowing formula and DR. It does not re-extract
or recalculate the other venues or agents. Baseline reports, before/after
provenance and exact changes are retained in
[grove-pr280](../reconciliation/2026-10/grove-pr280/README.md).

For this scoped update, after completing the local review, run:

```sh
PYTHONPATH=src python scripts/replay_october_grove_pr280.py --output-dir /tmp/grove-october-pr280-fresh
```

## Rebase and retained replay after #282

Rebased onto main `c559a19` after #282 merged. No rate/revenue formula or prime
configuration changed relative to this PR's prior head `6820525`; #265 adds
reporting and tightens RPC cutoff validation, and #282 adds settlement controls.
The replay verifies that production/config changes stay inside that scope.

The actual read-only replay recomputed all 186 October borrowing rows from the
retained workbooks using the production borrowing function, and all six
mint/send instructions using the production MSC function. Every instruction is
unchanged. Workbook floating-point differences stay below one cent. Grove's
20 active Base venue-month component replay also reproduces the already-booked
October +8,064.9552899653683474748 income-method change; it is not added again.

Spark's ALM idle also matches all 155 retained archive wallet observations across
31 days and five native-USDS chains. Existing gateway matching independently
reproduces all 31 native-USDS bridge deductions. The wallet observations are a
hash-bound, reviewed input snapshot from PR #281; its historical financial
correction remains separate and is not booked here.

See `reconciliation/2026-10/post-pr282-retained-replay.json` and
`post-pr282-grove-cash.json` for source/code hashes, deviations and results.
Reproduce the arithmetic without network access:

```sh
PYTHONPATH=src python scripts/verify_october_retained_after_pr282.py \
  --output /tmp/october-retained-replay.json
```

To repeat the additional native-USDS check, extract
`reconciliation/usds-bridge-2026/baseline-correction/wallet-inputs.json` from
commit `f526b0bdc1d60d37a5f30a807c51a19f2f183ac0`, then pass
`--spark-wallet-inputs /path/to/wallet-inputs.json`. The script requires the
exact reviewed SHA-256 and configured wallet/token identities.

**Historical pre-deferral checkpoint:** at that time none of the six complete
`asset-controls.json` packets is present. Complete independent position/debt
observations, inventory and source-backed journals still need capture/review.
Only Grove's older transfer review remains locally available; it does not meet
the new whole-portfolio packet contract. The retained arithmetic replay is not
an asset-control attestation, a source re-extraction, a new complete monthly
calculation, or a consolidated non-MSC calculation. Existing canonical summary
and workbook bytes are preserved. No control gate was bypassed for production
and no passing review was fabricated.


The post-rebase compatibility checks preserve closed historical source sets:
Maple's zero-wallet queue test exercises a cloned pre-extension allowlist,
September's frozen allocation rows reproduce while newly discovered O3 remains
explicitly outside that older normalized capture, and the legacy bridge audit
uses its recorded workbook set plus retained pre-policy October daily rows.
It must not substitute this PR's already-exempt October workbook into the old
baseline or issue another refund for it. These checks do not apply #281's
separate historical financial correction.

A new read-only complete ERC20 holder-transfer scan from block zero through
October close independently finds O3's first share transfer on October 12,
2026 at 20:08:35 UTC, and no O3 share transfers through September. The index was
more than 500 blocks beyond the requested endpoint. Raw selections, complete
rows and coverage are retained in
`reconciliation/2026-10/post-pr282-osero-o3-holder-history.json`. This focused
scan does not create a passing full-prime transfer review or asset packet.
