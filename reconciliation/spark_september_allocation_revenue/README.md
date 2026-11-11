# September Spark allocation revenue refresh

Only monthly allocation API analytics change. No settlement, true-up, daily revenue, borrowing cost, or global reconciliation changes. PR277 accounts for the historical settlement correction separately; PR275 retains October receipts separately.

| Venue | Revenue (USD) | Unchanged borrowing cost (USD) | Gross APY | Net APY |
|---|---:|---:|---:|---:|
| S64 | 1,373,208.000000 | 531,480.545712 | 9.3944% | 5.6664% |
| S70 | 2,400.001564 | 7,406.282193 | 1.1006% | -2.2604% |
| S72 | 3,674.156863 | 355,937.334155 | 0.0342% | -3.2321% |

Evidence is extracted from the pinned PR277 commit identified in evidence.json, including original file hashes, exact native RLUSD receipt logs, and S70/S72 revenue/exposure/dated capital. S70 uses the comprehensive lending replay result. S64 uses the existing published exposure; S70/S72 exposure is validated against retained daily flows. Funding principal is not replaced with NAV.

S64 cash receipts cover eligible RLUSD exposures across the portfolio. This allocation-level cash APY is not a contractual idle-RLUSD rate. S67 accrual stays untouched and is not added to cash receipts.

Run from the repository with PYTHONPATH=src:

```sh
python scripts/refresh_spark_september_allocation_revenue.py --output /tmp/spark-september-revenue-preview.json
```

Add `--publish` with the API database configured only after review. The command refuses a different current API revision, validates the frozen baseline digest, and writes a new immutable monthly analytics revision. It does not update settlement tables. Old API revisions remain queryable.

Review round 1: added repeat-application and field-scope regression checks, and clarified the revenue attribution/denominator labels. Review round 2: independently validate retained exposure and income against dated capital and opening/closing balances.
