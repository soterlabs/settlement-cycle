# OBEX — 2026-03

Period: 2026-03-01 → 2026-03-31 (31 days)

## Headline

Suffix legend: ``(gross)`` = one-way ledger entry, ``(net)`` = after the row's relevant offsets. Two rows carry ``(net)``: ``sky_revenue (net)`` is the BR claim net of sUSDS / Curve / PSM3 spread reimbursements (intra-Sky credits); ``prime_agent_revenue (net)`` = ``prime_agent_total_revenue (gross)`` − ``sky_revenue (net)`` (i.e. the legacy ``monthly_pnl``). **For non-SDE primes (e.g. OBEX) this equals the prime's profit.** **For SDE-heavy primes (e.g. Grove) ``sky_revenue (net)`` already contains ``sde_revenue (gross)``** — the per-venue SDE-allocated share (1.0 for fixed SDE, ``sd_share`` per the per-venue table for capped SDE) was redirected out of ``prime_agent_revenue (gross)`` and into ``sky_revenue (net)``, so subtracting it once via ``sky_revenue (net)`` is correct; but interpreting ``prime_agent_revenue (net)`` as the prime's true profit overstates Sky's claim by exactly the redirected amount (which equals ``sde_revenue (gross)``, the prime-level sum of per-venue ``sd_revenue``). For those primes the prime's true profit is ``prime_agent_revenue (net) + sde_revenue (gross)``. Sky's net P&L (not a row) = ``sky_revenue (net)`` − ``agent_rate (gross)`` − ``distribution_rewards (gross)`` (``pol_agent_rate`` and the sUSDS / Curve / PSM3 spread reimbursements are NOT subtracted again because they're already inside ``sky_revenue (net)``).

| Field | USD |
|---|---:|
| prime_agent_revenue (gross) | $2,227,383.50 |
| agent_rate (gross) | $72,061.47 |
| prime_agent_total_revenue (gross) | $2,299,444.97 |
| sky_revenue (net) | $2,073,865.93 |
| prime_agent_revenue (net) | $225,579.04 |
| sky_revenue (gross, pre-spread-credit) | $2,073,865.93 |

## Per-venue

| Venue | Label | value_som | value_eom | period_inflow | actual_rev | revenue | sd_revenue | sd_share | spread_reimb |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| V1 | Maple syrupUSDC (Category B — ERC-4626 vault) | $606,441,300.46 | $608,668,683.96 | $0.00 | $2,227,383.50 | $2,227,383.50 | $0.00 | 0% | $0.00 |

