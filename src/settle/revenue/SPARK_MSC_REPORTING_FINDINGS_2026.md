# Spark: MSC to reporting findings — 2026 snapshot

Recorded 6 November 2026, with an API cutoff of 31 October 2026. This replaces the dashboard’s live “MSC to reporting” card. This is a static diagnostic snapshot, not an additional financial statement or a certification of the figures. Reporting publications were being replayed when captured; refresh the evidence before using these figures for a close.

## Comparison

Amounts are USD, rounded to whole dollars. `*` means a known component sum with missing inputs; it is not a complete amount. `TBD` is unavailable, not zero. Differences are reporting minus the adjusted MSC comparator. The API’s `MSC_supply_less_Savings` and `MSC_total_less_Savings` controls apply the separately calculated Savings adjustment; the signed adjustment can include negative liability revaluation. Do not interpret it as a fixed positive interest charge.

| Month | MSC supply after Savings adjustment | Reporting net interest | Difference | MSC total after Savings adjustment | Reporting net revenue | Difference |
|---|---:|---:|---:|---:|---:|---:|
| 2026-01 | 2,332,446 | 5,679,178 * | 3,346,731 * | 3,579,324 | 2,615,368 * | -963,956 * |
| 2026-02 | 4,879,917 | 5,318,694 * | 438,777 * | 6,004,003 | 3,319,816 * | -2,684,187 * |
| 2026-03 | -2,230,734 | -1,167,811 * | 1,062,923 * | -702,996 | 2,272,278 * | 2,975,274 * |
| 2026-04 | -1,985,521 | -827,018 * | 1,158,503 * | -365,957 | 426,965 * | 792,922 * |
| 2026-05 | 18,805,497 | 20,499,230 * | 1,693,733 * | 20,553,619 | 1,482,157 * | -19,071,462 * |
| 2026-06 | 30,481,304 | 31,329,592 * | 848,288 * | 32,244,661 | 1,917,101 * | -30,327,561 * |
| 2026-07 | -12,677,224 | -12,285,327 | 391,897 | -11,602,458 | 2,530,196 | 14,132,655 |
| 2026-08 | -21,051,594 | -19,879,150 | 1,172,444 | -20,211,914 | 5,499,363 * | 25,711,277 * |
| 2026-09 | -5,652,266 | -4,055,117 * | 1,597,149 * | -4,842,915 | 1,795,834 * | 6,638,749 * |
| 2026-10 | TBD | 9,013,403 | TBD | TBD | 1,195,045 * | TBD |

## Findings and accounting decisions

| Item | Why reporting differs / treatment |
|---|---|
| PSM3 and sUSDS | Include sUSDS share-value growth and non-venue SSR credits; retain Savings-funded asset yield gross. Expense Savings liabilities separately. Principal is excluded from revenue. |
| PYUSD / RLUSD / USDG | Issuer accruals follow the rules in [ACCOUNTING_RULES.md](ACCOUNTING_RULES.md). MSC uses cash receipts for relevant streams. Raw issuer tokens at the ALM are allocations, not cash; token receipts do not become cash inflows until conversion. Avoid counting both accrual and receipt as income. |
| Anchorage | Loan principal returned is investing, not interest. Receipt attribution and escrow funding must be supported by transaction evidence. Do not infer unsupported loan accrual terms. |
| SparkLend fees | Recognize stable and non-stable reserve sweeps once at receipt value; later price changes belong in gains. The September MSC catch-up for earlier fees is not additional reporting income. Legacy spsDAI valuation remains a known gap. |
| MORPHO / AVAX and other non-stable assets | Reward income at receipt value, subsequent remeasurement through gains. Sales realize a previously tracked asset and must not create a second reward income entry. Non-stable gains deliberately extend MSC coverage. |
| Savings | VSR interest and liability revaluation are separate funding expense components. Keep depositor-liability revaluation out of gross SparkLend reserve gains. MSC does not have the same Savings funding perimeter. |
| Sky direct exposure | Use effective-dated designation and actual ownership, not pool name. The historical Curve USDT leg and designated Uniswap v4 USDT leg qualify in their applicable periods; PYUSD/USDS is not SDE. Spark-funded seeding must not be blindly netted out. |
| Missing inputs | Incomplete fee, AMM, ownership or reward inputs keep totals provisional. October DR remains unavailable. A zero arithmetic residual can coexist with incorrect or missing economic inputs. |
| Expenses | MSC comparison stops at net revenue; compensation and other operating expenses are not included. SPK compensation awaits approved expense-sheet values and must be added once. |

