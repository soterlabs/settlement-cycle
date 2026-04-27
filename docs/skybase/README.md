# Skybase

Skybase is a prime agent in the Sky ecosystem. It does not have an allocation system (no debt to Sky). Its MSC revenue comes from the agent rate on subproxy USDS holdings, plus distribution rewards from active referral codes.

## Key contracts

| Contract | Address | Role |
|----------|---------|------|
| Subproxy | `0x08978E3700859E476201c1D7438B3427e3C81140` | Holds idle USDS (earns agent rate) |

## Dune query parameters

Only the agent rate query applies (no allocations, no ilk, no sky revenue).

| Parameter | Value |
|-----------|-------|
| `subproxy_address` | `0x08978E3700859E476201c1D7438B3427e3C81140` |
| `start_date` | `2025-12-01` |
| `calendar_start_date` | `2026-02-02` |

## Subproxy balance history

| Date | Event | USDS Balance |
|------|-------|--------------|
| Feb 2, 2026 | Initial funding | 10,000,000 |
| Mar 11, 2026 | MSC #6 settlement (+203,134) | 10,203,134 |

## Agent rate summary

| Month | Days | Avg USDS Balance | Agent Rate |
|-------|------|-----------------|------------|
| Feb 26 | 27 | 10.0M | 30,435 |
| Mar 26 | 31 | 10.0M | 33,474 |

Note: The MSC #6 Demand Side total for Feb was 203,134 USDS. Our agent rate calculation gives 30,435 USDS. The remaining ~172,699 USDS comes from "Distribution Rewards - 2 active referral codes", which is a separate revenue stream not tracked by these queries.

## Findings

Pending.
