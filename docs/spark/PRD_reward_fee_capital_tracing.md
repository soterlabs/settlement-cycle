# PRD — Spark earned receipts in capital tracing

Status: proposed follow-up; not implemented. Operator guidance recorded
2026-10-09. Related work: PR #215.

## Objective

Consume the independently verified Aave reward claim and Uniswap V4 fee
evidence in the capital normalizer. Explain earned receipts without creating
borrowed principal, counting withdrawals twice, or recognizing revenue twice.
The completed August diagnostic replays still leave these receipts unmatched.

This is a capital-tracing change. It does not authorize changing published
January–August reports, API revenues, global Sky debt, rates, or deductions.
Existing MSC classifications are authoritative for this work; do not require
new counterparty confirmation merely to reuse an existing accounting decision.

## Operator guidance on incoming allocation capital

Incoming funding attributed to an allocation adds to that allocation's capital,
including when the ALM already holds the asset. Existing holdings alone are not
a reason to leave the new funding unallocated. An asset not previously held is
the straightforward case; pooled identical assets do not encode a unique
physical history for each dollar.

Keep the allocation's total invested capital distinct from its Sky-funded
principal. Savings funding and reinvested earnings can increase allocation
capital without increasing Sky borrowing. Preserve the earlier requirement that
Sky borrowing costs apply only to borrowed/minted capital, not gains. Where a
deterministic attribution convention remains necessary, document it as a model
convention, not a contract-enforced routing rule. Do not treat this guidance as
confirmation of the separate proportional Savings principal/interest repayment
assumption.

For Anchorage, reuse the current MSC capital overrides in `config/spark.yaml`:
10,036,438 USDC on July 16 and 51,267,944 USDC on August 17 are classified wholly
as capital. The exploratory 10m/50m principal scenario is not the approved MSC
split. Reusing the conservative classification is not evidence that the economic
interest component is literally zero. Match returned capital to the funded
facility and preserve funding-source conservation; do not invent new Sky debt.

## Aave reward claim

Confirmed receipt: **243,167.543642328131607315 aUSDS**, October 8, 2025,
transaction
`0x0af39af528cd328028432e17f451aff046b19536824007f2e1665f1b7ed5b2e3`.

The October 2 Spark spell calls `claimAllRewardsToSelf`:
https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20251002/SparkEthereum_20251002.sol#L180

Evidence and existing verifier:

- `tests/fixtures/spark_aave_rewards_claim.json.gz`.
- `scripts/audit_spark_aave_reward_claim.py` and its regression tests.
- `docs/spark/remaining-receipt-questions-2026-10-09.md`.

Require a matching rewards claim naming the ALM as user and recipient and an
actual transfer of the reward token to the ALM. Exclude incidental aToken
interest mints. Preserve unrelated SparkLend reserve gifts and all other legs
of the transaction. Represent the verified earned value with zero new Sky or
Savings principal, and carry that distinction through later transfers and
reinvestment. Do not duplicate a revenue item already recognized by MSC.

## Uniswap V4 earned fees

Confirmed witness: S61, NFT **324005**, August 20, 2026, transaction
`0x18a9cbdbd1fcf140e5e21b72571c1455d13be6ca5256c8e9f122f43873824593`.

| Component | Token units / USD at the diagnostic par valuation |
|---|---:|
| Earned PYUSD fees | 6,311.595421 |
| Earned USDS fees | 6,355.428945075564808842 |
| Total earned fees | 12,667.024366075564808842 |
| Returned liquidity principal, already normalized | 1,215.925898327927459078 |
| Residual after the independent fee proof | 0.00905259889230006504868 |

Reuse `scripts/audit_spark_v4_fee_witness.py`,
`tests/fixtures/spark_v4_fee_witness.json.gz`, and
`docs/spark/v4-fee-witness-2026-10-09.md`. Its independent proof reads position
state before/after the block and computes fees from previous liquidity and
the change in recorded fee growth using contract integer arithmetic.

Initially support only the verified zero-hook, single-position-modification
per block case. Check pool identity, ticks, owner, liquidity, token decimals,
and actual received cash. Multiple modifications and hooks require separate
evidence; retain their unresolved status until supported. Never infer fees by
setting them equal to the unexplained cash remainder.

Add only the independently proved fee component as earnings with no borrowed
basis. Retain the existing principal withdrawal exactly once. Preserve the
sub-cent discrepancy in explicit rounding diagnostics. One proved receipt
does not authorize classifying every V4 receipt as fees.

## Integration and acceptance

1. Add narrowly scoped normalization using the authenticated economic events;
   avoid a broad sender allowlist or hard-coded amount-only classification.
2. Prove idempotence and unchanged unrelated transaction legs, debt draws,
   repayments, and funding stocks by lender.
3. Extend the existing meaningful negative tests: wrong recipient, missing
   delivery, incidental interest mints, duplicate application, changed funding,
   V4 multiple modifications/hooks, and inconsistent principal or cash.
4. Replay to a new diagnostic checkpoint with immutable input/code hashes.
   Do not overwrite the completed overnight checkpoints. Check that the two
   target unmatched receipts decrease by only their independently proved
   amounts, allowing explicit token-rounding effects.
5. Compare per-allocation capital and borrowing costs before/after. Verify
   that the full reconciliation still closes and global Sky costs and MSC
   exclusions remain unchanged. A numeric bridge alone is not proof of every
   remaining receipt's purpose.
6. Document any remaining uncertainty separately. Once valid, extend coverage
   to other qualifying V4 events; do not assume the candidate scan is a
   complete inventory.

## Remaining classification questions

The operator has now confirmed Maple's 1,661,609.59 USDC, the USDe Safe's
7,281,596.60 USDe and the other PYUSD payer's 1,250,000 PYUSD as yield.
The existing sender configuration handles these, independently of the two
event-based integrations proposed here. Operations' 1,203,063.147623 USDC
inflows have also been explicitly confirmed as yield and configured on Base
and Avalanche-C. The smaller receipts in the remaining-receipts note remain
open; the operator identifies `0xc8a3e1e0776b912047c89dc16470fd9c7ea1141d`
as Maple-related, without yet classifying that payment. Their historical
cash amounts are neither proposed revenue adjustments nor August borrowing
costs. Do not block the two independently proved integrations on these questions.
