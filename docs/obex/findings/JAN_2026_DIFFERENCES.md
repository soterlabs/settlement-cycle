# Jan 2026 — Reconciliation of Interest & Supply Calculations

Comparison between our Dune dashboard and the [MSC #5 Settlement Summary (Jan 2026)](https://forum.skyeco.com/t/msc-5-settlement-summary-january-2026-spark-and-grove/27709) forum post for January 2026.

## Numbers

| Metric | MSC #5 post | Our Dune query (APY) | Delta |
|--------|-------------|----------------------|-------|
| Sky revenue (Net Sky Revenue) | 2,095,775 USDS | 2,030,086 USDS | +65,689 (+3.2%) |
| Agent rate (Net Owed to Obex) | 71,342 USDS | 73,383 USDS | -2,041 (-2.8%) |

## Root causes

### 1. MSC uses APR, not APY

The MSC post uses simple interest (APR), while our queries use compound interest (APY):

```
APR:  daily_interest = D × rate / 365
APY:  daily_interest = D × [(1 + rate)^(1/365) - 1]        ← correct (onchain accrual)
```

At 4.30%, APR gives a ~2.15% higher daily rate than APY. **APY is the correct method** — it matches how the SSR accrues onchain via per-second compounding. The MSC's use of APR overstates sky revenue.

### 2. Supply rate: SSR vs SSR + 0.20%

The MSC pays the subproxy at the raw **SSR (4.00%)**, not SSR + 0.20% (4.20%) as our model originally assumed.

Verification — APR at 4.00% on 21M idle USDS, 31 days:

```
21,000,000 × 0.04 × 31/365 = 71,342  ← matches MSC exactly (APR)
```

Correct calculation using the agent rate (SSR + 0.20% = 4.20% APY):

```
21,000,000 × [(1.042)^(31/365) - 1] = 73,383  ← correct (agent rate, APY)
```

## Summary

| Parameter | MSC #5 post | Correct | Discrepancy |
|-----------|-------------|---------|-------------|
| Interest formula | APR (`D × rate / 365`) | APY (`D × [(1+rate)^(1/365) - 1]`) | MSC overstates by ~1.8% |
| Borrow rate | 4.30% | 4.30% | None |
| Agent rate (USDS) | 4.00% (SSR only, APR) | 4.20% (SSR + 0.20%, APY) | MSC underpays by ~5% |

The MSC post has **two errors** on the demand side (agent rate):
1. Uses APR instead of APY (overstates by ~1.8%)
2. Uses flat SSR (4.00%) instead of the agent rate SSR + 0.20% (4.20%) — **underpays Obex by ~5%**

These partially offset: APR at 4.00% gives 71,342 while APY at 4.20% gives 73,383. Net effect: the MSC **underpays** Obex by 2,041 USDS for January.
