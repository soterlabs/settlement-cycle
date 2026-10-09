# Curve sUSDS/USDT: verify the exchange rate before interpreting residuals

This read-only audit independently reconstructs sUSDS pricing for Spark's
swaps through the August 2026 closing block, 25,878,704. It checks **308,171
Drip events and 16 rate changes** against the integer arithmetic in
[SUsds.sol](https://github.com/sky-ecosystem/sdai/blob/dfc7f41cb7599afcb0f0eb1ddaadbf9dd4015dce/src/SUsds.sol).
The opening and closing `chi`, `rho`, `ssr`, and `convertToAssets(1e18)`
values were queried independently from Ethereum. The reconstructed closing
state and quote match exactly.

The pool is `0x00836fe54625be242bcfa286207795405ca4fd10`. Its pinned
`coins()` calls identify sUSDS and USDT. Each accepted `TokenExchange` must
agree with both actual token transfers between Spark's ALM and the pool.
The sUSDS leg is valued using the independently reconstructed end-of-block
quote, with the same one-share precision as the capital extractor.

There are **7,241 verified swap transactions**. Another 596 transactions
have additional or mismatched pool cash flows and are excluded; they are
not assumed to be fees. Comparing the verified exchanges against the
corrected history's transaction residuals identifies:

| Exact residual matches | Transactions | Historical value difference |
|---|---:|---:|
| Paid value exceeds received value | 2,951 | 257,208.478295679602 USD |
| Received value exceeds paid value | 1,034 | 64,960.643811979565 USD |

These are inception-to-August execution value differences, **not August
borrowing costs**. A shortfall may include pool fees and price impact; the
audit does not claim to separate those components. It does not assume that
all paid funds originated from Sky. A match tolerance of 1e-8 USD only
absorbs Decimal arithmetic noise.

The positive differences remain unchanged in the running replay at commit
`546c8ae`: this checkpoint adds evidence, not a retrospective alteration to
that replay or to reported revenue. Only their cash-flow interpretation is
established here. Funding attribution still requires the completed ledger.

Evidence is saved in `tests/fixtures/spark_susds_curve_swaps.json.gz`.
Rate events retain their block/log identity, timestamp, topics and raw data;
the common emitting contract and query filters are recorded once. Both full
source event files' hashes are included. Swap evidence retains the complete
selected token/pool logs. Reconstruct rates and exchange values with:

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_spark_susds_swaps.py \
  --evidence tests/fixtures/spark_susds_curve_swaps.json.gz \
  --output /tmp/spark-susds-swap-audit.json
```

`--residuals` additionally compares an independently saved static residual
file. The measured comparison and both input hashes are committed in
`reconciliation/spark_susds_curve_swaps_2026_08.json`. Tests reject a changed
accrual, closing quote, premature rate change, conflicting log, or missing
cash leg. Repeated identical logs do not double-count swaps.
