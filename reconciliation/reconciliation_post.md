# Settlement Reconciliation — MSC #5–#9 (January–May 2026)

*Prepared by Soter Labs. Reconciles our independent recompute of the monthly settlement primitives against the amounts published in the MSC #5–#9 settlement summaries.*

Source posts: [MSC#5](https://forum.skyeco.com/t/msc-5-settlement-summary-january-2026-spark-and-grove/27709/4) · [MSC#6](https://forum.skyeco.com/t/msc-6-settlement-summary-february-2026/27778/3) · [MSC#7](https://forum.skyeco.com/t/msc-7-settlement-summary-march-2026/27844/2) · [MSC#8](https://forum.skyeco.com/t/msc-8-settlement-summary-april-2026/27888/4) · [MSC#9](https://forum.skyeco.com/t/msc-9-settlement-summary-may-2026/27962/3)

> **Shorthand:** *"Surplus Buffer"* is short for *mint the debt and transfer Sky profit to the Surplus Buffer*; *"SubProxy"* is short for *transfer the prime's payment to its SubProxy*.

---

**Scope note — DR cases not yet addressed.** This reconciliation does not yet cover the following distribution-rewards cases:

1. **Aggregators** (e.g. Yearn, Velora, LazySummer) — Skybase only.
2. **Morpho vaults and markets** — Skybase and Grove.
3. **Ref code 0 for PSM3 on L2s** — Skybase only.

---

## 1. Purpose

Our independent recompute of each prime agent's settlement primitives vs. the figures actually settled on the forum, MSC#5–MSC#9 (January–May 2026) — **per prime, per month**.

We also reconcile **distribution rewards** specifically — our recompute against the DR actually paid out (§3.1) — alongside the demand-, supply- and Sky-side primitives.

Two demand-side primitives are **not yet in the `settlement-reports` repo** and are added here from external sources: **GAR** (Governance Accessibility Rewards, Skybase) and **CP** (Chronicle Points, Grove). Both reconcile to 0 — see §3.2.

**Why this post exists.** From MSC#5 to MSC#8 (April 2026) the settlements paid each prime only its **demand-side** entitlement (`agent_rate` + DR); the prime's **supply-side revenue share** was **not transferred**. That is known technical debt in the early MSC cycles, and truing it up is the point of this reconciliation — the tables below quantify the unpaid gap per prime and month.

**Headline:** the **Sky-side** claim (*mint debt → Surplus Buffer*) reproduces our `sky_revenue` closely. The **prime-payout side** (*Send to SubProxy*) carries the material, *structural* differences:

1. **From MSC#5 to MSC#8 (April), the SubProxy payout excluded net trading revenue** — it tracked only `agent_rate + distribution rewards`. From **MSC#9 (May) it moved toward full `prime_agent_profit`**.
2. **Our DR recompute reconciles closely with the DR actually paid** (the `Payouts` figures) once the workbook's own prime grouping is used — residuals are modest (see §3.1). Keel is the exception: it accrued DR before any was paid.

---

## 2. Scope & method

### 2.1. Details

- **Primes:** Spark, Grove, Obex, Skybase, Keel.
- **Window:** 2026-01 … 2026-05 (MSC #5–#9).
- **Sky-side** — our `sky_revenue` vs forum **mint debt / Surplus Buffer**.
- **Prime-payout** — our **Prime profit** vs forum **Send to SubProxy**.
- **DR rollup** — per prime via the workbook's authoritative `group` column (`Summary` sheet), not fixed ref-code ranges.
- **DR baseline** — as-paid DR is the **`Payouts`** sheet (actual distributions).
- All figures USDS. **Δ = Soter − forum** (positive = our recompute is higher).

**Definitions.**
- **AR** — agent rate (`SSR + 20bps` on subproxy USDS; see §3.3 note).
- **DR** — distribution rewards (active referral codes); Obex earns none.
- **GAR** — Governance Accessibility Rewards (Skybase only; see §3.2).
- **CP** — Chronicle Points (Grove only; see §3.2).
- **DV** — demand-side revenue = `AR + DR + GAR + CP`.
- **SV** — supply-side revenue = net trading revenue + SDE = `profit − AR − DR` (only AR and DR are netted out — they're the demand-side items already inside `prime_agent_profit`; GAR/CP aren't in `profit`, they sit in DV, so they are **not** subtracted here).
- **Prime profit** = `DV + SV` (= `prime_agent_profit` + GAR + CP) — what the prime should receive.
- **Sky net** = `sky_revenue − DV` — Sky's revenue net of the demand side it pays the prime.

**Sources** (every figure is reproducible):
- Forum as-paid figures — the five MSC posts linked above.
- Our MSC primitives — each prime's [`reports/<prime>/<month>/summary.md`](https://github.com/soterlabs/settlement-reports/blob/main/reports).
- DR (Soter + Payouts) — `Summary` / `Soter by Ref Code` / `Payouts` sheets of [`dr_comparison_latest.xlsx`](https://github.com/soterlabs/settle-dr-dune/blob/main/dune-results/dr_comparison_latest.xlsx).

### 2.2. Venue support

We have **not** included Distribution Rewards for **aggregators** (1inch, Verlora, etc.), **Morpho Vaults/Markets**, and **PSM3 on L2s** *(to be confirmed)*. These can trigger double-counting, and we want clean frameworks before including them.

---

## 3. Demand-side (DV) reconciliation

The prime's demand-side revenue **`DV = AR + DR + GAR + CP`**. We reconcile each part against what was paid, then the DV total against the forum **Demand Side Total**.

### 3.1. Distribution rewards — Soter vs Payouts (as-paid)

> DR **excludes the venue classes flagged in §2.2**, pending clean frameworks.

Δ = Soter − Payouts. The `Payouts` sheet covers only the months shown.

**Spark**

| Month | Soter DR | Payouts (paid) | Δ |
|---|--:|--:|--:|
| MSC#5 2026-01 | 1,154,488 | 1,284,583 | -130,095 |
| MSC#6 2026-02 | 1,022,253 | 1,154,007 | -131,754 |
| MSC#7 2026-03 | 1,412,271 | 1,606,621 | -194,350 |

**Grove**

| Month | Soter DR | Payouts (paid) | Δ |
|---|--:|--:|--:|
| MSC#7 2026-03 | 191,719 | 191,643 | +76 |

**Skybase**

| Month | Soter DR | Payouts (paid) | Δ |
|---|--:|--:|--:|
| MSC#5 2026-01 | 147,764 | 235,867 | -88,103 |
| MSC#6 2026-02 | 116,850 | 194,882 | -78,032 |
| MSC#7 2026-03 | 171,564 | 243,259 | -71,695 |

**Keel**

| Month | Soter DR | Payouts (paid) | Δ |
|---|--:|--:|--:|
| MSC#7 2026-03 | 29,060 | 29,062 | -2 |

> **Excluded (non-prime).** A further **7,876,633** of Soter-computed DR over the window sits in workbook groups not mapped to any prime (`Osero`, `Other` — the excluded venue classes in §2.2). It is intentionally **not** attributed to any prime and excluded from this reconciliation.

### 3.2. GAR & CP — primitives outside `settlement-reports`

Not yet in the `settlement-reports` repo; added from external sources. Both are paid via the SubProxy and reconcile to **0**.

**GAR — Governance Accessibility Rewards (Skybase).**

GAR = **1% of a monthly base**, **only Skybase**. First settled in MSC#9 (May 2026): the month's own GAR + a one-time backlog true-up (May 2025 – April 2026). We add the same, so it reconciles to **0**.

| Component | Period | Amount |
|---|---|--:|
| Current-month GAR | May 2026 | 139,990 |
| Backlog true-up | May 2025 – April 2026 | 1,383,235 |
| **Total GAR in MSC#9 SubProxy** | | **1,523,225** |

**CP — Chronicle Points (Grove).**

CP = 20% of the base rate on the Chronicle Farm USDS balance ([dashboard](https://dune.com/lakonema2000_/chronicle-points-monthly-summary)); **only Grove**. Paid Surplus Buffer → SubProxy: MSC#8 settled the backlog (program start → Mar 2026), MSC#9 settled Apr + May. We add the same, so it reconciles to **0**.

| Settled in | CP |
|---|--:|
| MSC#8 (April 2026) | 182,908 |
| MSC#9 (May 2026) | 35,587 |
| **Total** | **218,494** |

### 3.3. DV total vs forum Demand Side Total

Our `DV = AR + DR + GAR + CP` vs the forum **demand settled** = Demand Side Total + any CP paid via the SubProxy (so CP appears on both sides and washes out). Δ = ours − settled.

> **Agent-rate definition.** The settlements compute AR as *"SSR on subproxy USDS"*; per Atlas it is **`SSR + 20bps`** — the 20bps was understated. Our `AR` uses `SSR + 20bps`.

> **Grove (Apr–May).** DR was **not settled** these months, so the demand budget covers `AR + CP` first (April leaves only ~13k for DR; May none). Grove's Δ is essentially unpaid DR.

**Spark**

| Month | agent_rate | DR (Soter) | ours | Forum demand settled | Δ |
|---|--:|--:|--:|--:|--:|
| MSC#5 2026-01 | 106,393 | 1,154,488 | 1,260,881 | 1,387,824 | -126,943 |
| MSC#6 2026-02 | 114,974 | 1,022,253 | 1,137,227 | 1,265,132 | -127,905 |
| MSC#7 2026-03 | 122,912 | 1,412,271 | 1,535,182 | 1,725,726 | -190,544 |
| MSC#8 2026-04 | 115,391 | 1,506,065 | 1,621,457 | 1,512,762 | +108,695 |
| MSC#9 2026-05 | 118,578 | 1,632,541 | 1,751,119 | 1,657,338 | +93,781 |

**Grove**

| Month | agent_rate | DR (Soter) | CP | ours | Forum demand settled | Δ |
|---|--:|--:|--:|--:|--:|--:|
| MSC#5 2026-01 | 6,276 | 0 | 0 | 6,276 | 6,090 | +186 |
| MSC#6 2026-02 | 5,810 | 0 | 0 | 5,810 | 5,630 | +180 |
| MSC#7 2026-03 | 6,287 | 191,719 | 0 | 198,006 | 138,412 | +59,594 |
| MSC#8 2026-04 | 45,333 | 125,090 | 182,908 | 353,331 | 241,690 | +111,641 |
| MSC#9 2026-05 | 71,961 | 20,519 | 35,587 | 128,066 | 134,616 | -6,550 |

**Obex**

| Month | agent_rate | DR (Soter) | ours | Forum demand settled | Δ |
|---|--:|--:|--:|--:|--:|
| MSC#5 2026-01 | 73,520 | 0 | 73,520 | 71,342 | +2,178 |
| MSC#6 2026-02 | 67,754 | 0 | 67,754 | 65,719 | +2,035 |
| MSC#7 2026-03 | 72,061 | 0 | 72,061 | 69,793 | +2,268 |
| MSC#8 2026-04 | 68,358 | 0 | 68,358 | 64,862 | +3,496 |
| MSC#9 2026-05 | 69,563 | 0 | 69,563 | 69,563 | 0 |

**Skybase**

| Month | agent_rate | DR (Soter) | GAR | ours | Forum demand settled | Δ |
|---|--:|--:|--:|--:|--:|--:|
| MSC#6 2026-02 | 30,492 | 116,850 | 0 | 147,342 | 203,134 | -55,792 |
| MSC#7 2026-03 | 33,536 | 171,564 | 0 | 205,100 | 225,299 | -20,199 |
| MSC#8 2026-04 | 32,401 | 171,917 | 0 | 204,318 | 201,469 | +2,849 |
| MSC#9 2026-05 | 33,878 | 189,188 | 139,990 | 363,056 | 423,381 | -60,325 |

**Keel**

| Month | agent_rate | DR (Soter) | ours | Forum demand settled | Δ |
|---|--:|--:|--:|--:|--:|
| MSC#7 2026-03 | 2,127 | 29,060 | 31,186 | 30,241 | +945 |
| MSC#8 2026-04 | 31,677 | 23,207 | 54,883 | 52,915 | +1,968 |
| MSC#9 2026-05 | 32,279 | 22,600 | 54,879 | 32,279 | +22,600 |

## 4. Supply-side (SV) reconciliation

The prime's supply-side revenue **`SV = profit − AR − DR`** (net trading revenue + SDE). Through MSC#8 the supply side was **not settled** — reconciled against 0 (the unpaid arrears). From MSC#9 it was settled as the forum **Prime Share** (`SubProxy − demand settled`, i.e. net of AR + DR + CP). Δ = our SV − settled. (Primes with no allocation module have no SV.)

**Spark**

| Month | our SV | settled (Prime Share) | Δ |
|---|--:|--:|--:|
| MSC#5 2026-01 | -318,867 | 0 | -318,867 |
| MSC#6 2026-02 | 2,804,648 | 0 | +2,804,648 |
| MSC#7 2026-03 | 2,348,789 | 0 | +2,348,789 |
| MSC#8 2026-04 | 833,428 | 0 | +833,428 |
| MSC#9 2026-05 | 2,766,367 | 2,547,519 | +218,848 |

**Grove**

| Month | our SV | settled (Prime Share) | Δ |
|---|--:|--:|--:|
| MSC#5 2026-01 | -52,691 | 0 | -52,691 |
| MSC#6 2026-02 | 2,883,563 | 0 | +2,883,563 |
| MSC#7 2026-03 | -2,000,509 | 0 | -2,000,509 |
| MSC#8 2026-04 | 3,682,517 | 0 | +3,682,517 |
| MSC#9 2026-05 | 289,319 | 137,227 | +152,091 |

**Obex**

| Month | our SV | settled (Prime Share) | Δ |
|---|--:|--:|--:|
| MSC#5 2026-01 | 439,228 | 0 | +439,228 |
| MSC#6 2026-02 | 170,350 | 0 | +170,350 |
| MSC#7 2026-03 | 153,518 | 0 | +153,518 |
| MSC#8 2026-04 | 262,250 | 0 | +262,250 |
| MSC#9 2026-05 | 456,641 | 456,641 | 0 |

> Grove's MSC#9 Δ (+152,091) is its **token-launch penalty** — the penalty reduced the transferred Prime Share below our SV; left visible.

## 5. Sky-side — Surplus Buffer = `Sky net + DV + SV`

Sky **mints to the Surplus Buffer**, keeps its **net revenue**, and transfers `DV + SV` to the SubProxy. So the mint decomposes additively as `Sky net + DV + SV`, **Sky net = `sky_revenue − DV`**. Δ = our mint − forum mint. Through MSC#8 SV wasn't minted (shows as 0; trued up in §6); from MSC#9 SV is minted, so Δ → ~0.

**Spark**

| Month | Sky net | DV | SV | = our mint | forum mint | Δ |
|---|--:|--:|--:|--:|--:|--:|
| MSC#5 2026-01 | 6,563,314 | 1,260,881 | 0 | 7,824,194 | 8,079,210 | -255,016 |
| MSC#6 2026-02 | 6,353,273 | 1,137,227 | 0 | 7,490,500 | 7,746,811 | -256,311 |
| MSC#7 2026-03 | 6,274,198 | 1,535,182 | 0 | 7,809,381 | 7,662,339 | +147,042 |
| MSC#8 2026-04 | 7,644,215 | 1,621,457 | 0 | 9,265,672 | 9,179,021 | +86,651 |
| MSC#9 2026-05 | 8,932,010 | 1,751,119 | 2,766,367 | 13,449,495 | 13,427,874 | +21,621 |

**Grove**

| Month | Sky net | DV | SV | = our mint | forum mint | Δ |
|---|--:|--:|--:|--:|--:|--:|
| MSC#5 2026-01 | 6,271,061 | 6,276 | 0 | 6,277,336 | 6,205,320 | +72,016 |
| MSC#6 2026-02 | 6,131,392 | 5,810 | 0 | 6,137,202 | 6,346,829 | -209,627 |
| MSC#7 2026-03 | 6,110,188 | 198,006 | 0 | 6,308,195 | 6,290,684 | +17,511 |
| MSC#8 2026-04 | 8,981,968 | 353,331 | 0 | 9,335,298 | 9,385,986 | -50,688 |
| MSC#9 2026-05 | 8,460,439 | 128,066 | 289,319 | 8,877,823 | 8,877,823 | 0 |

**Obex**

| Month | Sky net | DV | SV | = our mint | forum mint | Δ |
|---|--:|--:|--:|--:|--:|--:|
| MSC#5 2026-01 | 2,037,413 | 73,520 | 0 | 2,110,933 | 2,095,775 | +15,158 |
| MSC#6 2026-02 | 1,880,985 | 67,754 | 0 | 1,948,739 | 1,948,422 | +317 |
| MSC#7 2026-03 | 2,001,804 | 72,061 | 0 | 2,073,866 | 2,075,648 | -1,782 |
| MSC#8 2026-04 | 1,900,455 | 68,358 | 0 | 1,968,813 | 1,969,499 | -686 |
| MSC#9 2026-05 | 1,935,641 | 69,563 | 456,641 | 2,461,845 | 2,461,845 | 0 |

## 6. Net true-up per entity

### 6.1. Primes — `SV + DV`

Per prime, the Jan–May sum of each reconciled side (= `Prime profit − SubProxy`). **Positive = transfer owed to the prime; negative = grab back.**

- **Supply-side (SV)** — Σ (our SV − settled): the unpaid arrears through MSC#8 plus any MSC#9 shortfall (§4).
- **Demand-side (DV)** — Σ (our DV − forum demand settled) (§3.3); GAR/CP folded in (CP washes).

| Prime | Supply-side (SV) | Demand-side (DV) | **Net true-up** |
|---|--:|--:|--:|
| Spark | +5,886,846 | -242,916 | **+5,643,929** |
| Grove | +4,664,971 | +165,050 | **+4,830,021** |
| Obex | +1,025,346 | +9,978 | **+1,035,324** |
| Skybase | — | -133,466 | **-133,466** |
| Keel | — | +25,514 | **+25,514** |

### 6.2. Sky — per prime

Sky's own true-up: per minting prime, the Jan–May sum of the §5 mint reconciliation (Σ `our mint − forum mint`). Since DV cancels in the mint, this is just Σ (`sky_revenue + minted SV − forum mint`). **Positive = Sky under-minted to the Surplus Buffer vs our recompute; negative = over-minted.**

| Prime | Sky-side residual (Σ §5 Δ) |
|---|--:|
| Spark | -256,013 |
| Grove | -170,788 |
| Obex | +13,008 |

