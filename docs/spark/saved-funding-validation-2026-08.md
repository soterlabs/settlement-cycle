# August Spark funding: saved-number validation

Validated on November 2, 2026 without RPC/indexer queries, PostgreSQL access or a historical replay. This checks the existing proportional funding model; it does not independently certify each funding assignment. Settlement and API values are unchanged.

## Checks completed

- Rechecked hashes for the saved report inputs and consistency between the replay snapshot and recorded source hashes.
- Checked all 31 days for 28 configured allocations and six intermediate custody rows.
- Reproduced daily Sky/Savings principal, original-capital differences, average principal, conditional borrowing costs and available gross/net APYs from saved snapshots.
- Checked individual funding amounts are finite and nonnegative; counted unique snapshot accounts once for the Savings source comparison.
- Reused settled daily rates, idle/SDE deductions and the accepted Anchorage loan exposure. No revenue or debt amount was recalculated.

## Results and limits

Borrowing costs reproduce for 27 configured allocations. S66 remains unavailable because its August settlement row is missing. Gross APY is available for 25 allocations and conditional net APY for 24. S53/S55 have no positive published exposure; S45 has an undefined compounded net APY because its calculated period loss exceeds its published exposure. These values remain null.

The 27 conditional costs sum to **$5,278,758.54**. This is the selected allocation subset, not a claim of reconciliation to all Spark borrowing costs. Net APY subtracts Sky borrowing costs only, not Savings VSR expenses or prime-level adjustments.

The 140 unique snapshot accounts contain **98.8396%** of independently measured closing Savings principal. The remaining **$7,607,924.34** is not allocated by this validation: the saved account snapshots omit some custody/financing accounts and do not identify how much of that difference belongs to each. Do not distribute the remainder arbitrarily or treat the coverage percentage as Sky-debt coverage.

| Savings source | Outstanding principal | Captured accounts | Difference |
|---|---:|---:|---:|
| S56 | $297,384,506.47 | $291,390,712.26 | $5,993,794.20 |
| S57 | $349,860,830.26 | $348,494,428.94 | $1,366,401.32 |
| S59 | $0.00 | $0.00 | $0.00 |
| S60 | $8,375,311.75 | $8,127,582.94 | $247,728.81 |

## Conditional allocation numbers

Average principal and costs below are USD for August 2026. These are model outputs, not newly proven funding provenance. The six intermediate custody rows are omitted from this allocation table to avoid presenting them as extra chargeable venues.

| Venue | Average Sky principal | Average Savings principal | Sky borrowing cost |
|---|---:|---:|---:|
| S1 | $700,844,126.33 | $45,480,316.42 | $1,272,441.71 |
| S2 | $3,752,221.11 | $332,484.76 | $11,658.92 |
| S3 | $82,493,692.69 | $242,965,785.51 | $256,138.72 |
| S4 | $210,983,858.71 | $44,224,786.72 | $323,918.95 |
| S5 | $69,152,995.41 | $16,391,105.37 | $214,708.87 |
| S6 | $0.00 | $0.00 | $0.00 |
| S9 | $4.26 | $1.01 | $0.01 |
| S10 | $4,174,640.45 | $54,045.26 | $12,965.00 |
| S11 | $1.31 | $0.28 | $0.00 |
| S65 | $1,488,339.37 | $9,759,478.61 | $4,623.58 |
| S13 | $0.00 | $0.00 | $0.00 |
| S23 | $173,947,171.62 | $36,236,376.37 | $540,042.43 |
| S24 | $0.61 | $0.13 | $0.00 |
| S25 | $2.66 | $0.63 | $0.00 |
| S61 | $71,544,657.88 | $15,837,109.56 | $0.00 |
| S62 | $36,318,058.79 | $7,906,416.96 | $0.00 |
| S66 | $3,440,817.67 | $351,407.48 | Unavailable |
| S27 | $2.93 | $33.66 | $0.01 |
| S28 | $157,362,325.91 | $37,069,563.36 | $488,598.60 |
| S64 | $29,067,748.68 | $1,471,857.90 | $90,380.04 |
| S32 | $568,055,574.28 | $78,480,041.31 | $1,763,675.89 |
| S34 | $96,418,310.91 | $2,055,886.02 | $299,593.82 |
| S39 | $784.54 | $19.71 | $2.44 |
| S45 | $1,836.55 | $190.96 | $5.70 |
| S49 | $819.71 | $94.33 | $2.54 |
| S53 | $399.42 | $107.68 | $1.24 |
| S54 | $0.11 | $0.03 | $0.00 |
| S55 | $23.55 | $1,174.14 | $0.07 |

Machine-readable results and input hashes: `reconciliation/spark_saved_funding_validation_2026_08.json.gz`.

## Next bounded check

Keep this as numerical validation under the accepted proportional model. If exact source-level closure is required, obtain an aggregate balance for excluded custody, financing expenses and losses for the four Savings sources, using existing saved outputs where available. A missing aggregate must stay identified; no full historical origin investigation or replay is initiated by this report.
