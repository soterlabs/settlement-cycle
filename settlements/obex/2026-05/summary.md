# OBEX — 2026-05

Period: 2026-05-01 → 2026-05-31 (31 days)

## Headline

Suffix legend: ``(gross)`` = one-way ledger entry, ``(net)`` = after the row's relevant offsets. Two rows carry ``(net)``: ``sky_revenue (net)`` is the BR claim net of sUSDS / Curve / PSM3 spread reimbursements (intra-Sky credits); ``prime_agent_revenue (net)`` = ``prime_agent_total_revenue (gross)`` − ``sky_revenue (net)`` (i.e. the legacy ``monthly_pnl``). **For non-SDE primes (e.g. OBEX) this equals the prime's profit.** **For SDE-heavy primes (e.g. Grove) ``sky_revenue (net)`` already contains ``sde_revenue (gross)``** — that revenue was redirected out of ``prime_agent_revenue (gross)`` and 100% to Sky, so subtracting it once via ``sky_revenue (net)`` is correct; but interpreting ``prime_agent_revenue (net)`` as the prime's true profit overstates Sky's claim by ``sde_revenue (gross)``. For those primes the prime's true profit is ``prime_agent_revenue (net) + sde_revenue (gross)``. Sky's net P&L (not a row) = ``sky_revenue (net)`` − ``agent_rate (gross)`` − ``pol_agent_rate (gross)`` − ``distribution_rewards (gross)``.

| Field | USD |
|---|---:|
| prime_agent_revenue (gross) | $2,461,844.92 |
| agent_rate (gross) | $69,563.01 |
| prime_agent_total_revenue (gross) | $2,531,407.93 |
| sky_revenue (net) | $2,005,204.07 |
| prime_agent_revenue (net) | $526,203.86 |
| sky_revenue (gross, pre-spread-credit) | $2,005,204.07 |

## Per-venue

| Venue | Label | value_som | value_eom | period_inflow | actual_rev | revenue | sd_revenue | sd_share | spread_reimb |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| V1 | Maple syrupUSDC (Category B — ERC-4626 vault) | $610,899,747.35 | $613,361,592.27 | $0.00 | $2,461,844.92 | $2,461,844.92 | $0.00 | 0% | $0.00 |

