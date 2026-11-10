# PRD — Async allocation accounting for Spark and Obex

Status: implemented in the separate [PR #278](https://github.com/soterlabs/settlement-cycle/pull/278), using merged
[PR #226](https://github.com/soterlabs/settlement-cycle/pull/226).
Historical effects and evidence: [January–September audit](../reconciliation/async-allocations-2026-01_to_2026-09/README.md).
Date: 2026-11-07.

This PRD is a documentation-only follow-up. It does not extend #226, implement
adapters, authorize payments, or restate published settlements. The coverage
assessment below uses #226 at commit `792ac4a` and the current prime configs.
Pin the final merged dependency commit when implementation begins.

## 1. Goal

Apply the same economic ownership rule to Spark and Obex's async allocations:
recognize a vault deposit when its corresponding shares are minted, and retain
a withdrawal until its proceeds settle. Position valuation, daily exposure,
capital accounting, revenue and CoF attribution must agree throughout that
lifetime, without counting income or principal twice.

The policy reference is
[Atlas A.2.4.2.3](https://sky-atlas.io/#5d38602e-46e8-48d2-b661-460a3fe9b0b9).
Reuse the ownership and economic-capital mechanisms introduced by #226; add
protocol adapters and evidence rather than a second valuation engine.

## 2. Problem and scope

#226 covers Grove's JTRSY, JAAA and BUIDL, and configures Spark's JTRSY gateway.
It does not cover every async allocation used by Spark and Obex. Existing
queue-transfer netting or historical funding tracing is not proof that monthly
settlement valuation retains the pending position.

| Prime / venue | Allocation | Current handling / gap | Required follow-up |
|---|---|---|---|
| Spark S14 | Maple syrupUSDC | Queue transfers/refunds enter capital netting; pending ownership is absent from the shared exposure adapter. | Retain controller-owned queued shares or cash through actual settlement. |
| Spark S15 | Maple syrupUSDT | Same gap, with a separate pool and queue deployment. | Apply the same accounting with the correct USDT contracts and units. |
| Obex V1 | Maple syrupUSDC | Wallet valuation; no configured async ownership adapter or queue routing equivalent to Spark's. | Reuse the Maple adapter with Obex's controller and ownership state. |
| Spark S16 | Ethena sUSDe | Wallet-share valuation omits underlying held during an unstaking cooldown. | Retain the controller's underlying-asset claim until unstake payment. |
| Spark S19 | BUIDL | Historical request/payment associations exist in funding tracing; the settlement cash-ledger policy currently covers Grove only. | Extend settlement accounting to Spark, including partial payments and verified small exits. |
| Spark S21 | Superstate USTB | Reviewed subscription/redemption associations exist in tracing, not the shared settlement adapter. | Consume verified requests, pricing and receipts in economic positions and capital. |
| Spark S22 | Superstate USCC | Same separation; observed delayed payouts use settlement-day NAV. | Retain the correct variable-price claim until its price is fixed, then cash until payment. |
| Spark S20 | JTRSY | Covered by #226's configured gateway. | Regression coverage; no duplicate adapter. |

Maple is the first implementation priority: the published January–September
2026 reports contain material Spark and Obex holdings. Spark's published RWA
month-boundary balances are zero in that interval, while the repo also contains
earlier RWA allocation histories. Zero month-boundary balances alone do not
prove there were no intramonth positions or pending requests.

Arkis S18 is outside the initial async-adapter scope: the checked current
implementation performs ERC-4626 withdrawal atomically. Validate the deployed
version relevant to each audited period; do not infer async behavior from a
similarly named retail Spark Prime vault or prospective product design.
Ordinary atomic lending/vault operations retain their existing paths.

### Non-goals

- Changing SDE designations, subsidy eligibility, debt, rates or ownership caps.
- Treating every pending claim as idle cash or as an SDE deduction.
- Reworking bridge accounting, retail savings liabilities or unrelated rewards.
- Replacing existing borrowed/earned funding attribution with new Sky debt.
- Rebooking 2025 revenue solely because older events seed a 2026 opening claim.
- Adding partner rebates or altering #226's accepted Grove adjustments.
- Enabling additional Grove adapters implicitly through a protocol-wide switch.

## 3. Accounting requirements

### 3.1 Ownership states and valuation

Each supported adapter must identify the configured owner/controller at the
exact pinned block, distinguish the following states, and return disjoint
components to the shared valuation mechanism:

| State | Attributed economic position |
|---|---|
| Subscription requested; corresponding shares not minted | No new vault position or vault-specific SDE deduction. Preserve the funding trail separately. |
| Shares minted and allocated to the owner, awaiting collection | Include issued shares at the applicable NAV, including zero-wallet cases. |
| Shares collected into the wallet | Include wallet holdings; remove the escrow component exactly once. |
| Withdrawal requested, entitlement still denominated in shares | Retain owned shares/share-equivalent claims using the verified pricing terms. |
| Withdrawal processed and underlying amount fixed | Retain that fixed underlying quantity, independently of later share NAV. |
| Cancellation confirmed, shares awaiting return | Retain owned shares until they return to the wallet. |
| Withdrawal proceeds paid | Remove only the paid/settled component; retain any verified remainder. |

Fixed underlying quantity does not mean a universal fixed USD value. Apply the
existing underlying-token pricing policy. For example, a USDe claim must not
be repriced as sUSDe after the cooldown conversion has fixed its USDe amount.

Determine the actual issuance, pricing and settlement boundaries from deployed
contracts and canonical events. A method named `deposit`, `mint` or `claim`
does not by itself establish which boundary occurred. If issuance and investor
allocation are separate, establish when the minted shares became attributable
to this controller; document and test that interval explicitly.

Use ownership state rather than transfer-permission limits. Frozen transfers,
a matured cooldown, an expired request or an empty wallet do not prove that
the prime's economic claim has disappeared. Cancellation behavior applies only
where the deployed protocol supports it.

### 3.2 Capital, income and fees

Reuse #226's economic-capital bridge. The position and capital series must
change on the same recognition clock:

```
economic position = wallet value + recognized pending economic value
economic capital  = existing capital flow + change in the corresponding pending basis
venue income      = change in economic position - economic capital
                    + applicable realized adjustments
```

The pending basis depends on the source. Vault cash events may require an
issued-deposit acquisition-basis adjustment; wallet-share exits may require a
pending-redemption receivable adjustment. Do not add both representations of
the same principal or apply a generic receipt-time adjustment twice.

- A custody transfer, collection or cancellation return creates no income.
- A partial payment releases only its settled portion. Grouped payments settle
  only the requests supported by evidence.
- Preserve existing documented fee-recognition dates and dated carrying marks.
  Book the actual cash-versus-carrying difference once.
- Fee detection consumes original capital events. Pending-value bridges must
  not create a fee signature or hide a genuine subscription fee.
- Restore verified outgoing share principal omitted by an incoming-yield filter;
  incoming yield mints must retain their existing income treatment.
- Preserve the distinction between total invested capital and Sky-funded
  principal. An observed fee or execution shortfall is not automatically a loss
  of borrowed principal.

### 3.3 CoF and settlement boundaries

Apply existing SDE caps, effective dates and separately eligible idle-USDS
rules after economic exposure is assembled. Use the production daily borrowing
charge and subsidy-tranche calculation. Do not introduce a second rate formula.

Maple and sUSDe are not configured SDE allocations in the audited configuration.
Their missing pending-position handling therefore does not establish a missing
global CoF deduction. The implementation must measure valuation, income timing,
venue-level CoF attribution and whole-prime CoF separately.

All inputs must respect the report's closing pin. A later same-day payment
cannot erase an earlier pinned claim. Daily UTC closing exposure retains the
claim through the last unpaid close and removes it at the paid close. Use
actual settlement, not an assumed T+1/T+N window or cooldown expiry date.

## 4. Protocol-specific requirements

### 4.1 Maple — Spark S14/S15 and Obex V1

- Identify the pool, withdrawal-manager/escrow deployments and relevant
  upgrades from verified on-chain evidence for each historical interval.
- Read ownership by controller/request, not the queue's aggregate balance.
  Two primes using the same queue must not receive each other's holdings.
- Verify whether each deployment keeps a claim exposed to share NAV or fixes
  underlying assets at request/processing. Do not assume yield behavior from
  a general Maple description.
- Handle queued requests, processing, partial fills, cancellations/refunds and
  final settlement, including queues that transfer assets automatically.
- Separate actual withdrawal escrows from ordinary capital-routing addresses.
  Spark S14's existing `share_burn_destinations` includes Grove's ALM for an
  inter-prime capital transfer; that address is not a Maple escrow. Do not
  blindly treat every configured destination as pending Spark ownership.
- Preserve atomic deposit recognition and unrelated direct/inter-prime share
  transfers. An issuer transfer or an inter-prime transfer is not automatically
  a new async request.

Reference: [Maple pool interfaces](https://docs.maple.finance/technical-resources/pools/pools).

### 4.2 Ethena — Spark S16

- Read the owner-specific cooldown and underlying held through the Silo.
- Retain the converted underlying amount until its actual transfer on unstake,
  including after the cooldown has matured.
- Support the deployed duration/configuration at the historical block; do not
  hardcode a seven-day wait or infer payment from elapsed time.
- Keep the ordinary share position and cooldown claim disjoint. A failed call
  creates neither a claim nor a synthetic capital flow.

Reference: [StakedUSDeV2](https://github.com/ethena-labs/code4arena-contest/blob/main/protocols/USDe/contracts/StakedUSDeV2.sol).

### 4.3 BUIDL — Spark S19

Reuse the matched request/payment architecture and reviewed evidence already
available in [the Spark BUIDL audit](spark/buidl-redemptions-2026-10-08.md).
Do not copy Grove's policy unchanged:

- The reviewed May 14, 2025 request is only 250,000 shares, below Spark's
  incoming-capital/yield threshold.
- The July 18, 2025 200m-face request receives **98.543517 USDC**, followed by
  **199,899,898.685658 USDC**. The first installment cannot be ignored or treated
  as an independent unmatched material receipt.
- Extend shared settlement matching to support one request with multiple
  payments, explicit finalization and a visible remaining claim after an
  advance. Preserve the strict treatment of ambiguous associations.
- The reviewed April 14, 2025 4,999.749278-USDC receipt has no matching request
  in the audited boundary stream. Do not fabricate a redemption for it.
- Verify dated payout/fee policy per prime. Do not inherit Grove's subscription
  fee, haircut date or SDE treatment merely because the share token is shared.

Amounts from these historical tracing associations are evidence, not new
settlement credits. Preserve their borrowed/earned funding mix independently.

### 4.4 Superstate — Spark S21/S22

Reuse the verified groups and pricing evidence in the
[USTB audit](spark/ustb-capital-2026-10-08.md) and
[USCC audit](spark/uscc-capital-2026-10-08.md).

- Atomic subscriptions count delivered shares once; any genuinely asynchronous
  subscription must establish the issuance boundary before recognizing them.
- Retain each redemption claim from share exit until its reviewed cash receipt,
  including before payment later on the same date.
- USTB's reviewed executions use the token-selected execution oracle. Do not
  substitute raw share units or assume a universal $1 NAV.
- USCC's observed December 1/3, 2025 requests settle on December 2/4 using
  settlement NAV. Verify when payout pricing becomes fixed. Earlier cutoffs
  must value the then-existing claim without looking ahead to a future receipt
  or future settlement quote.
- A reviewed historical cash association is not a generic nearest-date matcher
  or permission to classify every payment from the same payer as this asset.
- Preserve unrelated report-pricing policy. If an existing pricing convention
  prevents valid claim valuation, document that dependency and resolve its
  required scope explicitly rather than silently using a fallback price.

## 5. Implementation shape

Build on the merged #226 version of these existing entry points:

- `src/settle/normalize/async_vaults.py`: common ownership components, protocol
  dispatch and economic capital alignment.
- `src/settle/normalize/redemption_settlements.py`: verified cash associations,
  exact-block claim lookup and request/payment accounting.
- `src/settle/normalize/positions.py`: opening/closing economic positions.
- `src/settle/compute/monthly_pnl.py`: daily/MTD and full-month orchestration.
- `src/settle/compute/prime_agent_revenue.py`: revenue, average holdings and
  the separate original-event fee source.
- Existing config loaders, provider/input caches, provenance, snapshot
  version checks and settlement report writers.

Add typed, opt-in per-venue protocol configuration where needed. Do not force
Maple or Ethena through a different protocol's gateway selector or the current
constant-$1 cash matcher. Keep protocol detection beneath the common accounting
interface and reuse existing raw sources and verified association adapters.

Request IDs and canonical state take precedence. Where a protocol has no shared
on-chain identifier, require a unique, supported association or reviewed exact
event links. Do not add age limits, nearest-date rules or undocumented FIFO
guesses to make an ambiguous reconciliation finish.

Each claim must retain enough provenance to reproduce its owner, asset, units,
quantity, request/issuance/pricing/payment identities, valuation basis, block
cutoffs and open remainder. Source failures and unsupported contract versions
must not become zero holdings. A verified empty state or pre-deployment state
is different from an unreadable state; historical deployment changes must not
hide claims owed under a preceding deployment.

Expose wallet and pending components through the existing daily provenance,
reusable snapshots and reports. Preserve compatibility when reading older
artifacts, while requiring refreshed calculations under changed code/config
versions. Results must remain deterministic and reusable across worker restarts.

## 6. Historical audit and reconciliation

The target earning months are **January through September 2026, inclusive**,
for Spark and Obex. Seed opening claims from complete required earlier history
or a verified empty anchor. Spark's config starts November 18, 2024; Obex's
starts November 17, 2025. These dates do not replace evidence of each protocol's
opening state. Never use a rolling few-day lookback or wallet balance alone.

Scan all daily closes and relevant intraday transitions, including requests
that open and close within one month. Use the earlier RWA cases above as
regressions without automatically creating 2025 MSC adjustments.

Produce two distinct comparisons:

1. **Implementation effect:** baseline after #226 versus the new adapters,
   using identical pins, historical rates, pricing policy, manual inputs and
   input revisions. Isolate this PR from unrelated data/methodology changes.
2. **Amount still owed:** corrected results versus published/settled MSCs,
   retaining original applicable rate conventions and subtracting previously
   settled credits/debits. Trace each proposed true-up to evidence.

For every month/prime/asset, report:

- Old/new opening, closing and average economic position.
- Every detected pending window and its request/pricing/settlement evidence.
- Old/new investment income, whole-prime CoF and venue-attributed CoF.
- Unrounded delta, previously reconciled amount and net remaining true-up.
- Timing-only/reporting-only differences separately from new amounts owed.

Use `sky_revenue - sde_revenue` for the borrowing-charge comparison and the
existing period-net settlement calculation for the prime's earned net revenue.
Do not infer a payment obligation from a raw asset-value increase or from the
legacy `monthly_pnl` field alone.

Deliver a repeatable, read-only audit command for both primes and the inclusive
month range, with explicit baseline and output locations. This is a requirement
for the implementation; the current two #226 replay scripts cover identified
Grove windows and are not a full Spark/Obex sweep. Persist reconciliation data
as an auditable artifact and retain immutable source/baseline identifiers.

Write reruns to separate directories. Do not overwrite frozen workbooks,
published reports or on-chain paid amounts. A calculated reconciliation is not
an executed payment; any later settlement bridge must consume accepted net
amounts once through the existing adjustment mechanism.

## 7. Validation and acceptance

The implementation is complete when all of the following hold:

1. Every in-scope venue has a verified adapter or positive evidence that its
   relevant operations are atomic. A zero balance is not such evidence.
2. The same ownership components reach opening/closing valuation, daily SDE,
   average holdings, economic capital and revenue accounting.
3. Canonical regressions cover:
   - Maple USDC/USDT queues, partial processing, refunds/cancellation, two owners
     sharing an escrow and the real inter-prime transfer boundary.
   - Obex's actual controller and Maple deployments.
   - Ethena before/during/after cooldown and after expiry but before payment.
   - Spark BUIDL's small request, July advance/final payment and unmatched cash.
   - USTB/USCC subscriptions, delayed pricing and request-only/receipt-only pins.
4. Generic tests cover zero-wallet claims, partial collection, same-day and
   month-boundary settlement, mixed deposit/withdrawal activity, transfer
   restrictions, malformed/unavailable inputs, historical upgrades, duplicate
   logs and out-of-order ingestion.
5. Unissued deposits produce no vault-specific SDE deduction. Collection,
   cancellation return and claim reclassification produce no artificial income.
6. Fixed underlying claims do not move with share NAV; variable-price claims
   use only information available at the cutoff. No expiry removes ownership.
7. Original-event fee detection and dated realization are preserved. No principal
   is recognized twice, no receipt creates debt, and funding mix is conserved.
8. January–September audit artifacts are reproducible and prior true-ups are
   explicitly netted. No dollar impact is assumed before completing this audit.
9. #226's Grove regressions and accepted adjustment inputs remain unchanged;
   Spark JTRSY and unrelated atomic venues retain their existing behavior.
10. Full unit and database integration checks pass, including provenance/report
    round trips, identical-input repeatability and fresh-worker raw-input reuse.

## 8. Delivery sequence

1. Pin the dependency and complete the per-deployment evidence/ownership map.
2. Implement the shared adapter interfaces with Maple first for both primes.
3. Add Ethena and the Spark-specific RWA payment/pricing adapters, reusing the
   reviewed tracing evidence without conflating it with settlement coverage.
4. Connect every consumer and add canonical boundary/fee regressions.
5. Run the full historical sweep and produce the separated reconciliation
   comparisons, including zero-impact findings where supported.
6. Submit the implementation as its own reviewable PR, with coverage, evidence,
   measured impacts and validation. Settlement publication remains a separately
   identifiable action after the net amounts are accepted.
