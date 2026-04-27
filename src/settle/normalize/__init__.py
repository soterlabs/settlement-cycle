"""Normalize — source dispatch + canonical primitives.

Each public function returns a typed Python object keyed by business concept
(`debt_timeseries`, `balance_timeseries`, `ssr_history`). The actual source
(Dune today, subgraph tomorrow) is selected by the registry via config.
"""

from .balances import (
    get_alm_balance_timeseries,
    get_subproxy_balance_timeseries,
    get_venue_inflow_timeseries,
)
from .debt import get_debt_timeseries
from .positions import get_position_balance, get_position_value
from .prices import get_unit_price
from .ssr import get_ssr_history

__all__ = [
    "get_alm_balance_timeseries",
    "get_debt_timeseries",
    "get_position_balance",
    "get_position_value",
    "get_ssr_history",
    "get_subproxy_balance_timeseries",
    "get_unit_price",
    "get_venue_inflow_timeseries",
]
