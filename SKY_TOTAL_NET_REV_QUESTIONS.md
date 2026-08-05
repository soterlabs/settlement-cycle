# Sky Total Net Revenue — open questions per month (vs BA Labs dashboard)

*Maintained alongside `settlements/sky_total/`. Each month compares our
paid-basis Sky Net Revenue, restated on BA's P&L perimeter
(`SNR + CC-genesis + TGE penalty + capital-seeding add-backs`), against the
BA dashboard net (`sky.data.blockanalitica.com/v1/accounting/profit-and-loss/`).
Status as of 2026-08-05, after PR #168's paid-basis restatement.*

**Confirmed rules (no longer questions):**

- *Recognition*: month M books the incomes/expenses that happen during M;
  Sky receives payment for cycle M−1 during M. BA's per-prime
  `Revenue > Primes` item = settlement executed in M: `mint − subproxy
  transfer + demand-side total` (as-settled Sky Share incl. reconciliation
  corrections). Verified exact to the dollar Feb–Jul.
- *Capital seedings* (Skybase $10M @ MSC#4, Keel + Osero/PRYSM $10M each
  @ MSC#6) are NOT in BA's net revenue — they sit below the line, hitting
  only "remitted to Sky reserves". Verified: absent from BA's revenue,
  expense, and revenue_distribution groups.
- *DSB transfers* are Operating expenses (BA classification, adopted by us
  2026-08-05).
- *Step 1 Capital* = 20% of the cycle month's net revenue, split evenly
  Core Council / Fortification, riding the CC Buffer transfer; the paid
  figures are in each MSC post's BA capital-allocations section (now in
  `config/sky_total.yaml → cc_step1_paid`). Aligned Delegates (1%) and GAR
  (0.5% + 0.5%) are separate below-the-line allocations.

| Month | ours (BA basis) | BA net | Δ | Status |
|---|---:|---:|---:|---|
| 2026-01 | 9,218,714 | 9,325,455 | −106,741 | open |
| 2026-02 | 33,141,443 | 32,710,465 | +430,978 | open |
| 2026-03 | 21,840,905 | 18,961,121 | +2,879,784 | open |
| 2026-04 | 12,488,503 | 11,193,418 | +1,295,085 | open |
| 2026-05 | 14,066,093 | 14,017,440 | +48,654 | open (minor) |
| 2026-06 | 14,875,075 | 14,875,328 | −253 | **closed** (non-MSC cent-rounding tail) |
| 2026-07 | 15,225,589 | 15,225,814 | −225 | **closed** (non-MSC tail; DSB aligned) |

## 2026-01 — Δ −106,741

No MSC settlement executed in January (MSC#4 executed Feb 2), so both
sides are non-MSC only.

1. **[BA]** Your January P&L shows Integration Expenses −135,476 with no
   prime revenue leg. Please itemize January's expense and revenue
   categories so we can reconcile the −106,741 residual against our
   non-MSC inventory (stability fees accrual, PSM jar, liquidations,
   SSR/stUSDS/DSR drips, keeper, Vest).

## 2026-02 — Δ +430,978 (MSC#4: Nov+Dec 2025 cycles, executed 2026-02-02)

1. **[BA]** Your Feb Obex item (1,364,888) = mint 1,768,819 − subproxy
   442,327 + November DV 38,396 only. The MSC#4 Obex post (t/27633) puts
   Net Sky Revenue at 1,447,572 — i.e. the December DV (82,684) is
   dropped from your item. Intentional or data-entry gap?
2. **[Amatsu/BA]** The MSC#4 CC Buffer transfer was 6,632,421 on-chain,
   but Nov + Dec Step-1 per t/27617/4 is 3,444,400 + 2,400,938 =
   5,845,338. What is the remaining **787,083**? (Sep/Oct 2025 Step-1
   arrears? A genesis/expense repayment? We currently book it as a cost.)
3. **[BA]** After the two items above, ~348K of the Feb delta remains on
   your expense side — please itemize February's expense categories.

## 2026-03 — Δ +2,879,784 (MSC#5 executed Mar 2 + MSC#6 executed Mar 30)

1. **[BA]** Your March **Operating Expenses 2,150,331.87** — please
   itemize (individual surplus-buffer payments + tx hashes). This is the
   bulk of the March delta; our non-MSC inventory has no counterpart.
2. **[BA]** The residual ~729,452 after Operating Expenses — likely your
   March Liquidation Revenues / PSM / RWA legs vs our accrual-basis
   non-MSC. Please share the March composition of those categories.

## 2026-04 — Δ +1,295,085 (MSC#7 executed Apr 27)

1. **[BA]** Every per-prime revenue item ties ours to the dollar
   (7,662,339 / 6,290,684 / 2,075,648), so the delta sits entirely on
   your expense side (April Expenses 23,129,707). Please itemize April's
   expense categories — we're looking for ~1.3M of expenses we either
   don't book or book in a different month.

## 2026-05 — Δ +48,654 (MSC#8 executed May 11) — minor

1. **[BA]** Sub-0.4% residual; suspected composition: GAR / Aligned
   Delegates timing or small non-MSC classification offsets. Please
   confirm May's below-the-line allocations (ADB, GAR deferrals) are
   fully outside net revenue.

## 2026-06 / 2026-07 — closed

June −253 and July −225 are cent-rounding tails in the accrual-basis
non-MSC leg. July's DSB transfer (34,902) is now classified as an
Operating expense on both sides.

## Cross-cutting (internal follow-ups)

- **MSC#11 (August 2026):** back-fill `settlement_blocks['2026-08']`,
  `cc_step1_paid['2026-08']` (20% of July net from the MSC#11 post) and
  any genesis/repayment lines when the post lands. Our July restatements
  (MSC11_CARRYOVER.md) will appear inside BA's August per-prime items.
- **Grove TGE penalty back-fill:** months before 2026-07 book $0 with a
  warning; MSC#7's post shows a March penalty (Grove DV 197,732 → 138,412,
  i.e. −59,320 penalty netted inside the DV) — decide whether pre-July
  penalties need entries in `grove_tge_penalty`.
