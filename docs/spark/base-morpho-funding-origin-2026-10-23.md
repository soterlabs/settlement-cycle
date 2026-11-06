# Spark Base Morpho: original funding through August 2026

PR #215 diagnostic research, October 23, 2026. This extends the existing S34
acquisition-cost ledger through its authenticated CCTP claims and Base USDC
cash (S39). Pre-2026 history establishes opening sources only. Production
settlements, API revenue and borrowing-cost calculations are unchanged.

## Result

| August 31 S34 capital | USD | Share |
|---|---:|---:|
| Traced original Sky borrowing | **264,631,606.58** | **93.4710%** |
| Remaining origin to classify | **18,484,532.67** | **6.5290%** |
| Total allocation acquisition capital | **283,116,139.25** | **100%** |

The residual can include reinvested gains, external funding and untraced source
capital. It is not automatically missing Sky debt. This is coverage of S34's
closing capital, not coverage of Spark's total debt or all allocations.

The earlier direct-only audit reported zero attributed borrowing because a Base
vault contribution does not itself mint Ethereum ilk debt. Following the claims
first attributes **254,116,181.11** (89.7569%). Applying the conservative bound
below to mixed external-funding transactions adds **10,515,425.47**, producing
the final result above. Neither step changes the underlying capital ledger.

## What the code follows

`audit_bridged_allocation_origins.py` selects the CCTP claims consumed in
transactions funding the allocation or its cash account. It requires each
selected claim to have exactly one source and one full receipt, with matching
amount, distinct chains, and source-before-receipt ordering. There are **11,482
matched claims**. A single deposit can consume many claims; the claim is never
counted again as an independent allocation contribution.

Authentication comes from the existing normalized bridge links, not from
matching amounts or nearby timestamps. `normalize/allocation_bridges.py`
validates the source domain/nonce/messenger, burn payment, receipt emitter,
message body, token, owner, recipient, destination and amount. This run reuses
the reviewed snapshot; it does not claim a new RPC re-extraction of every event.

The scoped ledger contains S34, S39 and those claim accounts. It carries the
weighted-average borrowed basis through their movements. Explicit income and
passive appreciation add no borrowed basis. A withdrawal removes the relevant
fraction of original basis; unrelated debt repayments do not refinance this
allocation. Existing source capital without traced origin remains unclassified.

Per-event snapshots retain only S34/S39 balances to avoid repeating thousands
of empty claim accounts. All claim releases, source witnesses and final account
balances are still retained. This is a reporting-size option, not a change in
the ledger arithmetic.

## Largest previously unresolved closing contribution

The August 31 [Base deposit](https://basescan.org/tx/0x57591b9fe87cdca67248e7fe0dbc11875775b04bd5ef49f1d34623b640e29a26)
consumes **26 claims totaling 25,641,472.293405 USDC** from one
[Ethereum source transaction](https://etherscan.io/tx/0x58466667127ca421556869fb82fd849843bfc29e1be7e6aea63bd5196209286c).
All of those claim amounts have traced Sky origin. They fund:

- **25,639,605.097260** of S34 acquisition capital;
- **1,867.196145** into the Base PSM allocation, outside this position.

The additional **11.989520254210…** recorded in S34's movement is classified
income, excluded from borrowed principal. The full 25.640m S34 contribution
remains at August-end and is now explained.

## Mixed Savings transactions: conservative bound

Previously, the scoped audit refused a new draw attribution whenever a
transaction contained any external-funding operation. That also rejected
transactions which drew Sky funds and merely repaid Savings elsewhere.

The new opt-in `--bound-external-funding` mode uses:

```text
D = net positive Sky draw for the single ilk
X = external cash draws recorded in the normalized transaction
N = sum of all asset changes, excluding explicitly classified income
O = positive capital destinations outside the selected group
G = net new capital entering the selected group

cash payments / execution shortfall = max(0, D + X - N)
new traced Sky contribution = min(G, max(0, D - O - shortfall))
```

Every other destination and payment is charged against Sky first. Adding X is
necessary: an external cash draw could otherwise hide a simultaneous payment
in the net asset change. External principal repayments and interest accrual
amounts are not used to infer the cash payment; changing their provisional
split does not change this result. Negative/unknown external-operation inputs
are rejected. The earlier behavior remains the default for other audits.

For example, the [August 22 source](https://etherscan.io/tx/0x12755464faf7a3579d9fb7526e3cae37a88f8898e2774c8189872fb99fd0b404)
draws **5,327,514.275807** and sends that amount into bridge claims. It also
withdraws approximately **114,856.49** from spUSDT to repay Savings. Instead of
assuming those paths are independent, the bound first deducts the full cash
use from the Sky draw and attributes only the remainder. This establishes a
conservative contribution without endorsing the provisional Savings repayment
policy.

These are conservative contributions under the stated weighted-average policy
and complete, correctly normalized input flows. They are not unique physical
token provenance through fungible balances. No new entitlement, reward rate or
loan-principal interpretation is assumed.

## Remaining work

Residual contributions are ranked by the acquisition capital still held after
subsequent withdrawals. Their sum reconciles to **18,484,532.67**. The largest
single residual now is **872,574.80**, from the
[August 31 Base transaction](https://basescan.org/tx/0x61b40651e07cc89df57c894fb3b475d6b34af7ef85f3817545a56598fd061dc4).
Its [Ethereum source](https://etherscan.io/tx/0xd5c50863e3f627f40f54965c5f82294830c5e7b326adafcaf48181a2aff71aed)
withdraws from Spark's Ethereum USDC Morpho vault (S10), repays ilk debt and
bridges the rest. Following S10's earlier acquisition funding is required;
calling that entire return borrowed principal would also capitalize any gains.

The other residuals and their exact source claims are retained in the artifact.
Across the previously audited large allocations, the next priorities are now
Spark sUSDS/POL (273.64m), Spark spUSDS (97.12m), and Grove JTRSY (84.38m), ahead
of this position's remaining 18.48m.

## Reproduction and tests

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_bridged_allocation_origins.py \
  --history /tmp/pr215-spark-v1-history.jsonl.gz \
  --venue S34 --cash-venue S39 --bound-external-funding \
  --start 2026-01-01 --end 2026-08-31 \
  --output reconciliation/spark_base_morpho_funding_origins_2026_08.json.gz
```

The artifact contains input/code hashes, bridge links, draw witnesses, external
cash bounds and surviving residual contributions. The already-normalized
regression fixture retains 316,812 scoped batches without numerical truncation.
Tests cover complete/unique message links, amount and ordering changes, partial
borrowed funding, gains, competing destinations, unrelated repayments, external
cash masking payments, and independence from provisional repayment splits.
The fixture reproduces the full-history closing capital and borrowing amounts.
