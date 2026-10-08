# Spark Savings is a separate capital source, not an investment receipt

The largest sampled Spark receipt, May 9, 2026, contains an authenticated
813,365,994.961115 USDT `take()` from the spUSDT Savings V2 vault to the ALM.
The May 20 500m USDT outflow is a plain transfer back to that vault. These are
part of a distinct saver-funded liability cycle; they must not become Sky
borrowing or earned income merely to clear unmatched movements.

Spark's primary contract documentation explains the mechanism:
https://docs.spark.finance/dev/savings/spark-vaults-v2

`take` removes idle underlying liquidity for deployment by the liquidity layer.
Returning cash requires an ordinary token transfer. Neither operation changes
vault shares or their accrued savings-rate liability. `assetsOutstanding()`
measures share-implied assets less the vault's own idle cash. Our existing S56,
S57, S59 and S60 configuration already treats these as liabilities and excludes
them from allocation CoF; the capital replay currently marks category S2
unsupported. Receipt-side funding was therefore left unresolved.

The read-only audit checks every configured stablecoin Savings vault/ALM cash
leg through the August pins. Every incoming cash transfer must match emitted
`Take` amounts by chain, vault, and transaction. Conflicting or missing evidence
fails. No account labels or capital ledger events are changed.

| Vault | Taken into ALM | Returned to vault | Observed vault/tx pairs |
|---|---:|---:|---:|
| S56 Ethereum USDC | 2,971,244,638.899912 | 2,681,660,733.939344 | 5,629 |
| S57 Ethereum USDT | 13,276,235,391.443105 | 12,938,428,813.371124 | 10,901 |
| S59 Ethereum PYUSD | 5,679,997.445027 | 5,691,702.708374 | 113 |
| S60 Avalanche USDC | 713,217,978.696943 | 707,059,362.013974 | 3,882 |

These are historical gross flows, not outstanding debt or monthly revenue.
Returns can include principal and VSR interest; the cash audit does not infer
that split. The difference of the table's two cash columns is not the accrued
saver liability.

Against the **prior pinned replay** (before the current uncertainty refresh),
whole-transaction net cash explains **7,646 receipt residuals ($12.280b)** and
**7,270 outflow residuals ($10.651b)** within one cent. Mixed transactions are
not claimed as wholly explained if their residual also contains other effects.
The new full replay is running; this inventory records its exact input hash so
these old diagnostic counts cannot masquerade as the current replay's result.

## Required ledger work

Saver principal needs an explicit non-Sky funding origin, distinct from income
and from the Sky ilk. Investment movements must retain the Sky/saver/earned
mix. Returning saver principal must retire the correct funding source, including
refinancing between sources, while VSR expense stays separate. It would be
incorrect to mark all takes as revenue or all vault returns as lost Sky capital.
This needs ledger coverage and tests before certifying Spark allocation costs.
No speculative source assignment is implemented by this audit.

Canonical evidence is compressed losslessly in
`tests/fixtures/spark_savings_v2_funding.json.gz` (30,212 logs). Reproduce:

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_spark_savings_funding.py \
  --events tests/fixtures/spark_savings_v2_funding.json.gz \
  --financing /tmp/pr215-resumed-spark/financing.json \
  --summary-only \
  --output /tmp/spark-savings-inventory.json
