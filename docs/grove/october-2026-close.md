# Grove October 2026 close — pending Distribution Rewards

**Incomplete settlement.** The on-chain calculation completed for October 1–31, 2026, for Grove only. October Distribution Rewards (DR) are unavailable in the latest upstream workbook. No canonical settlement summary or workbook has been produced.

## Calculated components

Amounts in USD; displayed values are rounded independently.

| Component | Amount |
|---|---:|
| Supply-side revenue to Grove | 4,374,650.15 |
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
- 37 configured Grove venues were processed: 33 settlement venues and four reporting-only venues.
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
