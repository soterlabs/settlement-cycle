# Anchorage: August 2026 funding origins above 99%

At August 31, Anchorage principal remains **$208,829,226.694857**. The final
origin classification is:

| Origin | Capital | Share |
|---|---:|---:|
| Original Sky borrowing | $206,103,343.215723 | 98.694683% |
| Identified Savings funding | $743,811.069988 | 0.356181% |
| Still unclassified | $1,982,072.409146 | 0.949136% |
| **Explained origin** | **$206,847,154.285711** | **99.050864%** |

The requested 99% threshold concerns explained origin. Savings cash is not
labelled Sky borrowing and must not acquire Sky borrowing costs merely because
it funded the same allocation. This is acquisition principal at month-end,
not August's average balance or a reconciliation of outstanding ilk debt.

## Evidence added after the direct-draw audit

The earlier receipt audit traced $201,630,012.24 to fresh draws. It left opening
cash and redemption/bridge receipts unclassified. The new audit first replays
the independently scoped USDC/PSM3 origin graph used for S34, then carries its
released borrowed basis into specific Anchorage cash receipts. Anchorage itself
is excluded from that graph, preventing its own future returns from being used
to prove its earlier funding.

Examples:

- [December 16 opening funding](https://etherscan.io/tx/0xeb7812c1bb950c16a4e70e7fb707b79a8cbf7a5e07c743d2decfcb36ad0b11ef):
  **$1,612,956.164806** of the $1,618,055.553588 deposit is now traced to Sky
  borrowing. This includes independently established opening USDC and a CCTP
  return, in addition to that transaction's fresh draw.
- [May 11, existing USDC cash](https://etherscan.io/tx/0xcff55b84b6dd7f5533ce2c2d134cf88fc7f1c1a9ca9fd4b566169013315d96ac):
  no new draw occurred. **$769,919.569820** of the $798,609.897244 payment is
  proven borrowed origin from the opening cash. The rest stays unclassified.
- [May 11, Savings funding](https://etherscan.io/tx/0x8cd38674cf372b788f2ef2ef5020a26177d5ef81a3dbae781fef192d25c55663):
  **$284,722.221876** is identified as Savings USDC funding, with zero Sky
  borrowing attributed to this deposit. A Sky draw elsewhere in the transaction
  does not make this cash Sky-funded. Eight deposits identify $743,811.069988
  of Savings funding in total.

## Accounting and proof controls

- All USDS/DAI conversions and PSM purchases require matching payments,
  burns/mints, amounts and log order. Multiple conversions are handled without
  reusing an output receipt.
- A source vault's redemption is credited only up to its independently released
  borrowed acquisition basis. Passive appreciation does not create borrowing.
- Each CCTP receipt must match a normalized source claim, the authenticated
  source token/owner, recipient and amount, and an unconsumed actual USDC mint.
  The Ethereum transmitter and messenger are fixed to the v1 route. Unsupported
  routes remain unclassified.
- Opening cash comes from the immediately preceding upstream ledger state.
  Each token's ordered transfers must reconcile with normalized cash balances.
- Route payments consume unknown cash first; other payments consume known cash
  first. Existing evidence and newly proved origin are alternative lower bounds
  for the same cash, never amounts to add twice.
- Savings origin is computed separately from actual Take/underlying-transfer
  pairs. Its lower bound plus the borrowing lower bound cannot exceed the
  facility deposit; the complete ledger checks the same invariant after every
  transaction and at each month-end.
- Principal returns still consume proven borrowed principal first. If a return
  exceeds it, identified Savings is consumed before unknown funding. The
  accepted principal-return total remains **$81,575,229.287194**, and no published
  revenue, settlement, API value or borrowing-cost calculation is changed.

Pre-2026 history establishes opening funding only. These research results do
not restate published historical accounting.

## Reproduction

```sh
PYTHONPATH=src:scripts .venv/bin/python scripts/audit_spark_anchorage_upstream.py \
  --facility-history tests/fixtures/spark_anchorage_origin_history.jsonl.gz \
  --source-history tests/fixtures/spark_base_morpho_origin_history.jsonl.gz \
  --source-supplement tests/fixtures/spark_usdc_origin_graph_supplement.jsonl.gz \
  --receipts tests/fixtures/spark_anchorage_draw_receipts.json.gz \
  --bridge-receipts tests/fixtures/spark_usdc_cctp_draw_receipts.json.gz \
  --output reconciliation/spark_anchorage_upstream_origins_2026_08.json.gz
```

The artifact retains monthly classifications, deposit/return ledger entries,
receipt proofs and rejections, prior-cash/source-release witnesses, source claim
metadata and input/code hashes. The original direct-draw artifact is retained.
Tests replay the source graph and facility, reproduce the final figures, check
Savings classification, reject altered source tokens/owners and missing or
duplicate bridge messages, and confirm missing upstream evidence stays unknown.