```

Omit `--summary-only` to retain every flow and matched residual. The output
contains hashes of both inputs. Published settlement/API numbers are untouched.

## Concrete refinancing case: May 18, 2026

Transaction:
https://etherscan.io/tx/0x3267f7a7508ad892778e1afafb965ecb12660d5d0c8e9fcc278210a19d07ccf6

The Spark ilk draws 399,989,732.847945526048219637 USDS. The ALM converts
399,989,732.847945 USDS through DAI/PSM into USDC and sends that USDC to the
spUSDC Savings vault. In the same transaction a saver redeems shares and receives
399,960,824.213788 USDC; the rest is vault liquidity. The sub-micro-USDS difference
remains at the ALM. The normalized replay sees a new Sky draw with no new
investment deposit and consequently retains a 399,989,732.847945 outflow gap.

This is concrete evidence of **Sky financing replacing saver financing**, not
an unidentified new asset purchase or an execution loss. The allocation ledger
must retain the earlier saver-funded investments and replace their funding
origin when saver capital is returned. Otherwise it cannot assign this Sky
borrowing to the investments it now finances. The proper split also needs VSR
liability accrual; treating every vault return as principal by fiat would conceal
the interest expense. Full transaction logs are retained in
`tests/fixtures/spark_savings_refinancing_may18.json`.

## Separate funding legs can coexist inside one transaction

The May 2 [execution](https://etherscan.io/tx/0xe087909f67a0f7ba0f093e0eac21b2a7bc573ff4cc4bdc8cee780e0a0667b7f7)
at block 25,007,787 has two distinct cash sources:

1. The Savings USDT vault transfers **180,000,802.451341 USDT** to the ALM,
   authenticated by its `Take` event. That money is saver funding.
2. Separately, a Sky draw sends **180,000,066.289061952380736771 USDS** from the
   allocator buffer to the ALM. The ALM starts and ends with zero USDS, and
   sends exactly that newly received amount to sUSDS in an ERC4626 deposit.
   This is a directly observed Sky-funded investment.

The complete receipt and original normalized batch are retained in
`tests/fixtures/spark_distinct_saver_and_sky_funding.json`. The regression proof
checks the zero USDS opening/closing balance, sole buffer→ALM funding leg,
sole ALM→sUSDS payment, actual Deposit amount, corresponding share mint, and
independent USDT `Take`/transfer. The tiny excess precision in normalized Vat
debt versus minted token units is below 1e-18 USDS.

A single transaction-wide proportional clearing account cannot recover this
proven separation while the Savings receipt is unresolved. It can assign part
of the new Sky borrowing to the USDT receipt, although the actual loan cash
went entirely to sUSDS. A Savings fix therefore needs **both** funding-source
accounting and preservation of independently proven token routes inside a
transaction; marking every `take` as income is not a substitute.

This observation does not implement a new source-splitting policy or claim a
reconciliation improvement. It narrows the required model and adds a concrete
acceptance case. More complicated executions can deposit the new USDS into
sUSDS and immediately spend some sUSDS in a Curve swap; those require following
the actual swap too, rather than treating the net sUSDS change as the draw.

## Implemented checkpoint: preserve the May 2 independent routes

`compute/spark_separate_savings_routes.py` now separates this exact execution
into two disjoint clearing groups before replay. The Sky group contains the
zero-net USDS cash account and the sUSDS deposit, with the original draw and
ilk. The other group retains USDT and the Morpho withdrawal/fee movements, with
no Sky draw. All original movements, values, fees and debt fields are preserved.
The adapter rejects altered debt, a different USDS/deposit shape, or a partial
already-split snapshot; it is idempotent and scoped to Spark.

An isolated replay of the saved transaction demonstrates the original bug:

| Sky borrowed basis attributable to this transaction | Before | After |
|---|---:|---:|
| sUSDS | 89,995,087.893178 | 180,000,066.289062 |
| USDT | 90,004,978.395884 | 0 |

The saver-side receipt remains unresolved and its uncertainty remains on that
branch. No saver funding is labeled revenue. No new loan is seeded from the
opening balances. This is a witnessed routing correction, not a general
principal/interest policy or a claim that the full Spark portfolio is traced.
The May 2 receipt and direct token-route proof already existed; this checkpoint
implements the corresponding split. It supersedes the earlier statement above
that this route is not yet handled.

The isolated before/after results and hashes are in
`reconciliation/spark_separate_savings_routes_2026_05.json`. The full August
borrowing-cost effect has not been measured for this additional fix. Published
reports, API revenues and global debt/costs are unchanged.

## May 18 prerequisite still awaiting a policy decision

A cash return to a Savings vault does not label principal versus interest.
The operator has been asked whether to use proportional attribution against
outstanding principal and accrued interest, with funding replacement weighted
across affected allocations, versus interest-first or principal-first.
Interest financing must remain outside allocation principal whichever policy
is chosen. No repayment policy has been selected by silence or implemented.

Independent inception-to-May-18 checks show S56 net cash taken before the
execution of **938,679,610.080881 USDC** and cumulative emitted VSR accrual of
**6,565,509.002915 USDC** before that block. These are cash/accrual totals, not
an asserted unpaid-principal/unpaid-interest split: earlier returns and the
transaction's own Drip must be accounted for. The actual vault return in the
May 18 execution is **399,989,732.847945 USDC**.
