# October 2026 monthly allocation analytics

Reviewed October inputs for **GET /v3/reporting/allocation-yields** (and the
deprecated `/v1/revenue/{prime}/monthly/2026-10/allocations` alias). This is a
data import with the existing September tooling and methodology. No API, revenue
or settlement code changes, and no published settlement amount is restated.

## Controls: the finalized October close

`reconciliation/monthly-allocation-controls/{prime}/2026-10/provenance.json` are
byte-identical copies of the finalized October close provenance (#275). Their
SHA-256 values equal `output_sha256` in the committed
`reconciliation/2026-10/finalization.json`, and a test enforces this. Earlier local
finalization runs differ only in MSC instruction adjustments (the Grove partner
rebate and the Spark historical Sky adjustment). Venue revenue, daily rates and
block pins are the same, but those earlier runs are not used.
Each control matches the published `prime_msc_preview.json` row: prime-retained
revenue less the net borrowing charge equals `ordinary_supply_net`, and Sky revenue
equals `sky_ordinary_total`. The published summaries and workbooks match the
preview's hashes. October SOFR and DR come from that close; nothing is
recalculated here.

| Prime | SHA-256 (provenance) |
|---|---|
| Spark | `de6b6b41…cfe9` |
| Grove | `66e50bb0…437d` |
| Obex | `4e89d2bb…a212` |
| Osero | `9f8779b0…ff8e` |

## Prime coverage

| Prime | October applicability | Status |
|---|---|---|
| Osero | O1 SparkLend spUSDS, O2 idle USDS, **O3 Gauntlet USDC Prime (new in October)** | Calculated; frozen inputs committed |
| Obex | V1 Maple syrupUSDC | Calculated; frozen inputs committed |
| Grove | 46 allocation, custody, boundary and Basin rows | Calculated; frozen inputs committed |
| Spark | Savings-funded mixture; Anchorage facility | See Spark section |
| Keel | No allocator ilk and no ALM venues (`config/keel.yaml`); October control has zero venues and zero Sky revenue | Not applicable; nothing to publish |
| Skybase | Same (`config/skybase.yaml`) | Not applicable; nothing to publish |

## Reproduce Osero and Obex

Inputs were captured with HyperSync block resolution at the October close's pins:

```sh
PYTHONPATH=src:scripts .venv/bin/python scripts/validate_allocation_capital.py PRIME \
  --start 2026-10-01 --end 2026-10-31 --capture-only --block-resolver hypersync \
  --provenance reconciliation/monthly-allocation-controls/PRIME/2026-10/provenance.json \
  --output-dir DIR
PYTHONPATH=src:scripts .venv/bin/python scripts/validate_allocation_financing.py \
  --provenance reconciliation/monthly-allocation-controls/PRIME/2026-10/provenance.json \
  --history-dir DIR --capture-idle-only --block-resolver hypersync
```

The normalized history, idle deductions and pin manifest are frozen under
`reconciliation/monthly-allocation-inputs/{osero,obex}/2026-10/`. The readiness
and yield artifacts are calculated from those repository paths:

```sh
PYTHONPATH=src:scripts .venv/bin/python scripts/report_allocation_capital_readiness.py \
  --prime PRIME --month 2026-10 \
  --history reconciliation/monthly-allocation-inputs/PRIME/2026-10/history.jsonl.gz \
  --idle-deductions reconciliation/monthly-allocation-inputs/PRIME/2026-10/idle_deductions.json \
  --settlements-root reconciliation/monthly-allocation-controls \
  --output reconciliation/allocation_capital_readiness_PRIME_2026_10.json.gz \
  --markdown docs/allocation-capital-readiness-PRIME-2026-10.md
PYTHONPATH=src:scripts .venv/bin/python scripts/report_allocation_yields.py --month 2026-10 \
  --settlements-root reconciliation/monthly-allocation-controls \
  --readiness reconciliation/allocation_capital_readiness_PRIME_2026_10.json.gz \
  --output reconciliation/allocation_yields_PRIME_2026_10.json \
  --markdown docs/allocation-yields-PRIME-2026-10.md
```

As in September, Osero and Obex do not use `--apply-capital-adapters`. With the
adapters applied, both October readiness results are identical (checked).
`tests/unit/test_single_prime_allocation_reports.py` recomputes both artifacts
from the frozen inputs. It also checks gross, retained and net revenue against the
October close.

## Osero and Obex results

| Prime / venue | Average exposure | Borrowing cost | Gross APY | Net APY |
|---|---:|---:|---:|---:|
| Osero O3 Gauntlet USDC Prime | $2,935,603.82 | $9,811.37 | 4.34% | 0.32% |
| Osero O1 SparkLend spUSDS | $1,640,124.41 | $3,599.11 | 3.80% | 1.16% |
| Osero O2 idle USDS | $0.40 | $0.00 (exempt) | 0.00% | 0.00% |
| Obex V1 Maple syrupUSDC | $406,437,126.89 | $1,292,281.79 | 5.15% | 1.30% |

The allocation costs are on original principal. They do not sum to each prime's
global charge, and the remainder is not distributed to a venue:

- **Osero:** $13,410.48 against $13,463.65 global, a $53.17 difference.
  September's difference was $14.45.
- **Obex:** $1,292,281.79 against $1,341,558.34 global, a $49,276.55 difference.
  September's difference was $40,427.17. As in September, MSC debt without
  invested proceeds and gains carried in NAV account for the gap.

O3 opens at zero shares. A $1.0m deposit on October 12 was fully withdrawn on
October 13, and $5.0m has been held since October 14. Its average principal
($2.94m) is therefore well below its closing principal ($5.00m). Yields use the
published time-weighted position value ($2.94m).

O2 held only dust (about $0.40 on average, versus zero in September). Its
exposure is positive and its revenue is a true zero, so the existing rules
publish it as `available` with 0.00% APYs rather than `no_positive_exposure`.
The cost is the known exemption ($0.00), not missing data.

An independent Decimal check recomputes every field from the control's
`venue_breakdown` and the document's cost and exposure: gross revenue, retained
revenue, net PnL, monthly returns and `(1 + r) ** (365 / 31) - 1`. It found no
differences.

## Grove

Grove's normalized history (2,128 Ethereum transactions plus Base, Avalanche,
Monad and Plume) is frozen under `reconciliation/monthly-allocation-inputs/grove/2026-10/`.
As in September, readiness uses `--apply-capital-adapters` (executed-spell
capital adapters), and the reproduction test applies them too.

