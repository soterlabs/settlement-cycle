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
