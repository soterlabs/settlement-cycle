"""Agent rate — what the prime EARNS on its subproxy's idle holdings.

The agent rate is **SSR + 20bps**. Two equivalent expressions:

* USDS in subproxy: rate = SSR + 20bps APY (full agent rate).
* sUSDS in subproxy: SSR is already received via the token's index growth,
  so we only pay the 20bps on top.

Caveat: the 20bps for sUSDS is applied to the **cost-basis principal**
(``shares × entry_pps``), NOT to the current balance. ``shares × current_pps``
would double-count SSR — the index already reflects the SSR accrual the prime
keeps. The orchestrator (compute_monthly_pnl) is responsible for converting
share-balances to cost-basis USDS before passing in.

NOTE on naming: prior versions called the 20bps an "agent spread" — that's a
misnomer. The agent rate is *SSR + 20bps* uniformly. The "+20bps" is just the
component above SSR; in the sUSDS case it's the only component because SSR
already accrued via the token index.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pandas as pd

from ..domain.period import Period
from ._helpers import cum_at_or_before, daily_compounding_factor, ssr_at_or_before

# +20bps over SSR — the agent rate's component above SSR. For USDS in subproxy:
# rate = SSR + 20bps. For sUSDS in subproxy: rate = 20bps (SSR already accrues
# via the token index, kept by the prime — applying SSR again would double-
# count). NOT two separate rates; the same agent rate, viewed differently.
AGENT_RATE_OVER_SSR = Decimal("0.002")
AGENT_RATE_SUSDS_ONLY = AGENT_RATE_OVER_SSR  # alias for readability at the call site

# Pre-compute the sUSDS daily factor — it doesn't depend on SSR.
_SUSDS_DAILY_FACTOR = daily_compounding_factor(AGENT_RATE_SUSDS_ONLY)


def compute_agent_rate(
    period: Period,
    subproxy_usds: pd.DataFrame,
    subproxy_susds: pd.DataFrame,
    ssr: pd.DataFrame,
) -> Decimal:
    """Sum of daily agent rate over ``period``."""
    total = Decimal("0")
    current = period.start
    while current <= period.end:
        cum_usds = cum_at_or_before(subproxy_usds, "cum_balance", current)
        cum_susds = cum_at_or_before(subproxy_susds, "cum_balance", current)

        # USDS earns the full agent rate = SSR + 20bps.
        if cum_usds > 0:
            ssr_apy = ssr_at_or_before(ssr, current)
            usds_apy = ssr_apy + AGENT_RATE_OVER_SSR
            total += cum_usds * daily_compounding_factor(usds_apy)

        if cum_susds > 0:
            total += cum_susds * _SUSDS_DAILY_FACTOR

        current = current + timedelta(days=1)

    return total
