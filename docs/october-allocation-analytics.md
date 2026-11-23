# October 2026 monthly allocation analytics

Reviewed October inputs for **GET /v3/reporting/allocation-yields** (and the
deprecated `/v1/revenue/{prime}/monthly/2026-10/allocations` alias). This is a
data import with the existing September tooling and methodology. No API, revenue
or settlement code changes, and no published settlement amount is restated.

## Controls: the finalized October close

`reconciliation/monthly-allocation-controls/{prime}/2026-10/provenance.json` are
byte-identical copies of the finalized October close provenance (#275). Their
SHA-256 values equal `output_sha256` in that close's `finalization.json`.
Each control matches the published `prime_msc_preview.json` row: prime-retained
revenue less the net borrowing charge equals `ordinary_supply_net`, and Sky revenue
equals `sky_ordinary_total`. The published summaries and workbooks match the
preview's hashes. October SOFR and DR come from that close; nothing is
recalculated here.

| Prime | SHA-256 (provenance) |
|---|---|
| Spark | `a9ead935…0724` |
| Grove | `21c244d8…a28b` |
| Obex | `4e89d2bb…a212` |
| Osero | `9f8779b0…ff8e` |

## Prime coverage

| Prime | October applicability | Status |
|---|---|---|
| Osero | O1 SparkLend spUSDS, O2 idle USDS, **O3 Gauntlet USDC Prime (new in October)** | Calculated; frozen inputs committed |
| Obex | V1 Maple syrupUSDC | Calculated; frozen inputs committed |
| Grove | 40+ allocation, custody and Basin rows | See Grove section |
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

O3 opens at zero shares and enters on October deposits, so its average principal
($2.94m) is well below its closing principal ($5.00m). Yields use the published
time-weighted position value.

An independent Decimal check recomputes every field from the control's
`venue_breakdown` and the document's cost and exposure: gross revenue, retained
revenue, net PnL, monthly returns and `(1 + r) ** (365 / 31) - 1`. It found no
differences.

## Publication

Use `scripts/publish_monthly_allocations.py --readiness … --yields …` first
without `--publish` and review the preview. Then publish with the production
`DATABASE_URL`. Publication appends content-addressed revisions to
`allocation_monthly_results`, and earlier revisions stay readable. After
publication, verify the public endpoint for each prime.
