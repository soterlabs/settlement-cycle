# Capital custody corrections: August 2026 reconciliation

Completed September 23, 2026 for PR #215. These are **partial allocation-cost subtotals reported by the current tracer**, not a fully validated attribution of all allocation costs. A remaining principal-return classification bug can also affect these subtotals. Unresolved allocations retain unavailable cost/net APY. The unexplained difference is not a measured economic loss.

| Prime | Previous allocation costs | Current allocation costs | Global costs excluding MSC | Share of adjusted target |
|---|---:|---:|---:|---:|
| Spark | 10,063.49 | 12,080.10 | 5,711,428.50 | 0.21% |
| Grove | 6,304.10 | 6,304.10 | 3,460,726.73 | 0.18% |

Spark's reported improvement is **$2,016.61**, almost entirely in S18 ($10,063.49 → $12,080.10). No previously unresolved August allocation becomes fully traceable: Spark still has 54 unresolved allocations and Grove 24. Matching more transfers does not remove every missing link in an allocation's funding history, so large event-count improvements need not produce a comparable increase in available cost attribution.

Neither prime fully reconciles. The unchanged global charges are **$6,108,910.34 for Spark** and **$3,720,604.84 for Grove**. Diagnostic MSC exclusions remain **$397,481.84** and **$259,878.12**, respectively, including outstanding prior-month MSC debt. These exclusions do not change settlement charges.

Grove's comparison combines ALLOCATOR-BLOOM-A and ALLOCATOR-GROVE-A. It does not establish separate allocation-cost attribution for each ilk. Spark covers ALLOCATOR-SPARK-A.

| Prime | Unmatched receipts: before → after | Unmatched outflows: before → after | Unresolved allocations |
|---|---:|---:|---:|
| Spark | 259,849 → 43,020 | 52,182 → 25,356 | 54 |
| Grove | 296 → 295 | 529 → 529 | 24 |

## Corrections verified in this replay