**Double-charge fix.** `normalize/allocation_capital.py` used to add a
tracing-only `E12_PAU` account for the Diamond PAU's Uniswap V3 AUSD/USDC NFT
whenever no venue with that ID existed. From October, `config/grove.yaml`
tracks the same NFT as the configured venue **E44**, at the same pool and
holder. Both accounts therefore carried identical principal: $21.6m on
average, charged $71,360 twice. `with_grove_pau_tracing` now adds `E12_PAU`
only when no configured Uniswap V3 venue already holds that pool at the PAU, so
E44 alone owns the capital. September and earlier analytics had no E44 and are
unaffected. The recapture also moves E44's average principal by $43 and its cost
by $0.14, because collected fees no longer route through two accounts. E15 and
the PAU idle-cash rows change by less than one cent.

| Venue | Allocation | Average exposure | Borrowing cost | Gross APY | Net APY |
|---|---|---:|---:|---:|---:|
| E21 | Galaxy Arch CLO Token (GACLO-1) | $17,907,834.79 | $59,063.64 | 9.76% | 5.60% |
| E42 | Galaxy Warehouse (facility principal; not escrow cash) | $375,250,001.00 | $1,237,509.46 | 6.11% | 2.08% |
| E7 | Securitize STAC | $103,405,568.80 | $327,222.80 | 4.98% | 1.15% |
| E43 | Grove x Steakhouse USDC (Base, ERC-4626) | $316,004,827.20 | $1,037,592.05 | 4.94% | 0.97% |
| E23 | Steakhouse Prime Instant (Base) | $24,174,190.93 | $76,212.45 | 4.84% | 1.03% |
| E19 | Grove x Steakhouse USDC High Yield (Base) | $36,790,814.81 | $119,054.97 | 4.82% | 0.91% |
| E8 | JAAA | $131,081,648.69 | $413,148.65 | 4.75% | 0.95% |
| E20 | JAAA (Avalanche) | $262,568,675.09 | $825,330.90 | 4.75% | 0.96% |
| E4 | Grove x Steakhouse USDC (Morpho 4626) | $14,922,201.28 | $48,941.96 | 4.57% | 0.62% |
| E10 | BUIDL-I (100% Sky-direct) | $114,942,756.08 | $0.00 | 3.80% | 0.00% |
| E9 | JTRSY (100% Sky-direct) | $281,849,182.19 | $0.00 | 3.75% | 0.00% |
| E22 | Apollo ACRDX | $20,599,353.69 | $66,046.02 | 1.69% | -2.08% |
| E12 | Uniswap V3 AUSD/USDC (ALM) | $8,387,500.28 | $27,537.18 | 1.14% | -2.69% |
| E44 | Uniswap V3 AUSD/USDC (Diamond PAU) | $21,615,589.13 | $71,360.14 | 0.23% | -3.60% |
| E15 | USDC raw (ALM idle) | $267,741.94 | $888.13 | 0.00% | -3.84% |
| E27 | USDC raw on Base (ALM idle) | $14,970.74 | $50.01 | 0.00% | -3.86% |

The other 30 rows break down as follows:
- **10** have no principal in the period.
- **4** are dust rows with both yields available (E1, E2, E3, E6). They are
  excluded from the table above because their average exposure is under $1,000.
- **1** is exempt idle USDS.
- **8** are covered by a boundary (the E36 Monad relay; the JTRSY Basin escrow is
  covered by `E_JTRSY_BASIN`).
- **1** is income-only (Agora incentives).
- **4** custody rows have a calculable cost but no revenue line, so their yields are unavailable.
- **2** Basin rows (BUIDL and JTRSY) still await deduction attribution, as in
  September. Their cost stays null.

Known allocation costs total $4,360,264.42 against a $4,435,995.50 global
charge. The $75,731.08 difference includes the two pending Basin rows. It is
reported and not assigned to any venue.

## Publication

Use `scripts/publish_monthly_allocations.py --readiness … --yields …` first
without `--publish` and review the preview. Then publish with the production
`DATABASE_URL`. Publication appends content-addressed revisions to
`allocation_monthly_results`, and earlier revisions stay readable. After
publication, verify the public endpoint for each prime.
