# Grove October 2026 close — pending Distribution Rewards

**Incomplete settlement.** The on-chain calculation completed for October 1–31, 2026, for Grove only. October Distribution Rewards (DR) are unavailable in the latest upstream workbook. No canonical settlement summary or workbook has been produced.

## Calculated components

Amounts in USD; displayed values are rounded independently.

| Component | Amount |
|---|---:|
| Venue revenue to Grove (before borrowing costs) | 4,374,650.15 |
| Agent rate | 87,849.81 |
| Chronicle Points | 12,439.99 |
| Borrowing costs charged to Grove | 4,435,995.50 |
| Calculated net before October DR | 38,944.45 |
| October Distribution Rewards | **Pending — not zero** |

The final net settlement is **$38,944.45 plus October DR**, subject to validating that missing input. Borrowing costs are the canonical net Sky claim ($5,682,959.32) minus Sky direct-exposure revenue ($1,246,963.82), following the existing settlement summary and payment-bridge convention. There are no configured October Grove historical adjustments.

## Sources and validation

- Settlement code: main `09e0f37`, with the October official reference snapshot in this PR.
- DR submodule: `5695d82e54777ec5016c6197b8b55b6b5ca2d415`, latest merged main when checked; includes upstream PRs #29–#33. Its exclusive scan cutoff is still `2026-10-01`, and its workbook has no `2026-10` column.
- Official SOFR: [NY Fed series via FRED](https://fred.stlouisfed.org/data/SOFR), retrieved November 6, 2026. All 21 required business-day observations are captured in `config/october_2026_official_reference_rates.json`; the calendar validates coverage for all 31 days. October 31 is a Saturday and correctly carries the October 30 observation.
- All 31 daily borrowing-cost rows passed the canonical `validate_interest` reconciliation.
- The calculation processed Grove’s configured venues. Reporting-only revenue is excluded from MSC.
- The configured Plume RPC rejected `eth_call` with HTTP 400 / JSON-RPC `-32601`. The [documented public Plume RPC](https://docs.plume.org/plume/developers/how-to-guides/how-to-connect-to-network/install-and-configure-wallet) served the historical read and was used for Plume. The original credentials file was unchanged.

### Block boundaries

| Chain | September 30 closing block | October 31 closing block |
|---|---:|---:|
| avalanche_c | 96488899 | 99082938 |
| base | 52011726 | 53350926 |
| ethereum | 26093737 | 26316188 |
| monad | 109459390 | 118321696 |
| plume | 96561570 | 102924353 |

## Complete the close

Identify or publish the October DR source and update the submodule or finalized DR snapshot. The guard must then pass before writing the canonical artifacts. Load the existing sibling `.env` into the process before running:

```sh
python scripts/run_october_grove_close.py --check-inputs
PLUME_RPC=https://rpc.plume.org python scripts/run_october_grove_close.py
```

The runner is fixed to Grove and October 2026. It validates the saved SOFR snapshot, requires October DR, recomputes using configured sources, reconciles borrowing costs, and writes only `settlements/grove/2026-10/`.

## Main venue revenue comparison

This compares revenue retained by Grove **before borrowing costs**, after any Sky direct-exposure split. August and September are the published reports on main at `09e0f37`; October is this PR’s completed on-chain calculation. These are report comparisons, not historical restatements. DR is separate from all figures in this table.

| Venue | August 2026 | September 2026 | October 2026 | October vs September |
|---|---:|---:|---:|---:|
| Galaxy Warehouse (E42) | $474,488.89 | $1,658,948.54 | $1,894,876.53 | $+235,927.99 |
| JAAA — Ethereum + Avalanche (E8/E20) | $1,713,705.05 | $1,041,086.58 | $1,556,104.30 | $+515,017.72 |
| STAC (E7) | $469,275.10 | $335,575.00 | $427,367.20 | $+91,792.20 |
| Galaxy ARCH CLO (E21) | $222,936.27 | $172,167.34 | $142,165.55 | $-30,001.79 |
| Base Steakhouse vaults (E19/E23) | $1,047,130.65 | $992,580.79 | $259,849.22 | $-732,731.57 |
| Agora AUSD incentives (E38) | $857,964.00 | $665,172.00 | $0.00 | $-665,172.00 |
| Apollo ACRDX (E22) | $113,847.70 | $-104,438.28 | $29,385.80 | $+133,824.08 |
| Ethereum Steakhouse USDC (E4) | $7,322.92 | $22,298.28 | $56,787.83 | $+34,489.55 |

Galaxy Warehouse grew **14.2%**, while Galaxy ARCH CLO fell **17.4%**. Their combined October revenue rose to **$2,037,042.08**, up **$205,926.20** from September. They do not explain the decline in the overall settlement. Galaxy venue revenue is recognized from qualifying cash receipts during each report month; this does not establish the underlying interest accrual month.

The two largest negative changes were:

- **Agora incentives: −$665,172.00.** No qualifying October cash distributions were recorded from the configured payers. This shows zero recognized receipts, not evidence that incentives permanently stopped or that no receivable exists.
- **Base Steakhouse vaults: −$732,731.57.** Combined time-weighted principal fell from approximately **$282.73m** in September to **$60.97m** in October. Both venues were fully exited by October 31, versus a combined opening balance of approximately **$269.09m**. Lower deployed exposure explains much of the revenue decline; the table does not isolate changes in lending rates.

Higher JAAA revenue (**+$515,017.72**), Warehouse (**+$235,927.99**), Apollo (**+$133,824.08**), and STAC (**+$91,792.20**) partially offset those declines.

### Effect on the settlement

| Component | September 2026 | October 2026 | Change |
|---|---:|---:|---:|
| Venue revenue to Grove before borrowing costs | $4,796,207.62 | $4,374,650.15 | −$421,557.47 |
| Borrowing costs charged to Grove | $3,754,809.06 | $4,435,995.50 | +$681,186.44 |
| Supply-side revenue after borrowing costs | $1,041,398.56 | −$61,345.35 | −$1,102,743.91 |
| Agent rate + Chronicle Points | $90,968.45 | $100,289.80 | +$9,321.35 |
| Net before DR | $1,132,367.01 | $38,944.45 | −$1,093,422.56 |

The main settlement change combines **8.8% less venue revenue** with **18.1% higher borrowing costs**. The earlier headline’s $4.37m should be read as venue revenue before borrowing costs; the repository’s formal supply-side headline is after those costs.

JTRSY (E9) and BUIDL (E10) also earned **$882,421.68** and **$364,542.14** respectively in October, but their revenue is directed to Sky under the configured SDE split; neither contributes venue revenue retained by Grove.

Sources: [August report](../../settlements/grove/2026-08/summary.md), [September report](../../settlements/grove/2026-09/summary.md), October calculation and block pins above. Values are rounded independently.
