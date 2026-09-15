"""SSR boundaries from sUSDS File(bytes32,uint256) logs.

The File event records the same successful governance update as the legacy
ssr_history.sql trace query. Reverted calls have no surviving log. Keep the
legacy DOUBLE -> POWER -> Decimal(str(...)) conversion so this source change
does not also change the settlement's rate arithmetic.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pandas as pd

from ...extract import hypersync_store
from .hypersync_balances import _default_start_block

SUSDS = "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd"
FILE_TOPIC = "0xe986e40cc8c151830d4f61050f4fb2e4add8567caad2d5f5496f9158e91fe4c7"
SSR_KEY = "0x737372" + "00" * 29


class HyperSyncSSRSource:
    def __init__(self, *, fetch_logs: Callable[..., Any] | None = None,
                 resolve_start_block: Callable[[str, date], int] | None = None) -> None:
        self._fetch = fetch_logs or hypersync_store.fetch_logs
        self._resolve = resolve_start_block or _default_start_block

    def ssr_history(self, start: date, pin_block: int) -> pd.DataFrame:
        rows = self._fetch(
            "ethereum", [{"address": [SUSDS], "topics": [[FILE_TOPIC], [SSR_KEY]]}],
            self._resolve("ethereum", start), pin_block,
        )
        daily = {}
        for row in sorted(rows, key=lambda r: (r.block_number, r.log_index)):
            day = datetime.fromtimestamp(row.block_time, UTC).date()
            if day < start or row.block_number > pin_block:
                continue
            if len(row.data.removeprefix("0x")) != 64:
                raise ValueError(f"Malformed sUSDS File event at {row.block_number}:{row.log_index}")
            rate = float(int(row.data, 16)) / 1e27
            daily[day] = Decimal(str(rate ** 31536000 - 1))
        return pd.DataFrame(
            [{"effective_date": day, "ssr_apy": apy} for day, apy in sorted(daily.items())],
            columns=["effective_date", "ssr_apy"],
        )
