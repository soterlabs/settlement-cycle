# Grove: initial funding and repayment investigation

This follows the custody replay on PR #215. It investigates Grove only, using
August 2026 as the unchanged control month and inception-to-August transaction
history. Published reports and API results are unchanged. The previous Grove
cost subtotal of $7,586.93 is superseded by the more conservative result below.
Spark was not rerun after the repayment-uncertainty fix.

## Confirmed missing historical links

### July 2025: funded subscription delivered directly by the issuer

The missing $50m is an existing draw, not a new loan inferred from a receipt:

1. [July 24 subscription](https://etherscan.io/tx/0x922d87f293b4bafb0edd5cc237d7df9aadffba3fe1e2e831e8601d1d724ae31a)
   draws 50m on BLOOM-A and sends 50m USDC to the Centrifuge escrow through
   vault `0xe9d1f733f406d4bbbdfac6d4cfcd2e13a6ee1d01`. The tracer already
   recorded that funded pending claim.
2. [July 24 issuer delivery](https://etherscan.io/tx/0x535e1bb08f66d6e01662bcb0959cb46e9026cddb274d4097515b47d537e1c4e6)
   calls the v3 BalanceSheet and emits `Issue`, then mints 50m JAAA to Grove.
   It has no ordinary vault `Deposit` event. The old tracer therefore left the
   subscription pending and marked the shares as unexplained.
3. [July 28 administrative cleanup](https://etherscan.io/tx/0x71bd49f75f114b85a21e9245f13e0503c7c8f4a9b1fe45a564a7015a4b72bacc)
   executes `cast()` on `0x5a110bc4fdd01b193fdadddd38231dd098274a06`.
   Logs 397–401 fulfill the old 50m request, mint its shares into escrow,
   transfer them to the spell and burn them. Grove receives no second holding.

The direct `investments(vault, Grove)` state on historical manager
`0x427a1ce127b1775e4cbd4f58ad468b9f832ea7e9` changes from pending 50m at
block 23015871 to pending 0 and maxMint 0 at block 23015872. Both remain zero
at block 23182877 and the August control pin 25878704. The direct state check
matters: `maxDeposit` and `claimableDepositRequest` can also return zero because
of transfer restrictions, so those views alone do not establish discharge.

The execution-specific correction releases the existing subscription basis
into the July 24 shares and removes the obsolete 50m component from later
pending marks. It requires the confirming cleanup in the supplied history,
rejects mismatched shapes and applies once. It does not fetch later events to
close a snapshot pinned before the cleanup.

Relevant issuer implementation:
[BalanceSheet.issue at v3.0.0](https://github.com/centrifuge/protocol/blob/abaf52ed12e4cafdb1a42475b71b4b8e3b6566f6/src/spoke/BalanceSheet.sol),
[InvestmentManager state and claim views](https://github.com/centrifuge/liquidity-pools/blob/e556c1a7a0ec7f6d700b47841eb586f5f4801406/src/InvestmentManager.sol).
The administrative spell's source was not located; its event sequence and
pinned pre/post state provide the execution evidence. Debug tracing was not
available on the configured RPC tier; no paid service was enabled.

### August–September 2025: JAAA moved to Avalanche

Five transfers move **250m shares** from Ethereum to Avalanche. Each source
transaction burns 50m shares and emits `InitiateTransferShares` for pool
281474976710663, share class `0x00010000000000070000000000000001`, domain 5,
and receiver `0x7107dd8f56642327945294a18a4280c78e153644`.
The destination Safe manually invokes `BalanceSheet.issue` for the same
pool, share class, recipient and quantity. These are manual fulfillments,
not CCTP receipts. There is no common nonce in the inspected events; this
change uses five explicit historical links, not a new generic matcher.
There is one outstanding transfer on this corridor at each delivery.

| Ethereum initiation | Avalanche delivery | Shares |
|---|---|---:|
| [Aug 27](https://etherscan.io/tx/0xc49e1b375beae51df861da3f19da036223f0381f534909adffac3fe7a40f29f2) | [Aug 29](https://snowtrace.io/tx/0x04e17ed6696fcd9ff573c3e7b7946543bb02c3a676dd78fd4532a68a9b0c70a5) | 50,000,000 |
| [Sep 2](https://etherscan.io/tx/0xb91127cd37947c01b60e8f0d8162d6ca56f11dda198f2d1a372dc84ad8f96390) | [Sep 3](https://snowtrace.io/tx/0x8eac7829c1699b587a10ac4c545e74782553d89e8ae1f5ebc9791a66151b75f0) | 50,000,000 |
| [Sep 4](https://etherscan.io/tx/0xa4aece3ea9d5dd283d993c772f8a53c86c077a0a06d511c21eeb55b8b152da56) | [Sep 4](https://snowtrace.io/tx/0x590d7ed33663ac4afc77b6898dfb8e5a60c1031df9b6779d747d3ee5bda766bd) | 50,000,000 |
| [Sep 8](https://etherscan.io/tx/0xdda1d9a966cc482b4fef9d5cdd1408c58fd636bac34d2219233b2823c6231cdd) | [Sep 8](https://snowtrace.io/tx/0x850ada023547d1579e72f31519caad111518ee35c9b7812edbd5d1e4ae4dd24b) | 50,000,000 |
| [Sep 9](https://etherscan.io/tx/0xa09ec9751938f06a4be385fcebb211f72e2882bab5ae4a4b0a584d4ef1f70bb7) | [Sep 9](https://snowtrace.io/tx/0xa32798d5cf4305375fd5c2c52f071ff34779b1164b0e12182c6f1a9791babdb4) | 50,000,000 |

The [August 21 Grove Ethereum payload](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20250821/GroveEthereum_20250821.sol)
provides the corridor's governance/configuration context. Transaction events
are the evidence for the executed transfers. The link preserves funding origin
and borrowed basis while shares are in transit. Different NAV quotes on send
and receipt are marks, not new capital or realized redemption fees. An
unreceived transfer stays in custody; unrelated future mints remain unresolved.

## Confirmed replay bug: repayments lost uncertainty

Unexplained cash is represented with zero attributed basis, but that does not
prove it is earned income. The old replay could repay debt with it, reduce
basis in other holdings, and still label those other holdings resolved.
Uncertainty now follows every holding whose per-ilk basis actually changes.
It also survives transfers into unallocated custody. Diagnostic repayments
include an affected-account count and a bounded sample; no settlement charge
is modified.

The first example is especially revealing:

- [Aug 4, 2025 receipt](https://etherscan.io/tx/0x2f03e74c7dcbacbd8d4dd474d90bc38c4649231a1130d39a0a524ae2df0dfec0)
  is **0.444758 USDC**, from the configured BUIDL cash payer.
- [The later repayment](https://etherscan.io/tx/0xb27b2346668de5396f02136e09839e81d253c657eee3e41d17ae62a845c24346)
  consumes 99,949,998.882832 USDC: the linked 99,949,998.438074 BUIDL
  settlement plus that small receipt.
- The extra receipt's economic classification is unconfirmed. The replay
  previously treated its zero basis like known equity in the repayment.
  The unresolved status now propagates to the affected BLOOM-A holdings.

There are 166 repayment transactions with uncertain source funding in this
history. This **does not mean the full value of each position is unexplained**.
The current completeness test is binary: even a small unresolved component can
make an allocation's full cost unavailable. The $0.444758 example exposes that
limitation; it is not an explanation for millions of dollars of missing cost.

## August 2026 replay result

| Ilk | Eligible diagnostic allocation subtotal | Global CoF | MSC CoF | Global excluding MSC | Gap |
|---|---:|---:|---:|---:|---:|
| BLOOM-A | 0.00 | 3,708,820.11 | 259,878.12 | 3,448,941.99 | 3,448,941.99 |
| GROVE-A | 1,233.51 | 11,784.74 | 0.00 | 11,784.74 | 10,551.22 |

Neither ilk reconciles. **Null means unresolved, not zero economic charge.**
The combined eligible subtotal falls from **$7,586.93 to $1,233.51** because
E4's $6,353.42 is now correctly excluded as affected by uncertain repayments.
This fixes an overstatement of validation coverage; it is not a lower
settlement bill.

The historical links nevertheless remove material source gaps:

- Unmatched receipts: **273 → 267** (six receipts totaling $301,235,738.71
  at their original normalized quotes).
- Unmatched outflows: **495 → 490** (five outflows totaling $250,765,183.28).
- Modeled average borrowed basis in Avalanche JAAA: **$0 → $176,324,293.40**.
  It remains provisional because earlier uncertainty affects its funding.
- Original global charges, MSC subtraction and all 31 daily idle controls are
  preserved. No invented principal, residual allocation or CoF rescaling.

## What remains

The first five remaining receipts are small BUIDL-payer transfers in
August/September 2025 totaling $2.926314. Their treatment as fee refunds,
additional redemption proceeds or another kind of receipt needs evidence.
Do not silently classify all such payer receipts as revenue.

The first remaining large funding gaps are October 13–16, 2025 JTRSY transfers
from Ethereum to Plume (approximately $20m, $20m and $10m). Later asynchronous
subscriptions/redemptions and venue-specific cash flows also remain. These are
separate from the small-receipt/binary-completeness limitation. A useful next
step is to track the *amount* of uncertain capital and its cost bounds, while
closing those explicit Plume routes, so a sub-dollar unresolved receipt does
not obscure the otherwise traced position.

## Evidence and validation

- `tests/fixtures/grove_initial_custody_events.json`: selected canonical
  HyperSync logs for the subscription, issuer delivery, cleanup and five
  transfer pairs. Tests verify their emitters, pool/share class, recipients,
  quantities, blocks and chronology.
- `reconciliation/grove_initial_funding_2026_08.json`: pinned manager-state
  reads, removed gaps, new allocation rows, per-ilk controls, input hashes and
  the first remaining discrepancies. The preceding evidence JSON retains
  the daily debt, MSC and idle control inputs.
- Full local output: `/tmp/pr215-grove-uncertainty-financing.json`.
  Source history: `/tmp/pr215-resumed-grove/history.jsonl.gz`.
  This is a fresh replay of immutable saved normalization, with targeted
  historical execution links, not a fresh extraction of every venue.

1,430 broad unit/monthly computation tests passed; 2 skips (optional Keccak dependency and isolated PostgreSQL URL). Final 19 focused origin, historical-link, financing and per-ilk tests passed. Ruff and git diff --check passed.
