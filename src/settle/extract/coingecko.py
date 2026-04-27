"""CoinGecko price fetcher. Free tier — rate-limited, no auth required."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import requests

from .cache import cached

API_BASE = "https://api.coingecko.com/api/v3"
DEFAULT_TIMEOUT = 30


class CoinGeckoError(RuntimeError):
    """Raised on CoinGecko API failures or missing data."""


@cached(source_id="coingecko.simple_price")
def simple_price(coin_id: str, fetch_ts: datetime | None = None) -> Decimal:
    """Spot USD price by CoinGecko coin ID (e.g. ``ethereum``, ``morpho``).

    `fetch_ts` is part of the cache key only (so re-runs at the same logical
    snapshot return the same cached result). The actual API call is always live.
    """
    _ = fetch_ts or datetime.now(tz=timezone.utc)
    r = requests.get(
        f"{API_BASE}/simple/price",
        params={"ids": coin_id, "vs_currencies": "usd"},
        timeout=DEFAULT_TIMEOUT,
    )
    r.raise_for_status()
    body = r.json()
    if coin_id not in body or "usd" not in body[coin_id]:
        raise CoinGeckoError(f"No price for {coin_id!r}: {body}")
    return Decimal(str(body[coin_id]["usd"]))
