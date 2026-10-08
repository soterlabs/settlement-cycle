# Spark Savings: the cash-only funding model misses accrued liabilities

At the August 2026 pins, the four stablecoin Savings vaults report a combined
**657,810,351.892540** of assets outstanding, versus **633,537,394.452171** of
cumulative cash taken minus cash returned. The difference is
**24,272,957.440369**. These are inception-to-cutoff balances, not August
revenue, unpaid interest alone, or additional Sky debt.

| Vault | Cash taken less returned | On-chain assets outstanding | Cumulative VSR accrual |
|---|---:|---:|---:|
| S56 Ethereum USDC | 289,583,904.960568 | 299,265,230.205546 | 9,681,325.511299 |
| S57 Ethereum USDT | 337,806,578.071981 | 350,069,024.842654 | 12,262,447.192651 |
| S59 Ethereum PYUSD | -11,705.263347 | 370.696577 | 12,075.960056 |
| S60 Avalanche USDC | 6,158,616.682969 | 8,475,726.147763 | 2,317,109.468574 |

The PYUSD case is especially clear: cash returned already exceeds cash taken,
yet the vault still has a positive accrued claim. A model that calls every
return principal would invent negative outstanding funding here.

The [pinned SparkVault implementation](https://github.com/sparkdotfi/spark-vaults-v2/blob/51c6d7a1da85944804ba87234f2eac13dba8330e/src/SparkVault.sol#L153)
emits `Drip(chi, diff)` with the exact integer increase in the share liability
at the current supply. `totalAssets()` includes the un-dripped tail;
`assetsOutstanding()` is total assets less actual idle cash, floored at zero.
Deposit/withdraw events change share supply and do not themselves constitute
interest. The audit reconstructs all these quantities in integer token units.

Evidence contains **83,406** Deposit, Withdraw, Drip and Take events from
inception through Ethereum block **25,878,704** and Avalanche block
**94,159,927**, plus pinned RPC state. For every Drip, reconstructed supply and
the preceding/new chi reproduce `diff` exactly. Closing supply and stored chi
match the independent RPC reads. All observed takes go to the configured ALM,
and their totals agree with the separate cash-transfer inventory.

The exact liability identity is:

```
share liability = deposits - withdrawals + VSR accrual + share rounding
assets outstanding = max(share liability - vault idle cash, 0)
```

Combining the independently observed ALM cash inventory with that identity
leaves small additional vault-cash residuals: 0.259115 USDC and 0.414760 USDT.
They are reported without labeling their purpose. Share rounding totals
-0.018336. Neither residual is assigned to Sky borrowing or allocation income.
The liability/cash difference is cumulative accrual plus rounding minus these
other cash residuals; it is not a proposed principal/interest allocation for
individual repayments.

The existing, unused `compute/savings_v2_liability.py` helper uses opening daily
supply times the daily PPS change. That is an approximation when savers deposit
or withdraw during the day. Its comment also contained a non-equivalent
totalAssets rearrangement. The comments are corrected; the helper's behavior
and settlement expense treatment are unchanged. A future funding ledger must
use the event-level liability evidence and preserve source substitution and
token routes documented in `savings-funding-inventory-2026-10-08.md`.

Reproduce without RPC or credentials:

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_spark_savings_liability.py \
  --events tests/fixtures/spark_savings_v2_liability.json.gz \
  --cash-inventory reconciliation/spark_savings_funding_inventory_2026_08.json \
  --output /tmp/spark-savings-liability.json
```

The committed result is `reconciliation/spark_savings_liability_2026_08.json`.
Regression tests reject missing deposits and inconsistent pinned supply, chi
or outstanding liability. This checkpoint adds a validated prerequisite for
the Savings funding model; it does not claim certified cost reconciliation or
change the running Spark replay, published reports, API output or Sky costs.