## Evidence and reproducibility

- API: `/v3/reporting/spark/msc-reconciliation?year=2026&cutoff=2026-10-31`
- [Replay this exact API selection](https://settle-api-production.up.railway.app/v3/reporting/spark/msc-reconciliation?year=2026&revision=eyJhc19vZiI6IjIwMjYtMTAtMzEiLCJleGFjdCI6dHJ1ZSwiZXhwZW5zZV9pbnB1dF9yZXZpc2lvbiI6IjNmYzg3NzFhZDQ1YWJlZTMzY2UyN2YyZTUwYzE4OGYzNjhiNjdkNDM2NjFmNDliN2Y0YjhiODYxZTMxNjc3NGMiLCJtb250aGx5X3JldmlzaW9ucyI6WyI1MGM3ZTVkMWM2YTcwMjQ1MjljNzJmZDRmNDg4ZmRmYWU4ODJiNDc5MDMwZGNlM2VhZTAzZTQyNzJjMmQ3Y2YwIiwiN2RkYmI3OTE2NDAzNzIxNGZkOWE4ZDY4MGUxMzM5YzBmNWZlNTBiMTg3MzdhNDEwMGQxYTA5OTNkYzVlODU0ZSIsImU3MTdmYWVkYmUyZmQ0NDYzMzFhMjQ1OTA3MDNhYWYxZjQ4NTgwNjFjMzI3MDVhODAyOTUyMjIwMzE5Mjk4MTEiLCI3NDEwYjQxYWRkODM5M2Y0OTk0ZDI3OTg1NTkzZGFjZjlhNWYwMTY2MGRlM2E0ZjBhMTQ0MWY0NTMxNmJkNTYxIiwiZTQwMDNjZTgwYWE3OGU2YjJmMDI5N2E1MDFhYWVmZjE4MDNhNmIxM2ZjMTNmZWRlMjJiNjA5YjhkMDViZTgwNSIsImExNzIwNzMwOWRjMzQxNDU0NWE5ZDk2NTJlMTQ4MjQ4M2Q2MjZkZjBiNzhlMzE4ODYxZWQ5ZmY5NzUzZGI5ZWYiLCIzMGUyZDg0MTdhOGIwNGZmNWNjMDYyOWU0ZjFiMmQzNzM5OWE0YTFiNzYwOWU4NGFkYzJjOTU5ZTQ1ZWMzYTU2IiwiMDJhYTQwMjk5NjNjYTA0MWZhMTU1MTZmNDgzNzNkNzgwZjI5NzIxYjMwOWE0ZGNkYjFkNTQ0YWFlZmMyZjg0MiIsIjEwMWEwNWRkNDJlZTkwNmNiYTNmY2ZjYTJjM2JhNjg2YTE1Njg5OTQ3ZDg0Y2M4ZTQ0YzY0NTg3NTA2NmYwZDYiLG51bGwsbnVsbCxudWxsXSwib2ZmY2hhaW5fcmV2aXNpb25zIjpbIjE3NGNkZGFkMWI1MjVmNjQ4Y2Y2MjQ3MGQ4N2JiYTRmZmI1MDRkMzRmYTE0NWM1OGRlZDFlNDc3YmVhNmI4NzAiLCI0MTI3MjAzYWM4MDY4ODhlOGM3YmQzMGY2Y2MyMTk5YWQ3MzA0NmE1OTBiOTg4Y2MyYjRhNTFkMjlmMjczYjQyIiwiZTRmM2NkM2Y3YTA2ZGNhOGU2NTg4MDRiMmMyYTI4YjE5ZjJlNWMwMjNmMjljNTRjMWQ3NzA3NWY2OTI5YTllOCIsIjVjYTc2Mzk2ZTVkYzg0YjYwYjQ0OWQxMThiODFkYzBmOWUwMzJjZmZjMGNiMzQ0MzBjNDNiZThjOWZiNGUyZTIiLCJmM2JkYzFiOGM1MTA4NzMxZjc2MTM3NjhkZGQ3MDZmMjIxZTVjMjMxMDlkNmVjZTVlYjgyNDE4NGI2YzAyYzFjIiwiYjA4YzY0OGNiYmQ1NGFjMGU4YTU2YTA5OTcwYTE5NDM1ZGFkZGJhMjllMWIyZDc5MWQ3ZDllZTU1MWUwN2UxNiIsImYxODJmYzE4YTljMmIxMTVjOTVmYTI5NTM1YTkwZjdlZjBjZTAyYTNmNmM2OGQ1MGNkODllMzkxNGY4YzI3Y2UiLCIwY2Q5MzRmYzBiYzcwZTJiOGQ2MTM2ODkxMmRlNjhlOWE2MzJmNDZkMzAzY2I1YjhmMDJmNGRiZDcxYzAxNjQ2IiwiYmQ0NzZjOWU4OWJmYWZhYTM1ODYxYjA2YTdiOTBlNTEzNWNhYzIwODhhODE2ZmI0NGNhZTgxYWYzZjMwNjg2MyIsImM3NjYxZTRhY2M3YjZkY2U1ZjE2NjMwZjlmMDZjODUzNDQ1OTYzNWJhMjZiZGI2ZDJkM2MwNTYxYmJmMTA2YzQiLG51bGwsbnVsbF0sInBvbGljeV92ZXJzaW9uIjoicHJpbWUtbWFuYWdlbWVudC5zcGFyay1yZWNvbmNpbGVkLnYzLjEwIiwicHJpbWUiOiJzcGFyayIsInJldmlzaW9ucyI6W251bGwsbnVsbCxudWxsLG51bGwsbnVsbCxudWxsLG51bGwsbnVsbCwiZGVmNzkyM2ZhYTIxMzM2MDAwODE2Y2U4ZjFiNGM4OTI4MTRiNTAwNTA1ZjQ2MGNiNDYyNjc3YWM0YjczOTNmMyIsIjJkOGY3OTgxMGFjYjExZWI5ODA1Mzk1MmM0NGMzZDY5YThjODVjZGE0MmQyMzEwMGRhZDBmMTZiOTg1ZTE2YmIiLG51bGwsbnVsbF0sInllYXIiOjIwMjZ9). The unpinned cutoff URL above will change as sources are replayed.
- Statement report hash: `3b8bbf4920c76f62000d698f4f9fe40c9449326b3522a95fe7adb8ae37b3f48e`
- Response SHA-256 (exact JSON bytes): `bb521ec6bf9434805395ee661d4c696fbe2ed29a89a83f13ef742e9051add941`
- Full accounting policy: [ACCOUNTING_RULES.md](ACCOUNTING_RULES.md).
- Implementation: [financial_spark_reconciled.py](financial_spark_reconciled.py). The API and source evidence remain available even though the dashboard card is removed.

| Month | Published MSC summary source | Reporting source revision |
|---|---|---|
| 2026-01 | [summary.md](https://github.com/soterlabs/settlement-reports/blob/66854e865be5d6972a975a5069de56a3c83c2704/reports/spark/2026-01/summary.md) | `174cddad1b525f648cf62470d87bba4ffb504d34fa145c58ded1e477bea6b870`<br>`50c7e5d1c6a7024529c72fd4f488fdfae882b479030dce3eae03e4272c2d7cf0` |
| 2026-02 | [summary.md](https://github.com/soterlabs/settlement-reports/blob/66854e865be5d6972a975a5069de56a3c83c2704/reports/spark/2026-02/summary.md) | `4127203ac806888e8c7bd30f6cc2199ad73046a590b988cc2b4a51d29f273b42`<br>`7ddbb79164037214fd9a8d680e1339c0f5fe50b18737a4100d1a0993dc5e854e` |
| 2026-03 | [summary.md](https://github.com/soterlabs/settlement-reports/blob/66854e865be5d6972a975a5069de56a3c83c2704/reports/spark/2026-03/summary.md) | `3f8832809673c65ffd031c85f37b96af3d800d686fb40c8f89025c696eee36fc`<br>`e4f3cd3f7a06dca8e658804b2c2a28b19f2e5c023f29c54c1d77075f6929a9e8`<br>`e717faedbe2fd446331a24590703aaf1f4858061c32705a80295222031929811` |
| 2026-04 | [summary.md](https://github.com/soterlabs/settlement-reports/blob/66854e865be5d6972a975a5069de56a3c83c2704/reports/spark/2026-04/summary.md) | `1261e3c1fd79ecf0e0dc510a0c42fbc2174d0e8209edf0e8347843964d6f1f35`<br>`5ca76396e5dc84b60b449d118b81dc0f9e032cffc0cb34430c43be8c9fb4e2e2`<br>`7410b41add8393f4994d27985593dacf9a5f01660de3a4f0a1441f45316bd561` |
| 2026-05 | [summary.md](https://github.com/soterlabs/settlement-reports/blob/66854e865be5d6972a975a5069de56a3c83c2704/reports/spark/2026-05/summary.md) | `e4003ce80aa78e6b2f0297a501aaeff1803a6b13fc13fede22b609b8d05be805`<br>`f3bdc1b8c5108731f7613768ddd706f221e5c23109d6ece5eb824184b6c02c1c` |
| 2026-06 | [summary.md](https://github.com/soterlabs/settlement-reports/blob/66854e865be5d6972a975a5069de56a3c83c2704/reports/spark/2026-06/summary.md) | `a17207309dc3414545a9d9652e1482483d626df0b78e318861ed9ff9753db9ef`<br>`b08c648cbbd54ac0e8a56a09970a19435daddba29e1b2d791d7d9ee551e07e16` |
| 2026-07 | [summary.md](https://github.com/soterlabs/settlement-reports/blob/66854e865be5d6972a975a5069de56a3c83c2704/reports/spark/2026-07/summary.md) | `30e2d8417a8b04ff5cc0629e4f1b2d37399a4a1b7609e84adc2c959e45ec3a56`<br>`f182fc18a9c2b115c95fa29535a90f7ef0ce02a3f6c68d50cd89e3914f8c27ce` |
| 2026-08 | [summary.md](https://github.com/soterlabs/settlement-reports/blob/66854e865be5d6972a975a5069de56a3c83c2704/reports/spark/2026-08/summary.md) | `02aa4029963ca041fa15516f48373d780f29721b309a4dcdb1d544aaefc2f842`<br>`0cd934fc0bc70e2b8d61368912de68e9a632f46d303cb5b8f02f4dbd71c01646` |
| 2026-09 | [summary.md](https://github.com/soterlabs/settlement-reports/blob/66854e865be5d6972a975a5069de56a3c83c2704/reports/spark/2026-09/summary.md) | `101a05dd42ee906cba3fcfca2c3ba686a15689947d84cc8e44c645875066f0d6`<br>`bd476c9e89bfafaa35861b06a7b90e5135cac2088a816fb44cae81af3f306863`<br>`def7923faa21336000816ce8f1b4c892814b500505f460cb462677ac4b7393f3` |
| 2026-10 | Not published | `2d8f79810acb11eb98053952c44c3d69a8c85cda42d23100dad0f16b985e16bb`<br>`c7661e4acc7b6dce5f16630f9f06c8534459635ba26bdb6d2d3c0561bbf106c4` |
