"""Compute — pure-Python settlement math on Normalize primitives.

The low-level functions (sky_revenue, agent_rate, prime_agent_revenue) are pure
— take DataFrames + Decimals, return Decimals. Trivially testable with hand-built
input frames.

`compute_monthly_pnl` is the orchestrator — the only place where Normalize and
Compute meet. It is the user-facing entry point for a settlement run.
"""

from .agent_rate import compute_agent_rate
from .monthly_pnl import Sources, compute_monthly_pnl
from .prime_agent_revenue import (
    VenueRevenueInputs,
    compute_prime_agent_revenue,
    compute_venue_revenue,
)
from .sky_revenue import compute_sky_revenue

__all__ = [
    "Sources",
    "VenueRevenueInputs",
    "compute_agent_rate",
    "compute_monthly_pnl",
    "compute_prime_agent_revenue",
    "compute_sky_revenue",
    "compute_venue_revenue",
]
