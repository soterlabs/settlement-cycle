# Nov–Dec 2025 — Reconciliation of Interest & Supply Calculations

Comparison between our Dune dashboard and the [MSC #4 Settlement Summary (Nov–Dec 2025)](https://forum.skyeco.com/t/msc-4-settlement-summary-november-december-2025-obex/27633) forum post.

## MSC #4 figures

### November 2025

| Metric | MSC #4 post |
|--------|-------------|
| Demand Side | 38,396 USDS |
| Supply Side — Sky Share | 192,592 USDS |
| Supply Side — Obex Share | 42,689 USDS |

### December 2025

| Metric | MSC #4 post |
|--------|-------------|
| Demand Side | 82,684 USDS |
| Supply Side — Sky Share | 1,254,980 USDS |
| Supply Side — Obex Share | 278,558 USDS |

### Totals

- Net Sky Revenue: 1,447,572 USDS (= sum of Sky Shares)
- Net Owed to Obex: 442,327 USDS (= sum of Demand Sides + Obex Shares)
- Settlement debt minted: 1,768,819 USDS (= sum of all Supply Side)

## Verified inputs

### SSR history (onchain — [Dune query 6953056](https://dune.com/queries/6953056))

The SSR was **not constant** during Nov–Dec 2025. It changed multiple times:

| Date | SSR (APY) |
|------|-----------|
| Oct 27, 2025 | 4.50% |
| Nov 7, 2025 | 4.25% |
| Nov 11, 2025 | 4.50% |
| Dec 2, 2025 | 4.25% |
| Dec 16, 2025 | 4.00% |

During the OBEX active period (Nov 17–Dec 31), the applicable SSR was:
- Nov 17–Dec 1: **4.50%**
- Dec 2–15: **4.25%**
- Dec 16–31: **4.00%**

### Subproxy USDS balance ([Dune query 6954383](https://dune.com/queries/6954383))

The subproxy held exactly **21,000,000 USDS** throughout Nov–Dec 2025 (from the Nov 17 suck until the MSC #4 settlement on Feb 2, 2026).

## Demand Side verification

The correct agent rate is **SSR + 0.20%** (see [RULES.md](../../RULES.md)). The MSC posts appear to use flat SSR with APR.

### Our Dune query results (APY, agent rate — correct method)

Agent rate is now computed by a standalone query (`6957966`) that correctly includes Nov 17 (suck day). Previously the PnL query missed Nov 17 because its `cum_debt > 0` filter excluded it (frobs started Nov 18). This was a bug — the subproxy held 21M USDS earning the agent rate from Nov 17.

| Month | Our Dune query | MSC #4 | Delta |
|-------|---------------|--------|-------|
| **Nov (14d)** | **36,997** | 38,396 | **-1,399 (-3.6%)** |
| **Dec (31d)** | **75,589** | 82,684 | **-7,095 (-8.6%)** |

### Manual verification

| Method | Rate | Nov (14d) | Dec (31d) |
|--------|------|-----------|-----------|
| **APY (correct)** | **agent rate (SSR+0.20%)** | **37,073** | **75,648** |
| APY | flat SSR | 35,509 | 72,270 |
| APR | flat SSR | 36,272 | 73,695 |
| — | — | **MSC: 38,396** | **MSC: 82,684** |

Note: The Dune query gives 36,997 vs manual 37,073 for November — the small difference is from the query using exact daily SSR step function vs the simplified single-rate manual calc.

### January (31 days, SSR = 4.00%, agent rate = 4.20%) — for reference

| Method | Rate | Result | MSC #5 | Delta |
|--------|------|--------|--------|-------|
| **APY (correct)** | **4.20% (agent rate)** | **73,383** | 71,342 | **+2,041 (+2.9%)** |
| APR | 4.00% (flat SSR) | 71,342 | 71,342 | 0 (exact match) |

## Findings

### 1. MSC posts use APR at flat SSR — two errors that partially offset

The MSC settlement uses APR (overstates by ~1.8%) at flat SSR without the +0.20% agent rate spread (understates by ~5%). For January, these cancel almost exactly: APR at 4.00% = 71,342 matches the MSC. But the correct value (APY at 4.20%) is 73,383 — the MSC **underpays** Obex by 2,041 USDS.

### 2. Nov–Dec gap partially explained by agent rate

Using the correct agent rate (SSR + 0.20%) with APY and the standalone query (which includes Nov 17) narrows the gap significantly:
- **Nov (14d):** gap narrows from -10.5% (old 13d query) to **-3.6%** (standalone 14d query). The remaining ~1,399 USDS gap is consistent with the ~1.8% APR/APY difference.
- **Dec:** gap narrows from -12.6% (flat SSR) to **-8.6%** (agent rate APY). The residual is larger than expected from APR/APY alone.

The remaining gap (~3-9%) for Nov–Dec is larger than the ~1.8% APR/APY difference seen in Jan-Feb. This suggests MSC #4 may include additional components or use a different methodology for OBEX's first settlement cycle. The Dec gap is notably larger than Nov, which could indicate accumulated interest effects or different day-count conventions.

### 3. SSR was NOT 4% in Nov–Dec 2025 (now fixed in queries)

The SSR was 4.50%/4.25%/4.00% during Nov–Dec. Our Dune queries have been updated with the full SP-BEAM SSR history (all 6 rate change dates).

### 4. Different accounting structure

MSC #4 has a **Supply Side Obex Share** (42,689 Nov / 278,558 Dec) not present in MSC #5 (Jan). The total Supply Side (Sky + Obex shares) equals the settlement debt minted (1,768,819), suggesting it represents the total borrow interest on the full Vat debt, split between Sky and Obex.
