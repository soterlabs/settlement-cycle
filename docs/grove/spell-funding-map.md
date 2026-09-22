# Executed spells: Grove debt and acquisition funding

Verified 2026-09-22 against `sky-ecosystem/spells-mainnet`, commit
`50ea24e8605a31e283100b9f59f873404d939b87`, and the Grove/Spark payloads
referenced by those spells. This corrects the earlier uncertainty about the
purpose of Grove's historical nonzero `grab` events and initial portfolio.

## MSC debt: source and execution verified

All 11 nonzero Grove `grab` debt additions through the August 2026 pin match
the corresponding spell's `_takeAllocatorPayment` amount. For each deployed
Sky spell, RPC reads show `done() == false` one block before the observed debt
event and `done() == true` at that block. These are execution dates, not the
proposal dates in directory names.

| Executed | Debt added (USDS) | Spell date |
|---|---:|---|
| 2025-09-22 | 4,788,407 | 2025-09-18 |
| 2025-10-20 | 6,382,973 | 2025-10-16 |
| 2025-12-01 | 4,196,768 | 2025-11-27 |
| 2026-02-02 | 14,311,822 | 2026-01-29 |
| 2026-03-02 | 6,205,320 | 2026-02-26 |
| 2026-03-30 | 6,346,829 | 2026-03-26 |
| 2026-04-27 | 6,290,684 | 2026-04-23 |
| 2026-05-11 | 9,385,986 | 2026-05-07 |
| 2026-06-22 | 8,877,823 | 2026-06-18 |
| 2026-07-20 | 12,342,158 | 2026-07-16 |
| 2026-08-17 | 9,685,438 | 2026-08-13 |
| **Total** | **88,814,208** | |

The helper reads the ilk from its allocator vault, updates its rate, converts
the requested payment to normalized debt rounded upward, creates balance and
debt at the Vow with `suck`, then uses `grab` to put the outstanding debt on
the allocator while cancelling the Vow debt. The resulting balance stays in
the surplus buffer; this is not an allocation deposit. Separate transfers to
subproxies in the spell must not be mistaken for that same debt draw.

Source example:
[September 18, 2025 spell](https://github.com/sky-ecosystem/spells-mainnet/blob/50ea24e8605a31e283100b9f59f873404d939b87/archive/2025-09-18-DssSpell/DssSpell.sol).
Per-event source URLs, deployed spell addresses, execution blocks and `done`
checks are in `reconciliation/grove_msc_spell_debt.csv`.

This confirms the MSC classification underlying the earlier $259,878.117027
August borrowing-cost exclusion; it does not change the global charge or
prove allocation funding completeness. The nonzero events are on
ALLOCATOR-BLOOM-A; zero-dart initialization of ALLOCATOR-GROVE-A is not an MSC
financing charge.

## July 28, 2025: actual purchase prices explain the opening mismatch

The July 24 Sky spell raises Grove's instantaneous draw capacity, executes
the Grove payload, restores the parameter, and executes the Spark payload.
The deployed Sky spell is `0x12a79fAcd8D6Bbb1583593ef414897A9bA8f0D26`.
It changes `done()` from false to true at block **23018751**, containing
transaction `0xdd5bf338720c06dd098216e53a276a8bac00fddd80e94757f4e12c9e09f853ab`.
The receipt succeeds and contains the expected USDS and investment-token legs.

The Grove payload explicitly draws and pays Spark:

- **$608,367,166.98** for Spark's BUIDL balance at par.
- **$404,016,484** for JTRSY, fixed in the payload.
- Total new Grove debt/payment: **$1,012,383,650.98**.

USDS goes directly from Grove's allocator buffer to Spark's ALM. Spark then
sends its BUIDL and **376,701,261.673992 JTRSY shares** to Grove's ALM. This is
a debt-funded asset purchase, not a gift, and not an MSC surplus-buffer mint.

Our initial replay quotes those JTRSY shares at **$404,100,956.4404180383**
and compares that with the cash funding, creating a false **$84,472.440418**
unknown-funding gap. Acquisition cash and the subsequent valuation must be
kept separate. The spell gives the acquisition cost directly; that valuation
gap should not manufacture either an extra loan or missing principal.

An isolated replay substituting the documented JTRSY acquisition cost removes
that initial unmatched receipt (298 to 297 total). It does not resolve the
other historical funding gaps. No production override was applied by this
investigation.

Sources:

- [Sky July 24 spell](https://github.com/sky-ecosystem/spells-mainnet/blob/50ea24e8605a31e283100b9f59f873404d939b87/archive/2025-07-24-DssSpell/DssSpell.sol)
- [Grove purchase payload](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20250724/GroveEthereum_20250724.sol)
- [Spark asset-delivery payload](https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20250724/SparkEthereum_20250724.sol)

The Grove payload also explicitly identifies BUIDL's subscription address
`0xd1917664be3fdaea377f6e8d5bf043ab5c3b1312` and redemption address
`0x8780dd016171b91e4df47075da0a947959c34200`. This is direct historical
evidence for the redemption endpoint used in the delayed-payment audit.
It does not establish an individual Zero Hash payment/order reference.

## July 20, 2026: separate executions settle an inter-prime exchange

The Sky July 16 spell authorizes the payloads through StarGuard; authorization
is distinct from executing them. The two successful receipts already
inspected establish the actual asset and payment movements on July 20.

Grove's payload sends its syrupUSDC balance to Spark. Spark's payload computes
the payment with `convertToAssets(85,943,747.637271 shares)`, calls
`mintUSDS(payment)`, and sends the USDS to Grove. Thus Spark borrows to acquire
the position; Grove receives sale proceeds. Treating each transaction in
isolation leaves an unexplained position receipt in Spark and an unexplained
cash receipt in Grove. A pending purchase/sale link must connect the two
executions without creating borrowed basis from gains or moving the same debt
between ilks by assumption.

Sources:

- [Sky July 16 authorization](https://github.com/sky-ecosystem/spells-mainnet/blob/50ea24e8605a31e283100b9f59f873404d939b87/archive/2026-07-16-DssSpell/DssSpell.sol)
- [Grove delivery payload](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20260716/GroveEthereum_20260716.sol)
- [Spark funding and payment payload](https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20260716/SparkEthereum_20260716.sol)

## Limits and next implementation work

The spell evidence removes the need to infer these purposes from amounts or
payer identity. Implement the acquisition-cost correction and authenticated
cross-transaction purchase/sale links using the verified execution identities.
Continue tracing JAAA cross-chain movements, other asynchronous subscriptions,
and issuer payment references. None of these findings makes the current
Grove or Spark allocation-cost reconciliation pass by itself.
