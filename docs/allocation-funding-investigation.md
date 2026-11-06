# Spark and Grove allocation funding investigation

2026-09-22, PR #215. Existing settlement amounts and revenue calculations are
unchanged. This investigation concerns funding provenance for allocation
analytics, not the global debt calculation.

**September 23 update:** The executed-spell purchase corrections are now
implemented. Latest replay and MSC-adjusted comparison results are in
[the spell replay report](spell-capital-replay-2026-08.md); the counts and
pending statuses below describe the preceding September 22 baseline.

## Verified defects corrected

1. **Spark PSM3 custody was absent.** PSM3 shares are internal accounting,
   not ERC-20 transfers. The normalizer saw cash enter/leave PSM3 without the
   corresponding beneficial holding. Base transaction
   `0x00443295933073cb1df2e067f5d34aff092481e8ce2b538f509cffce6260d227`,
   block 22575049, includes a 999,900 USDS deposit and its PSM3 share credit
   at log index 363. The old normalized history contained only the cash
   outflow. The adapter now recognizes the configured PSM3's Deposit and
   Withdraw events, using the credited receiver / debited user respectively.
   It preserves borrowed basis through custody and uses actual assets at
   transaction prices. Full withdrawals use exact cash, avoiding a rounded
   per-share quotation. Missing opening history remains an error.

2. **Secondary custodians lacked some cash legs.** Cash tokens were added
   only for the primary ALM and NFT holders, even when reporting explicitly
   configured another beneficial custodian. That could leave only one leg
   of a swap or repayment. The normalizer now registers the same known cash
   tokens at each configured tracked holder. This does not add unconfigured
   third-party counterparties or treat receipts as presumed loans.

3. **Configured BUIDL distributions were treated as unknown capital.**
   Both primes already configure a $1M per-transfer capital threshold for
   BUIDL. The capital normalizer ignored it. Grove transaction
   `0xdd60b4df6448dcea2ac6850368afc822948923619a3c9f4e0dbec949f4bb0d79`
   (2025-08-12) includes a 58,503.66 BUIDL mint from zero to the ALM, previously
   recorded with zero external income. The replay now honors the existing
   configured policy for sub-threshold issuer mints: add value without
   borrowed basis or an unknown-funding flag. Each mint is checked separately;
   a batch of small distributions is not reclassified as a subscription.
   This reuses the configured accounting rule; it does not establish a new
   claim that every small token receipt is income.

## Grove: remaining evidence and results

The corrected replay has 298 unmatched receipts (previously 559) and 530
unmatched outflows (previously 528). Broader cash tracking exposes additional
unresolved flows, so a lower count alone is not proof of reconciliation.
The calculable allocation-cost subtotal remains $6,222.614398 against
$3,720,604.844326 globally; the MSC-adjusted global target is
$3,460,726.727300. It remains incomplete.

Large outstanding gaps include asynchronous BUIDL settlement. On 2025-08-04:

- `0x5ebafa5e7253ffaa7e1f596d197258c68f7648b935f538e4d73380f710b072bb`
  sends 100M BUIDL from the ALM to `0x8780dd016171b91e4df47075da0a947959c34200`.
- Hours later,
  `0x092c6464116cc908dacb4f71e4a64b72a9fa6a4368a92b774490b2d59625aa79`
  receives 99,949,998.438074 USDC from
  `0xcfc0f98f30742b6d880f90155d4ebb885e55ab33`.

Full RPC receipts were inspected. The normalized replay has separate
unpaired legs. Their timing/amounts suggest a settlement relationship, but
are not a sufficient authenticated request identity. No amount/date matching
was added. A verified redemption-adapter or issuer settlement linkage is
needed to release the original borrowed basis.

The initial portfolio also needs migration tracing: the 2025-07-28 transaction
`0xdd5bf338720c06dd098216e53a276a8bac00fddd80e94757f4e12c9e09f853ab`
contains $1,012,383,650.98 of new debt, $608,367,166.98 of BUIDL, and a
$404,100,956.440418 quoted JTRSY receipt. The difference must be explained
from migration/valuation evidence, not silently called a loan or income.

E21 still requires off-chain principal matching; E36 requires beneficial
custody tracking. Bridge coverage is currently authenticated CCTP v1 only.
Unknown receipts propagate through later reallocations, so many unresolved
venues can descend from one unresolved earlier funding event.

## Validation

Regression coverage includes deposit/withdraw ownership, partial/full PSM3
exits, borrowed-principal conservation despite earnings, exact full-exit cash,
missing-history rejection, secondary-holder swaps, and multiple issuer
distributions in one transaction. Fresh August diagnostic runs use
`/tmp/allocation-grove-custody-fix` and `/tmp/allocation-spark-psm-fix`, leaving
published settlement artifacts untouched. Spark's final updated validation
is pending; no improved Spark reconciliation is claimed yet.
