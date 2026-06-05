"""Spark Savings V2 — VSR-liability accrual per vault per period.

Per ``docs/spark/PRD_savings_vaults.md`` §3, every S2 vault (spUSDC, spUSDT,
spPYUSD, ...) has:

* **An asset side** — the deployed underlying which routes to the Spark
  ALM. The gross yield on this is already captured by Spark's existing
  Cat A / B / C / E venues (S1-S55).
* **A liability side** — the depositor claim, which grows daily at the
  Vault Savings Rate (VSR). Each ``spUSDC`` share's price-per-share
  appreciates each day; the corresponding interest is what Spark owes
  the depositor and is the cost that must be subtracted from
  ``prime_agent_revenue``.

The on-chain ``ERC-4626 totalAssets()`` already equals ``totalSupply() ×
pps``. The VSR accrual on day *d* is the change in ``totalAssets`` that
is **attributable to pps growth** rather than to net deposit flows.

Working formula (daily, exact):

.. code-block::

    vsr_liability_d = totalSupply(d-1) × (pps(d) − pps(d-1))

Equivalent, but written in terms of ``totalAssets``:

.. code-block::

    vsr_liability_d = totalAssets(d) − totalAssets(d-1) × (totalSupply(d) / totalSupply(d-1))

We use the first form because reading ``convertToAssets(10**dec)`` (= pps)
+ ``totalSupply()`` at each EoD block gives a direct, decimal-stable
expression of the depositor accrual; ``totalAssets()`` is only needed for
the per-day display value (returned alongside the liability so the
caller can populate ``value_eom``).

Caveats
-------
* This deliberately **does not** add back the deployment yield. That's
  already in Spark's other venues; double-counting is the failure mode
  the prior design was at risk of (see PRD §3.4).
* Avalanche S60 is supported by the same code path; the caller passes
  the right chain.
* spETH (S58) is out of scope per the PRD (different backing model).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ..domain.primes import Address, Chain, Venue
from ..extract import rpc as _rpc

_log = logging.getLogger(__name__)


def _pps(
    chain: Chain,
    vault: Address,
    share_decimals: int,
    underlying_decimals: int,
    block: int,
) -> Decimal:
    """Price-per-share = ``convertToAssets(10**share_decimals) / 10**underlying_decimals``.

    ERC-4626's ``convertToAssets(shares)`` takes a value in share-token units
    and returns a value in **underlying-token** units. Mixing the two
    decimals up silently mis-scales the result when share decimals don't
    match underlying decimals (every in-scope SSV vault today is 6/6, so
    the difference is a latent zero; a future 18-decimal share token
    backed by 6-decimal USDC would mis-price by 10**12).

    Returns a Decimal in **human assets-per-share** units.
    """
    one_share = 10 ** share_decimals
    assets_raw = _rpc.convert_to_assets(chain, vault, one_share, block)
    return Decimal(assets_raw) / Decimal(10 ** underlying_decimals)


def compute_vsr_liability_period(
    venue: Venue,
    period_start: date,
    period_end: date,
    *,
    block_resolver: Any,
) -> tuple[Decimal, Decimal, Decimal]:
    """VSR-liability accrual + display values for one S2 vault over a period.

    Returns ``(vsr_liability_usd, total_assets_som_usd, total_assets_eom_usd)``.

    * ``vsr_liability_usd`` is the **negative** revenue line: the depositor
      interest Spark accrued in the period. Caller wraps it into
      ``VenueRevenueInputs.actual_revenue_override = -vsr_liability``.
    * ``total_assets_som_usd`` / ``_eom_usd`` are the depositor liability
      (= the dollar value of the vault's full share book) at the period
      boundaries — surfaced so the venue's ``value_som`` / ``value_eom``
      reflect what BA Labs (and Spark's own reporting) display.

    Daily accrual: at each EoD block between ``period_start - 1`` and
    ``period_end``, read ``convertToAssets(10^decimals)`` (pps) and
    ``totalSupply()``. Liability_d = totalSupply(d-1) × (pps(d) - pps(d-1)).

    The 24-hour anchor and the use of EoD blocks matches the rest of the
    pipeline. Reads are cached at the RPC primitive layer, so re-runs
    inside a billing-cycle window are free.
    """
    chain = venue.chain
    vault = venue.token.address
    share_decimals = venue.token.decimals
    if venue.underlying is None:
        raise ValueError(
            f"compute_vsr_liability_period: venue {venue.id} has no "
            "``underlying`` — required to scale ``convertToAssets`` output."
        )
    underlying_decimals = venue.underlying.decimals

    # Build the daily date list, INCLUDING the day before period.start so
    # we have a baseline pps_{d-1} for the first daily accrual. Cap dates
    # at period_end inclusive.
    days = []
    d = period_start - timedelta(days=1)
    while d <= period_end:
        days.append(d)
        d += timedelta(days=1)

    # Resolve EoD block for each day. The fixture-backed resolver covers
    # Spark's daily blocks for Ethereum + Avalanche-C; if the day is
    # outside the fixture window, the loader falls back to RPC binary
    # search (slow but correct).
    from datetime import datetime, time, timezone
    blocks: list[int] = []
    for d in days:
        anchor = datetime.combine(d, time.max, tzinfo=timezone.utc)
        blocks.append(block_resolver.block_at_or_before(chain.value, anchor))

    # Read pps + totalSupply at each block. ``convertToAssets`` and
    # ``total_supply_of`` are both cached RPC primitives.
    pps_series: list[Decimal] = []
    supply_series: list[Decimal] = []
    for blk in blocks:
        pps_series.append(_pps(chain, vault, share_decimals, underlying_decimals, blk))
        ts_raw = _rpc.total_supply_of(chain, vault, blk)
        supply_series.append(Decimal(ts_raw) / Decimal(10 ** share_decimals))

    # Sanity check on the baseline pps: a zero baseline combined with a
    # non-zero day-1 pps would produce a phantom day-1 accrual of
    # ``supply_d0 × pps_d1`` — pinning the liability at a billion-dollar
    # level on the first day. The only legitimate reason for pps==0 at
    # ``period_start - 1`` is that the vault hadn't been deployed yet;
    # in that case ``supply_d0`` is also 0 and the math degenerates to 0.
    # Anything else (transient RPC issue, cache poisoning) is a data
    # anomaly we'd rather hard-fail on than silently book.
    if pps_series[0] == 0 and supply_series[0] > 0:
        raise RuntimeError(
            f"compute_vsr_liability_period: pps baseline is 0 at block "
            f"{blocks[0]} but totalSupply > 0 (= {supply_series[0]}). "
            "Likely a cached RPC zero or partial chain state. Refusing to "
            "compute a corrupted liability."
        )

    # Daily accrual: liability_d = totalSupply(d-1) × (pps(d) - pps(d-1)).
    # Index 0 in days/pps/supply is period_start - 1 (the baseline);
    # the first accrual is between index 0 and 1.
    #
    # ``dpps`` semantics:
    # * ``dpps == 0`` is a legitimate quantization day (no observable
    #   accrual at 6-decimal pps resolution). Contributes 0 naturally.
    # * ``dpps < 0`` is *not* expected on a chi-style monotonic-VSR
    #   index — it indicates either a block-resolver out-of-order
    #   anomaly, an RPC hiccup, or a contract upgrade. Skip the day and
    #   warn loudly so an auditor can investigate. We don't synthesize
    #   negative liability (Spark's depositor obligation can't go down
    #   from VSR mechanics alone).
    liability = Decimal("0")
    for i in range(1, len(days)):
        dpps = pps_series[i] - pps_series[i - 1]
        if dpps < 0:
            _log.warning(
                "  [savings_v2] %s: pps decreased on %s (blocks %d → %d, "
                "Δpps=%s) — unexpected for a chi-style vault; skipping day. "
                "Verify block-resolver ordering or contract upgrade.",
                venue.id, days[i], blocks[i - 1], blocks[i], f"{dpps:.18f}",
            )
            continue
        liability += supply_series[i - 1] * dpps

    # Display values at period boundaries (= totalAssets read directly —
    # equal to ``totalSupply × pps`` by ERC-4626 definition). Scaled by
    # underlying decimals because ``totalAssets()`` returns underlying
    # units, not share units.
    ta_som_raw = _rpc.total_assets_of(chain, vault, blocks[0])
    ta_eom_raw = _rpc.total_assets_of(chain, vault, blocks[-1])
    total_assets_som = Decimal(ta_som_raw) / Decimal(10 ** underlying_decimals)
    total_assets_eom = Decimal(ta_eom_raw) / Decimal(10 ** underlying_decimals)

    _log.info(
        "  [savings_v2] %s (%s on %s): "
        "vsr_liability=$%s  total_assets_som=$%s  total_assets_eom=$%s",
        venue.id, venue.token.symbol, chain.value,
        f"{liability:,.2f}", f"{total_assets_som:,.2f}", f"{total_assets_eom:,.2f}",
    )

    return liability, total_assets_som, total_assets_eom
