# Spark capital tracing: completed overnight replay

The confirmed-repair replay completed all **451,911 batches** through August
2026. Under the documented proportional Savings-funding assumption, modeled
August costs assigned to named allocations increased from **2,333,198.92 to
5,404,074.78 USDS**. This is improved attribution, not additional revenue or a
change to Spark's bill.

The full numerical reconciliation closes to much less than one cent. It is
**not yet certified funding provenance**: unidentified receipts and the Savings
attribution assumption still qualify the allocations. The strict eligible
subtotal remains zero, and uncertain rows do not gain certified cost, net PnL
or net APY merely because the numerical reconciliation closes.

## August reconciliation

All amounts below are conditional modeled August Sky borrowing costs, in USDS.

| Component | Amount |
|---|---:|
| Named allocations, after their modeled deductions | 5,404,074.784637 |
| PSM inventory outside named allocation rows | 172,850.110308 |
| Sky interest on principal used to pay saver interest | 47,994.600280 |
| CCTP transfers in flight | 1,818.113087 |
| Transaction outflow accounts | 6,190.231388 |
| Rounding accounts | 0.005591 |
| Independently proved historical non-cash Vat accrual | 234,768.881402 |
| Modeled-versus-published deduction adjustment | -156,268.230735 |
| **Global borrowing cost excluding MSC** | **5,711,428.495958** |

The exact, unrounded remainder is approximately **1.7e-20 USDS**. These are
separately measured components, not a residual assigned to an allocation to
force agreement. The unchanged global charge is **6,108,910.339051 USDS**;
MSC-related borrowing costs account for **397,481.843094 USDS**.

The 47,994.60 line is Sky interest on debt that financed earlier saver-interest
payments. It is not the Savings VSR expense itself, and it is not principal in
a live investment. Of the PSM line, 160,929.89 is Arbitrum, 7,424.67 Base,
4,382.31 Optimism, and 113.24 Unichain. The exact per-account and per-lender
amounts are retained in the JSON artifact.

The deduction adjustment preserves the frozen published control's dollar
exemptions. It includes credits outside the named allocations and differences
between their modeled deductions and the original report's scope; no historical
exemption is silently added to the payable charge.

## Execution discrepancies now have a measured financing impact

The monthly Sky cost carried by historical transaction outflow accounts falls
from **3,389,037.31 to 6,190.23 USDS**. Within the new bucket:

- 5,616.182512 has an exact numerical execution witness;
- 543.364966 has an execution witness leaving less than one cent;
- 18.898636 is attached to partly explained transactions;
- 11.785275 is attached to transactions without a matching execution witness.

The last two figures are the costs of those whole transaction accounts, not an
estimate of only their unexplained pieces. They also do not measure uncertainty
from unidentified incoming payments or the funding attribution policy.

The known [900,612.89-USDC BUIDL-interest forwarding transaction](https://etherscan.io/tx/0x0032e26b8e4b284e3c61ea8aeb0870e3f0dbb7d3173945faf0449ca6ec5138e8)
has **zero Sky-funded principal and zero August borrowing cost**. Its historical
cash amount must not be mistaken for missing borrowed capital.

## Gross historical discrepancies

These totals cover repeated turnover from inception through August; they are
not revenue, outstanding debt, August expenses, or settlement adjustments.

| Historical discrepancies | Previous checkpoint | Completed replay |
|---|---:|---:|
| Incoming count | 19,573 | 3,512 |
| Incoming gross value | 17,343,087,257.05 | 74,069,475.24 |
| Outgoing count | 24,441 | 14,546 |
| Outgoing gross value | 16,687,095,203.84 | 3,973,007.99 |

Savings draws/returns, their interest and refinancing, mixed vault deposits,
Paxos conversions, the Ethereum fsUSDS asset, SubProxy reserve earnings,
Anchorage's duplicate-disbursement correction, native withdrawals, verified swap
earnings, Binance's boundary and Ethereum Morpho fee shares are included.
See the individual evidence notes in this directory for their transactions,
contract arithmetic and regression tests.

## Important reporting qualifications

S66 now appears as a provisional allocation instead of remaining outside the
named subtotal. Its average modeled Sky principal is **3,438,052.65 USDS**;
gross financing is **10,687.733211 USDS**, its independently saved idle-USDS
credit is **8,151.092172**, and modeled net cost is **2,536.641039**. The
historical revenue snapshot omits S66. Its extra modeled exemption is offset
in the reconciliation adjustment, preserving the published charge. Revenue and
APYs remain unavailable for this added diagnostic row.

S61 and S62 show negative modeled net costs, **-91,848.20** and **-44,068.71**.
This is a deduction-credit effect, not negative borrowing or a negative Sky
rate. Their average modeled Sky principal is about 70.54m and 35.81m, while
the frozen August report exempts roughly 100.12m and 50.00m respectively through
idle-USDS plus SDE deductions. S61's historical PYUSD SDE treatment differs from
the current September-forward configuration. We deliberately retain the
published August controls; these conditional credit allocations should not be
presented as certified investment APYs.

## Still open

The separate Anchorage run tests **10m principal returned in July and 50m in
August**, with the remainder treated as interest. It is still running at this
checkpoint. The statement/counterparty must confirm that split; this completed
base case leaves both receipts unclassified. Its result will be compared using
the same debt, rates, deductions and source history.

Other principal-versus-income questions are listed in
[remaining-receipt-questions-2026-10-09.md](remaining-receipt-questions-2026-10-09.md).
The independently confirmed Aave reward claim and
[V4 fee witness](v4-fee-witness-2026-10-09.md) were established after these replay
inputs were frozen. They explain specific receipts but are **not yet consumed
by the completed replay**. The V4 witness explains 12,667.024366 of a
12,667.033419 discrepancy; it does not automatically classify other V4 events.

## Validation and reproduction

The funded replay is frozen at `927f849`; the reporting projection adds S66
without replaying transactions. Every existing allocation row, debt movement
and global control was checked unchanged. All **59,557 ledger accounts** passed
origin-sum and non-negative-material-funding checks. Attributed funding for each
lender does not exceed its observed net borrowing; remaining aggregate stock
differences are below 1e-8 USDS. The independently reconstructed Vat event proof
also passes. No published settlement or API data was regenerated.

`reconciliation/spark_overnight_financing_2026_08.json` contains the full
numerical comparison, per-allocation amounts, financing accounts, assumptions,
input hashes and outstanding scenario status.
`reconciliation/spark_overnight_cash_flow_comparison_2026_08.json` separately
checks unchanged global/MSC charges and gross per-ilk draws/repayments. It can be
reproduced from the saved results without another funding replay:

```sh
PYTHONPATH=src PYTHON_DOTENV_DISABLED=1 .venv/bin/python \
  scripts/compare_spark_financing_snapshots.py \
  --before /tmp/pr215-spark-superstate-financing.json.gz \
  --after /tmp/pr215-spark-v1-projected-financing.json.gz \
  --control settlements/spark/2026-08/provenance.json \
  --savings-events tests/fixtures/spark_savings_v2_funding.json.gz \
  --output /tmp/spark-overnight-comparison.json
```

Latest committed-code CI passed both unit and PostgreSQL integration suites.
The compressed-result comparison also completed successfully on these actual
files, and its seven existing unit tests passed. The comparison uses independent
full-precision debt controls; their difference from the rounded published daily
sum is approximately 0.000000531 USDS and is recorded separately.