- **CCTP v1:** match each USDC payment to the messenger's historical `localMinter()`, consuming each payment once. The old code looked for payment to the messenger itself and compared the transaction's total payment against each individual burn. **44,874 sends match 44,874 receipts**, with no unmatched message in this verified source set. A receive transaction can immediately invest all of its cash. [Example Ethereum send](https://etherscan.io/tx/0x5332b54a1b545921fb625dd526cd5be2a4ead0cffd8e635f925e18d90415ff63) and [Base investment](https://basescan.org/tx/0xb109977fca86a06cd38ae9df0112ca273afde6f81a917f5c04112ef46eaf5d84). [Circle source](https://github.com/circlefin/evm-cctp-contracts/blob/a92a2b4e7e6ef99bf0b05dca71780f5ec190e729/src/TokenMessenger.sol).
- **Ethena cooldowns:** preserve principal in the ALM's claim on silo-held USDe between the sUSDe burn and release. **15 transactions** cover eight starts and seven releases. Reconstructed pending amounts match historical `cooldowns(holder)` state. The August 5/12, 2025 sequence releases 714,205,521.594844350131593641 USDe. [Strategy announcement](https://forum.skyeco.com/t/spark-liquidity-layer-configuration-and-strategy/25860/140), [redeployment update](https://forum.skyeco.com/t/spark-liquidity-layer-configuration-and-strategy/25860/144), [contract source](https://github.com/ethena-labs/bbp-public-assets/blob/f3e56d5f06bfef82367d5d5b561398e91d5bebc1/contracts/contracts/StakedUSDeV2.sol).
- **Base Morpho fees:** classify **246,032 performance-fee mint transactions** as own funds, without creating borrowed principal. Match each mint to its exact `AccrueInterest` fee-share amount. Those unindexed accrual events were absent from the ALM-topic query. Mixed deposit/fee transactions preserve actual deposit funding separately. This adapter is limited to the reviewed S34 vault. [Example fee mint](https://basescan.org/tx/0x5f2cc01503367db6dd55ff4a2c2b4fa5e561053dfd4182cdbc6c07399eff5efa), [MetaMorpho source](https://github.com/morpho-org/metamorpho/blob/ded84e59668155b34d3c24906c4f7461c12828af/src/MetaMorpho.sol).
- **Grove's forwarded BUIDL interest:** the September 4, 2025 payload, executed September 8, forwards **900,612.89 BUIDL** that the issuer mistakenly paid to Spark. The exact transaction/block/account/amount correction classifies it as income with no borrowing; it removes one unmatched receipt. Grove's reported August cost subtotal remains unchanged because other funding gaps remain. [Forum rationale](https://forum.skyeco.com/t/september-4-2025-proposed-changes-to-spark-for-upcoming-spell/27102/1), [exact payload](https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20250904/SparkEthereum_20250904.sol), [execution](https://etherscan.io/tx/0x0032e26b8e4b284e3c61ea8aeb0870e3f0dbb7d3173945faf0449ca6ec5138e8).

## Replay and validation

- **451,902 Spark normalized transactions** replay from inception through the published August pins. The existing executed-spell correction adds one isolated purchase batch during compute. Grove replays 2,259 normalized transactions.
- Every raw debt event, ilk attribution, transaction identity and timestamp is unchanged. Both published provenance files retain their hashes. Global charges, booked settlement revenue and settlement amounts are unchanged.
- **1,252 unit/monthly-integration tests passed, one skipped** for a missing optional Crypto.Hash dependency. After the final page-size tuning, 30 focused extraction/cache/bridge tests pass; changed files pass Ruff and `git diff --check`.
- GitHub CI jobs did not start because account billing/spending limits blocked execution; [job annotation](https://github.com/soterlabs/settlement-cycle/actions/runs/35856666480/job/107166652028). This is not a local test failure.
- The diagnostic rebuild explicitly migrates the prior immutable normalized snapshot only for these changed adapters. Original ALM raw-log streams have complete inception-to-pin coverage; all discovered burn tokens match configured USDC assets. Source hashes and exact results are recorded in [the machine-readable summary](../reconciliation/capital_custody_replay_2026_08.json).
- Historical Circle minter configuration is reconstructed from Added/Removed events and checked against endpoint state and previously fetched historical calls. Transmitter/domain fields are Solidity immutable. Production retains historical contract reads.
- Receipt retrieval now selects `MintAndWithdraw` by the indexed ALM recipient and joins the same transaction's message logs. Message body, source domain/nonce and destination emitter are checked. Joined/projection cache keys are distinct from ordinary queries, and only requested message events are persisted. Larger bounded pages avoid the provider's small-page request bottleneck.

## Remaining work

A concrete Spark classification bug remains: the capital normalizer treats transfers from `external_alm_sources` as income without applying `principal_return_overrides`. The May 14, 2026 Anchorage return is explicitly configured as principal, but the replay records all **$5,270,830** as external income. [Transaction](https://etherscan.io/tx/0x1d3dd0adf2b6ab8c1bc89998bc4e370a4549382cf73a16c968c94f49445ad667). That can contaminate later borrowed-principal attribution when the ledger applies own-money repayments. The replay also incorrectly records the other three configured returns as income: **$5,000,000** on December 19, 2025, **$10,036,438** on July 16, 2026 and **$51,267,944** on August 17, 2026. The mixed principal/interest splits on the latter two remain unresolved in the config. This is a capital-tracing issue, not a change to published settlement accounting. Correct classification alone will not recover the missing beneficial-custody funding link.

The counts above still include unidentified funding routes. Spark needs further work on other bridges, Savings V2 third-party funding and unsupported beneficial/off-chain custody. Grove still needs verified links for delayed BUIDL redemptions and their fees, cross-chain investments and off-chain principal. Date/amount/fee-based BUIDL candidates remain investigative rather than active production matches. Fixing these links does not by itself validate the unfinished development-only APY range investigation.

The PR remains draft. Re-run from the saved, fingerprinted final inputs without regenerating published settlements:

```sh
.venv/bin/python scripts/validate_allocation_financing.py \
  --provenance settlements/spark/2026-08/provenance.json \
  --history-dir /tmp/allocation-spark-custody-final
```

For Grove, use `settlements/grove/2026-08/provenance.json` and `/tmp/allocation-grove-bridge-fix`. Replay code: `97d3182`. The [previous spell-only comparison](spell-capital-replay-2026-08.md) remains the recorded baseline; the new result above supersedes it for current validation.
