# Grove STAC / JAAA reporting coverage — 2026

This reviewed preview uses the bundled, hash-verified January–September settlement summaries and the exact saved financial/raw-source pair for October. It performs no RPC requests, daily calculations or database writes. It does not deploy or publish this PR.

All 20 elapsed-month venue cells are available. October is MTD through **2026-10-27**, the latest retained source when this audit was prepared; the expected date is October 28, so the API keeps the period marked stale. It does not invent an extra day of revenue.

| Period | STAC prime-retained income (USD) | JAAA prime-retained income (USD) | Source cutoff |
| --- | ---: | ---: | --- |
| 2026-01 | 616387.90 | 933299.18 | 2026-01-31 |
| 2026-02 | 188278.20 | 396689.67 | 2026-02-28 |
| 2026-03 | 127923.80 | 0.00 | 2026-03-31 |
| 2026-04 | 574281.10 | 590469.81 | 2026-04-30 |
| 2026-05 | 453105.30 | 495968.87 | 2026-05-31 |
| 2026-06 | 99799.80 | 385937.82 | 2026-06-30 |
| 2026-07 | 540942.60 | 451019.48 | 2026-07-31 |
| 2026-08 | 469275.10 | 570646.76 | 2026-08-31 |
| 2026-09 | 335575.00 | 346671.49 | 2026-09-30 |
| 2026-10 MTD | 348094.400000 | 448971.1846168581493691352 | 2026-10-27 |

YTD STAC: **3753663.200000 USD**.
YTD JAAA: **4619674.2646168581493691352 USD**.
Combined RWA income: **8373337.4646168581493691352 USD**.

January JAAA retains 933,299.18 of 2,363,169.37 reported gross return; 1,429,870.18 belongs to Sky (a 0.01 displayed rounding bridge). March JAAA reports a genuine zero prime income: the negative gross return is allocated to Sky. Rounded historical ownership percentages are never reapplied to gross amounts.

The summaries changed their per-venue table layout in August. Parsing uses column names, so the inserted time-weighted-value column cannot shift revenue/SDE values.

## Measurement and limits

These amounts are **investment returns**: reported NAV movements and distributions net of capital flows, embedded fund costs and the saved Sky split. They are not contractual loan interest or cash receipts. The Grove statement labels this bucket Investment income from allocations and Net investment income after funding.

Closed summaries retain their published cent precision and disclose USDS at USD par in the source manifest. October retains saved decimal precision. No new principal, asset-value or receivable amounts are invented. Other historical allocation/income categories are outside this slice, so broader prime totals remain partial.

## Frozen October source

- Financial revision: `ef0b80c2e50d23ff44c1bd2a8d8e60f26b048be8f8a57fb94425c464bd8f7754`.
- Raw revision: `8e1ae7902e5093981af7ab1617c4ff6e00a65fd24c839b0c4476a91c3f63d05d`.
- Raw result hash: `12fb53876ba79aedbca6b9f12dab85cad35a7449faea46d05fd374091d202288`.
- Raw source: https://settle-api-production.up.railway.app/v1/revenue/grove/at/2026-10-27?revision=8e1ae7902e5093981af7ab1617c4ff6e00a65fd24c839b0c4476a91c3f63d05d

Fixture: `tests/fixtures/grove_rwa_financial_2026_10.json`. Coverage is enforced in `tests/unit/test_financial_rwa.py`; the PostgreSQL/API test checks exact-revision enrichment, correction replay, disabled sources and read-only operation.

After deployment, normal source publications move the October/current-month cutoff forward automatically. Closed-month RWA income uses a pinned monthly summary when included in the bundle, otherwise the exact month-end saved publication. This PR needs no historical daily backfill or new scheduled job.
