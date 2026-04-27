# Feb 2026 — Reconciliation of Interest & Supply Calculations

Comparison between our Dune dashboard and the [MSC #6 Settlement Summary (Feb 2026)](https://forum.skyeco.com/t/msc-6-settlement-summary-february-2026/27778) forum post for February 2026.

## Numbers

| Metric | MSC #6 post | Our Dune query (APY) | Delta |
|--------|-------------|----------------------|-------|
| Sky revenue (Net Sky Revenue) | 1,948,422 USDS | 1,868,715 USDS | +79,707 (+4.3%) |
| Agent rate (Net Owed to Obex) | 65,719 USDS | 67,628 USDS | -1,909 (-2.9%) |

## Verified inputs

- **SSR:** 4.00% throughout February (set Dec 16, 2025)
- **Borrow rate:** 4.30% (SSR + 0.30%)
- **Subproxy balance:** 21,000,000 on Feb 1 → 21,442,327 from Feb 2 (MSC #4 settlement arrived)
- **Obex demand:** ~579M → ~580.3M after Feb 2 settlement added 1,768,819 to Vat debt

## Demand Side (agent rate on subproxy)

| Method | Result | MSC #6 | Delta |
|--------|--------|--------|-------|
| APR (split balance: 21M×1d + 21.44M×27d) | 65,747 | 65,719 | +28 |
| **APY** (split balance) | **64,557** | 65,719 | **-1,162 (-1.8%)** |

APR with the split balance matches the MSC within rounding (~28 USDS). Same pattern as January — **MSC uses APR at flat 4.00% SSR**. APY gives 1.8% less.

## Supply Side (sky revenue)

| Method | Demand base | Result | MSC #6 | Delta |
|--------|-------------|--------|--------|-------|
| APR | 579M/580.3M (settlement-adjusted) | 1,914,126 | 1,948,422 | -34,296 (-1.8%) |
| **APY** | 579M/580.3M (settlement-adjusted) | **1,876,937** | 1,948,422 | **-71,485 (-3.7%)** |
| APR | Full Vat debt (600M/601.8M) | 1,984,804 | 1,948,422 | +36,382 (+1.9%) |

Neither our utilized USDS nor the full Vat debt reproduces the MSC figure exactly:
- APR on utilized (1,914K) is **1.8% too low**
- APR on full Vat debt (1,985K) is **1.9% too high**
- The MSC-implied average demand at APR is **~591M**, between utilized (~580M) and full debt (~601M)

This pattern matches January, where the MSC-implied demand (~574M) also didn't correspond to our end-of-month Obex demand (579M). The likely explanation is accumulated Vat interest (the actual economic debt = art × rate is higher than the initial frob amounts) or a different demand averaging method.

## Correct demand-side calculation (agent rate)

The correct agent rate is **SSR + 0.20% = 4.20% APY** (not flat SSR). Using APY on the split balance:

```
21M × [(1.042)^(1/365) - 1] × 1  +  21.44M × [(1.042)^(1/365) - 1] × 27  =  67,628
```

The MSC pays 65,719 — **underpaying Obex by 1,909 USDS** for February.

## Findings

### 1. MSC demand side uses APR at flat SSR (two errors)

The MSC uses APR at 4.00% (SSR only): `21M × 4.00% / 365 × 1 + 21.44M × 4.00% / 365 × 27 = 65,747 ≈ 65,719`.

Two discrepancies vs the correct calculation:
- **APR instead of APY** — overstates by ~1.8%
- **SSR (4.00%) instead of agent rate (SSR + 0.20% = 4.20%)** — underpays by ~5%

These partially offset, but the net effect is the MSC **underpays** Obex by 1,909 USDS for February.

### 2. Sky revenue gap persists

The MSC interest (1,948K) is 1.8% higher than our APR on utilized (1,914K) and 3.7% higher than our APY (1,877K). The ~34K APR gap likely comes from the MSC using a slightly higher effective demand than our "Obex demand" figure, possibly reflecting accumulated Vat rate on the ilk art.

### 3. Pattern consistency across months

| Month | MSC demand side | Correct (APY, agent rate) | Net underpayment |
|-------|-----------------|---------------------------|------------------|
| Jan 26 | 71,342 | 73,383 | -2,041 |
| Feb 26 | 65,719 | 67,628 | -1,909 |

The MSC consistently underpays the demand side by using flat SSR (APR) instead of the agent rate (SSR + 0.20%, APY).
