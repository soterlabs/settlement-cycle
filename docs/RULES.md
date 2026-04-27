# MSC Revenue Calculation Rules

Reference rules for computing prime agent revenues in the Sky ecosystem. These rules apply to all agents (OBEX, Skybase, Grove, Spark). Established after reconciling Dune queries against MSC settlement posts.

## Rule 1: Use APY, not APR

All calculations use **APY with per-second compounding**, matching how the SSR accrues onchain:

```
daily_interest = D × [(1 + APY)^(1/365) - 1]
```

The MSC settlement posts use a simpler APR approach (`D × rate / 365`), which overstates daily interest by ~1.8%. This is flagged as a discrepancy in `agents/obex/findings/` for each month.

## Rule 2: Track SSR changes via SP-BEAM

The SSR is adjusted through **SP-BEAM** governance parameter changes. It can change multiple times per month (e.g., Nov–Dec 2025 had 4 changes). Queries must apply the correct SSR for each day, not a single monthly rate.

Source: [Dune query 6953056](https://dune.com/queries/6953056) — reads `file(bytes32("ssr"), uint256)` calls on sUSDS (`0xa3931d...fbD`).

### SSR history (onchain)

| Effective date | SSR (APY) | Borrow rate (SSR+0.30%) | Tx |
|----------------|-----------|-------------------------|----|
| Sep 17, 2024 | 6.25% | 6.55% | `0x2221973...` |
| Oct 7, 2024 | 6.50% | 6.80% | `0x0e0dfb0...` |
| Nov 18, 2024 | 8.51% | 8.81% | `0x789c927...` |
| Nov 30, 2024 | 9.51% | 9.81% | `0x1dd6319...` |
| Dec 8, 2024 | 12.51% | 12.81% | `0xc6807c3...` |
| Feb 10, 2025 | 8.76% | 9.06% | `0x37d1ff4...` |
| Feb 24, 2025 | 6.50% | 6.80% | `0x395e70d...` |
| Mar 24, 2025 | 4.50% | 4.80% | `0xfa915f8...` |
| Aug 4, 2025 | 4.75% | 5.05% | `0xf1c5e50...` |
| Oct 27, 2025 | 4.50% | 4.80% | `0xbca8f5e...` |
| **Nov 7, 2025** | **4.25%** | **4.55%** | `0xa63295c...` |
| **Nov 11, 2025** | **4.50%** | **4.80%** | `0xb951835...` |
| **Dec 2, 2025** | **4.25%** | **4.55%** | `0xac1db72...` |
| **Dec 16, 2025** | **4.00%** | **4.30%** | `0xef4bc6f...` |
| **Mar 9, 2026** | **3.75%** | **4.05%** | `0x9c48c28...` |

## Rule 3: Track subproxy USDS and sUSDS balances for agent rate calculation

The **agent rate** is the earnings owed to the prime agent on its subproxy's idle holdings:

```
daily_agent_rate = subproxy_usds × [(1 + SSR + 0.20%)^(1/365) - 1]
                 + subproxy_susds × [(1.002)^(1/365) - 1]
```

- **USDS in subproxy** earns **SSR + 0.20% APY**
- **sUSDS in subproxy** earns a flat **0.20% APY** (not SSR-based)
- Balances change when MSC settlements arrive (e.g., Feb 2 +442,327 from MSC #4). These mid-month changes must be accounted for day-by-day.
- The MSC settlement posts appear to use flat SSR (without the +0.20% spread) and APR instead of APY. Both are flagged as discrepancies in `agents/obex/findings/`. The forum posts are **not** the source of truth for the correct rate.

Source: [Dune query 6954383](https://dune.com/queries/6954383) (parameterized).

Subproxy balance histories are tracked per agent — see each agent's README under `agents/`.

## Rule 4: Track Vat debt changes for sky revenue calculation

**Sky revenue** is the interest the prime agent owes to Sky, computed from utilized USDS at the borrow rate:

```
daily_sky_revenue = utilized_usds × [(1 + borrow_rate)^(1/365) - 1]
```

- **Borrow rate = SSR + 0.30%**
- **Utilized USDS = Vat ilk debt (cumulative frobs) - subproxy USDS - subproxy sUSDS - ALM proxy USDS**
- Vat debt changes via frob transactions AND MSC settlement debt minting. Both must be tracked.
- The MSC settlement figures imply a slightly higher effective demand than our "utilized USDS" (~1-2% gap growing over time), possibly due to accumulated Vat rate on the ilk art. This is flagged in findings.
