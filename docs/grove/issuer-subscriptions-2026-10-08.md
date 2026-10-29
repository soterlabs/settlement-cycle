# Grove STAC and BUIDL subscription funding

Follow-up in draft PR #215 to
[quantified funding and Plume custody](quantified-funding-2026-10-07.md).
August 2026 was replayed using the same immutable normalized history, debt
controls and idle deductions. Settlement reports and API revenue outputs were
not regenerated or changed.

## Results

The remaining $50m entries were issuer token deliveries, not cash receipts.
Twenty-two deliveries now link to **$900m of actual USDC subscription payments**:
$100m into STAC and $800m into BUIDL. BUIDL issued $799.91m face value after
$90,000 of documented-pattern fees. Total removed unmatched receipt value is
**$899.91m**.

| Metric | Before | After |
|---|---:|---:|
| Unmatched receipts | 239 | 217 |
| Unmatched outflows | 465 | 438 |
| STAC E7 average modeled borrowed principal | $0.00 | $76,695,461.63 |
| STAC E7 modeled borrowing costs | $0.00 | $237,786.98 |
| BUIDL E10 average modeled borrowed principal | $88,706,527.94 | $560,158,956.86 |
| BUIDL E10 signed diagnostic cost after SDE deductions | −$1,906,056.86 | −$444,397.86 |
| Apollo E22 average modeled borrowed principal | $16,728,886.49 | $18,181,841.33 |
| Apollo E22 modeled borrowing costs | $51,868.61 | $56,373.76 |
| Fully eligible allocation cost subtotal | $1,233.51 | $1,233.51 |

The downstream Apollo change comes from correcting funding and later repayments,
not a new Apollo cash inflow. All three allocations still have unresolved funding
elsewhere in their history. The negative BUIDL diagnostic reflects subtracting
known dollar SDE deductions from incompletely traced borrowed basis; it is not a
payable credit. Do not sum these signed model estimates as verified charges.

The per-ilk reconciliation and conservative uncertainty intervals are unchanged:

| Ilk | Eligible allocation costs | Global costs excluding MSC | Unreconciled gap |
|---|---:|---:|---:|
| ALLOCATOR-BLOOM-A | $0.00 | $3,448,941.99 | $3,448,941.99 |
| ALLOCATOR-GROVE-A | $1,233.51 | $11,784.74 | $10,551.22 |

The fixes explain concrete funding routes. They do not establish complete
reconciliation or narrow the full-history outer bounds, which remain dominated
by other unresolved custody receipts. The evidence and before/after rows are in
[grove_issuer_subscriptions_2026_08.json](../../reconciliation/grove_issuer_subscriptions_2026_08.json).

## STAC

The [December 11, 2025 Grove spell](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20251211/GroveEthereum_20251211.sol)
identifies:

- USDC deposit wallet: `0x51e4c4a356784d0b3b698bfb277c626b2b9fe178`.
- STAC token: `0x51c2d74017390cbbd30550179a16a1c28f7210fc`.

On December 16, Grove paid $1,000 in a test transfer, then $49,999,000. It
subsequently received 50,000 STAC tokens at the observed $1,000 issue price.
On December 18, it paid $50m and received a second 50,000-token issuance.

| Date | Cash funding transaction | STAC issuance |
|---|---|---|
| Dec 16 test | [0xc3aa67…](https://etherscan.io/tx/0xc3aa67c5974286c05ba8fb0013f456897efc5036056d6afb3f8581d8fda81c2b) | |
| Dec 16 balance | [0x45f8a6…](https://etherscan.io/tx/0x45f8a63c48adb2bcbc867130753f801bc19c63b033a9a6d75e33cbcdf2a311fa) | [0x2f544c…](https://etherscan.io/tx/0x2f544c36733dfc9c71c76a371e21a3c5d04b3af475635a85d7000996c74c0ae4) |
| Dec 18 | [0x2559a0…](https://etherscan.io/tx/0x2559a0b92c5897aa8a7f18298999e1d7ab0ae8e736e0be4106ad64435965a82b) | [0x8f8e78…](https://etherscan.io/tx/0x8f8e781c2fbfdefdee08a225e702b319e9fabd65c3c0a1167df5e13f0303dc8e) |

The full inception-to-August query found exactly these three deposit transfers
and two STAC mints. The test-payment transaction also funds Galaxy ARCH CLO and
FalconX entrypoints: those payments are not assigned to STAC. Pending
subscription basis belongs to E7 until issuance; issuance transfers that basis
to the tokens. Known earned cash stays non-borrowed.

## BUIDL

The [July 24, 2025 Grove spell](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20250724/GroveEthereum_20250724.sol)
authorizes the BUIDL deposit wallet
`0xd1917664be3fdaea377f6e8d5bf043ab5c3b1312`. Its accompanying test demonstrates
the cash-transfer/issuer-delivery sequence.

The inception-to-August query finds 26 Grove USDC payments to this entrypoint,
funding 20 capital issuances from February 5 through April 13, 2026. Six
subscriptions include a $1,000 test followed by the remaining $49,999,000.
Each issuance finishes before the next reviewed subscription starts. Small
reward mints are not subscription deliveries and are left unchanged.

Six $50m subscriptions delivered $49.985m each: February 5, 6, 17, 18, 27 and
March 2. This $15,000 difference agrees with the existing issuer fee policy in
`config/grove.yaml`. The other subscriptions deliver their paid face amount.

The full paid borrowed basis remains with the allocation when the tokens arrive,
even when a fee reduces their face value. For example, a fully borrowed $50m
payment delivers $49.985m of tokens with $50m borrowed basis. Issuance is a
custody change, not repayment or redemption. A later cash exit can realize a
shortfall under the existing ledger rules. Revenue already accounts for issuer
fees separately; these changes do not book them again. The replay's total
realized principal loss remains unchanged at $50,001.561926.

These are explicit reviewed historical links, supported by the configured
recipient, canonical payments, chronological single outstanding subscription
and subsequent issuance to the same ALM. There is no shared on-chain subscription
identifier between an off-chain issuer's payment and mint. No generic amount or
nearest-date matcher runs in production, and the issuer's commingled EOA is not
traced after the deposit boundary.

## Validation and limits

- Canonical transfer fixtures validate payment senders/recipients, token
  contracts, mint recipients, amounts, blocks, chronology and all $90,000 of
  fee differences.
- Replays preserve original debt draws and known earned funding. Pending
  subscriptions remain when a pinned history ends before issuance.
- Tests reject missing test payments, underfunded claims, changed issue amounts,
  wrong execution order, duplicate events and raw appends to adapted histories.
  Unreviewed mints remain unresolved.
- Broad unit/monthly integration suite: 1,464 passed, two environment-dependent
  skips and seven existing fixture warnings. The final fee-basis convention and
  extra mixed-funding case passed the focused 18-test suite.
- Ruff and whitespace checks passed. Spark and Grove August control hashes are
  byte-identical. Spark was not replayed in this follow-up.

The largest remaining receipts are $20m USDC arrivals on June 2 and July 21,
plus $19,996,000.80 on June 11. Their exact transaction identities are retained
in the evidence JSON; no source has yet been assigned to them in this work.
