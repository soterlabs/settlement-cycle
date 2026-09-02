# SNR — open questions per month (vs BA Labs dashboard)

*Maintained alongside `settlements/sky_total/`. Status as of 2026-08-07.*

**Basis (2026-08-07):** months **≤ 2026-06** are PAID basis, defined to
match BA's "Net revenue" dashboard line (the comparison below). Months
**≥ 2026-07** are ACCRUAL basis (operator decision): SNR = prime revenue
EARNED in the month (paid the following month at the MSC settling the
cycle; per-prime preview pinned to the MSC post / settlement sheet) + the
month's non-MSC net. July 2026 is frozen at **10,517,425.81 ≈
10,517,426** (MSC net 14,097,718 − non-MSC 3,580,292). The BA-dashboard
July net (15,225,814) corresponds to the PAID view (MSC#10 executed in
July) and is no longer our July headline; the two coincide one month
apart — accrual(M) ≡ paid(M+1) up to execution variance.*

**Confirmed rules (no longer questions):**

- *SNR definition (2026-08-06)*: `SNR = Σ mints − Σ subproxy sends (net of
  capital seedings) + non-MSC net` — nothing else above the line. The FULL
  Core Council Buffer transfer (Step-1 distribution + genesis/expense
  repayments) sits below net revenue (BA's "Security and Maintenance" =
  CC Buffer + Aligned Delegates transfers); the Grove TGE penalty is
  income Sky retains (already inside mint − subproxy), never a deduction;
  buybacks are BA's "Revenue Allocation" (below the line, untracked here).

- *Recognition*: month M books the incomes/expenses that happen during M;
  Sky receives payment for cycle M−1 during M. BA's per-prime
  `Revenue > Primes` item = settlement executed in M: `mint − subproxy
  transfer + demand-side total` (as-settled Sky Share incl. reconciliation
  corrections). Verified exact to the dollar Feb–Jul.
- *Capital seedings* (Skybase $10M @ MSC#4, Keel + Osero/PRYSM $10M each
  @ MSC#6) are NOT in net revenue — they sit below the line, hitting only
  "remitted to Sky reserves". Verified: absent from BA's revenue, expense,
  and revenue_distribution groups. Adopted on our side 2026-08-05: SNR
  excludes them; the summary carries a below-the-line section.
- *Grove TGE penalties before 2026-07* are NOT separate deductions — they
  were netted inside Grove's demand-side payment (e.g. MSC#7: 197,732 →
  138,412 paid), so they're already in the subproxy sends. Only MSC#10's
  1,396,260 settled as its own line (operator decision 2026-08-05: no
  back-fill).
- *DSB transfers* are Operating expenses (BA classification) on the PAID
  basis. On the accrual basis a DSB rides the previewed settlement and is
  deducted from the MSC leg via `msc_preview.<month>.dsb` — MSC#11
  announces none, so July carries $0.
- *Step 1 Capital* = 20% of the cycle month's net revenue, split evenly
  Core Council / Fortification, riding the CC Buffer transfer; the paid
  figures are in each MSC post's BA capital-allocations section (now in
  `config/sky_total.yaml → cc_step1_paid`). Aligned Delegates (1%) and GAR
  (0.5% + 0.5%) are separate below-the-line allocations.

| Month | our SNR | BA net | Δ | Status |
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
   arrears? A genesis/expense repayment? SNR-neutral either way — the
   full transfer sits below the line — but the Security-and-Maintenance
   decomposition should name it.)
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

## Basis-transition gap (2026-08-07)

Switching to the accrual basis at 2026-07 leaves **MSC#10 — the June
cycle, executed 2026-07-20 — carried by no month** in this series: June
stays paid basis (MSC#9, executed in June) and July is the MSC#11
preview. Inherent to a mid-series basis switch, not a defect, but it
means **`settlements/sky_total/2026-01…07` must not be summed as a YTD
figure** (it would omit ~15.2M). The June cycle's prime-side economics
are in `settlements/<prime>/2026-06`; its paid Sky figures remain
reconstructible from `settlement_blocks['2026-07']`.

## Rate units — Atlas clarification requests (2026-09-01)

1. **[Atlas/BA] SOFR day-count basis.** The Atlas defines SOFR as "the rate
   (expressed as an annual rate) ... as administered and published by the
   Federal Reserve Bank of New York" but does not state a day-count. The Fed
   annualises SOFR on **actual/360**; we accrue on `n/365`, so we
   under-accrue it by ~1.39% of its value (~5 bps on the reference). Using
   it as published keeps the subsidy alive; converting to the /365-equivalent
   (3.6977% at a 3.647% print) would push the reference above `BR_apr` and
   clamp the subsidy to zero. One sentence in the Atlas would settle it.

2. **[Atlas] The T-Bill article.** Same question — the 3M is quoted both as
   a discount rate (/360) and a coupon-equivalent yield (/365), and which one
   "the Treasury Bill Rate" means determines what we should pull. Dormant
   while SOFR is the reference, but it governs any restatement of Jan–Jul.

3. **[internal] Thin subsidy headroom.** `BR_apr` (3.664456%) now sits ~1.4
   bps above SOFR. August 2026 printed 3.62%-3.66%, so the subsidy applied
   all month, but the 2026-08-25 print (3.66%) came within 0.45 bps of
   `BR_apr`. Half a basis point of movement either way would clamp it to
   zero on individual days, so a `zero_benefit` warning is now a plausible
   rate outcome rather than proof of stale data — check the day's prints
   before treating one as a defect.

## Cross-cutting (internal follow-ups)

- **MSC#11 (August 2026):** back-fill `settlement_blocks['2026-08']`,
  `cc_step1_paid['2026-08']` (20% of July net from the MSC#11 post) and
  any genesis/repayment lines when the post lands. Our July restatements
  (MSC11_CARRYOVER.md) will appear inside BA's August per-prime items.
- **GAR — RETIRED from the MSC from 2026-08 (operator decision 2026-09).**
  Bounded via `config/skybase.yaml → gar.until: '2026-08'`, so 2026-01…07
  still compute and reproduce it. SNR is unaffected for those months
  (Jan–Jun are paid-basis and never read `gar`; July is pinned via
  `gar_in_dv`) — from 2026-08 SNR is ~$105K/month HIGHER, since GAR
  subtracted from it. The BA question below is now historical. Original
  methodology, for the record:
- **GAR (operator decisions 2026-08-06/07):** Skybase's GAR = 1% × the
  SAME month's SNR (July: 1% × the frozen 10,517,426 = **105,174.26**,
  paid at MSC#11 per the updated post — send 327,407). The freeze
  convention avoids the fixed point: the month's SNR is computed with
  whatever GAR the report carried at freeze time (July: 152,255.89,
  pinned in config `msc_preview.skybase.gar_in_dv`), then the report's
  GAR is reset to 1% × the frozen SNR and the SNR is NOT recomputed.
  NOTE: the frozen sheet keeps skybase send 374,489 while the post pays
  327,407 — the 47,082 delta will surface in August's paid-vs-preview
  reconciliation.
  **[BA question]** BA's dashboard historically shows GAR as a
  below-the-line Step-1 allocation (0.5% Integrators + 0.5% Prime
  Agents) — confirm how the GAR portion of Skybase's settlement payment
  is classified in "Net revenue" from MSC#11 on; if below the line, a
  ~GAR-sized monthly delta will appear vs our series.
