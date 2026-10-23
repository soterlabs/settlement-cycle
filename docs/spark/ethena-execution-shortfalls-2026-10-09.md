# Ethena minting and redemption explain 856 historical execution shortfalls

Ethena's Mint and Redeem events identify both the collateral paid by Spark's Ethereum ALM
and the USDe minted to it or burned from it. The complete matching transaction logs independently
confirm those amounts with the actual collateral transfers and zero-address
USDe mints or burns. The audit requires both forms of evidence; it does not infer fees
from an assumed percentage.

Across 856 witnessed transactions through the August 2026 closing pin, the
paid-minus-received differences total **2,224,865.733350 USD**, using the
capital diagnostic's par-stable valuation. All 856 match the corresponding
residuals in the previously completed capital replay exactly. These are
historical execution shortfalls, not August revenue or additional borrowing
costs, and not transfers whose destination remains unknown.

The 216 mints account for 1,051,105.498600 USD; the 640 redemptions account
for 1,173,760.234750 USD.

For example, the [July 17, 2025 mint](https://etherscan.io/tx/0x94ce2896098f7e11f245fb9d1185cdaf0d30bc68928524fab5b1a4878c4fb983)
paid 20,000,000 USDC and received 19,981,978.60 USDe: an 18,021.40 USD shortfall.
The Mint event and both Transfer events agree exactly.

Raw logs are in `tests/fixtures/spark_ethena_execution_events.json.gz`. Reproduce the
read-only audit with `scripts/audit_spark_ethena_execution.py`; passing a completed
`--financing` output also authenticates the residual matches. The measured
transactions and input hashes are in
`reconciliation/spark_ethena_execution_shortfalls_2026_08.json`.

The underlying funding may include earned funds and non-Sky borrowing. This
audit therefore does not assign all the shortfalls to Sky, create a new
investment, or alter reported revenue. The remaining financed amounts still
need to be shown separately from live allocation principal in the reconciliation.
