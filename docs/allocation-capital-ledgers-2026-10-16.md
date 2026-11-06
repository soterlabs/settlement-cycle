# Large-allocation capital ledgers

This continues PR #215 under the operator's narrower objective: establish
capital contributed to an allocation and principal released from it. Repaying
an ilk using another account does not change this ledger. This supersedes the
outstanding-debt-floor metric as the investigation's progress measure.

The saved inputs end August 31, 2026. No September result, settlement change,
new borrowing-cost charge or certified full-year reconciliation is claimed.

## January–August results

USD millions, rounded. Pending subscriptions/redemptions stay within the
allocation until cash leaves that boundary; holding and pending capital are
reported separately in the underlying artifacts.

| Allocation | January 1 capital | 2026 contributions | Cost basis released/consumed | August 31 capital |
|---|---:|---:|---:|---:|
| Grove E9 — JTRSY | 254.663 | 1,240.570 | 654.124 | **841.109** |
| Grove E10 — BUIDL | 175.807 | 800.000 | 321.137 | **654.670** |
| Spark S1 — spUSDS | 177.030 | 5,969.927 | 5,465.224 | **681.733** |
| Spark S32 — sUSDS / POL | 528.579 | 12,434.937 | 12,260.382 | **703.134** |

Contributions include repeated turnover. Do not add those gross columns to
outstanding capital or interpret them as new ilk draws. Releases use the
weighted-average acquisition cost of disposed shares; sale proceeds can also
contain gains. Execution fees/losses can consume basis without a cash return,
so the release column is not labeled cash received at the ALM.

The sum of these four closing capital ledgers is about **2.881bn**. They measure
capital at the allocation boundary, not how much of it is still Sky-funded.
Some contributed capital can originate in gains earned elsewhere. This local
audit does not assert that all contributions were borrowed or charge them CoF.

Adding the previously audited Galaxy Warehouse (304.000m) and Anchorage
(208.829m) puts allocation-local closing capital mapped so far at about
**1.800bn for Grove** and **1.594bn for Spark**. These totals are conditional
on the reviewed normalized movements and stated principal classifications.
They are not yet a precise percentage of ilk borrowing explained: that would
require distinguishing borrowed money from reinvested gains in each source.

## Concrete funding evidence

Grove's July 28, 2025 initial acquisition is explicitly priced by the executed
spells: 404,016,484 USDS for JTRSY and 608,367,166.98 for BUIDL, matching the
1,012,383,650.98 BLOOM draw. This establishes opening acquisition cost, not NAV.
The existing guarded adapter validates the block, asset legs and draw:
`src/settle/compute/executed_spell_capital.py`.

- [Initial acquisition transaction](https://etherscan.io/tx/0xdd5bf338720c06dd098216e53a276a8bac00fddd80e94757f4e12c9e09f853ab)
- [Grove execution source](https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20250724/GroveEthereum_20250724.sol)
- [Sky authorization](https://github.com/sky-ecosystem/spells-mainnet/blob/50ea24e8605a31e283100b9f59f873404d939b87/archive/2025-07-24-DssSpell/DssSpell.sol)

Examples of 2026 contributions with a matching draw in the normalized transaction:

| Allocation | Date | Contribution | Transaction |
|---|---|---:|---|
| Grove JTRSY | April 15 | 95,000,000 | [Execution](https://etherscan.io/tx/0xb0db01f070cb6ddf7bcd42a4b945c13de5ece342af0836d6e44a9af1b7647373) |
| Grove BUIDL | February 6 | 50,000,000 | [Subscription payment](https://etherscan.io/tx/0xe1f4fd4e2fa05beac131031313e25c03a0ee4907b8d83ad5319a05121677c6c4) |
| Spark spUSDS | June 2 | 62,559,787.55 | [Execution](https://etherscan.io/tx/0x772949ee31998b3d9dd537854c9acc87301c962eaabf9c06d5f334859a524d38) |
| Spark sUSDS | April 30 | 496,553,401.88 | [Execution](https://etherscan.io/tx/0xe8ae492d7641accdc12d18c55bf22706682807cf9311856b51f351bf0bdc434f) |

Other contributions use pre-existing ALM cash. For example, Grove's March 11
50m JTRSY subscription debits existing USDC rather than drawing another 50m:
[transaction](https://etherscan.io/tx/0x945b69c587977dbd5acbbaeafaf1d214812fc8c7753ac4e45b01eda02a1c9d9b).
It is still an identifiable allocation contribution. The audit retains source
accounts and draw candidates for every contribution; in mixed transactions,
these candidates are not automatically promoted into a one-to-one funding proof.

Spark also receives funding from Savings depositors. For example, the May 9
transaction `0x11264ba7a19e1f8d3e568bbc080698511ca7f8f0416a68a395126d88f82ece62`
combines a 500m Sky draw with 813.366m of external Savings funding and several
asset destinations. Its 499.861m sUSDS contribution is identifiable, but assigning
all of it to Sky merely because the transaction includes a 500m draw is not
justified. External lender candidates and existing funding assumptions are
retained alongside the source accounts in the artifact.

## Gains and timing are separate from contributed capital

- Grove BUIDL's **14,369,567.76** of explicitly classified 2026 distributions
  does not increase capital. Passive price appreciation also adds no basis.
- Spark spUSDS's **1,317,709.82** of classified 2026 reserve receipts adds no
  capital, even if the same spell also draws money for another purpose.
- JTRSY retains **24,505,923.35** of cost basis in pending redemptions at
  August-end, against a last observed claim value of **25,000,012.17**.
- BUIDL retains **24,490,848.95** of cost basis in pending redemptions, against
  a last observed claim value of **24,999,000.00**. It must not disappear from
  the allocation merely because the tokens have already been burned.
- BUIDL's reviewed 2026 subscriptions link 26 payments to 20 issuances.
  The 800m contributed includes 90k of documented subscription fees across six
  issuances. Issuer delivery is an internal movement, not another deposit.

The cost-basis arithmetic is a diagnostic convention for separating capital
and returns; it does not change MSC cash-revenue recognition or published fees.

## Reuse opening checkpoints

Each artifact includes a January 1 checkpoint with cost basis by holding and
custody account. The tool independently runs again with only that checkpoint
and 2026 allocation movements, requiring the entire daily timeline to match.
Thus subsequent research need not revisit pre-2026 history or replay unrelated
ilks, debt repayments or allocation accounts. Checkpoints inherit the source
history's evidence and must be regenerated if that evidence is corrected.

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_allocation_positions.py \
  --history /tmp/pr215-grove-eoa/history.jsonl.gz --venues E9 E10 \
  --start 2026-01-01 --end 2026-08-31 --output /tmp/grove-positions.json.gz

PYTHONPATH=src .venv/bin/python scripts/audit_allocation_positions.py \
  --history /tmp/pr215-spark-v1-history.jsonl.gz --venues S1 S32 \
  --start 2026-01-01 --end 2026-08-31 --output /tmp/spark-positions.json.gz
```

Artifacts are `reconciliation/{grove,spark}_large_allocation_positions_2026_08.json.gz`.
They include transaction evidence, daily balances, opening checkpoints, exact
capital identities, source candidates and source-code/input hashes. No RPC
extraction or full funding replay was needed for this step.

Follow-up: Grove JAAA (Ethereum + Avalanche), Steakhouse Prime (Base), and Spark
Morpho USDC (Base) are now covered in
[the next-venues audit](allocation-capital-next-venues-2026-10-20.md).
JAAA's grouped ledger carries acquisition cost across chains rather than
treating bridge receipt NAV as a second contribution.
