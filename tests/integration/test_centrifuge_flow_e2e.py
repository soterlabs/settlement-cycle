"""End-to-end test for erc4626_centrifuge_flow.sql against Dune.

The unit tests for ``_erc4626_event_inflow_timeseries`` stub ``execute_query``,
so they cover parameter wiring but do NOT exercise the SQL itself.  This test
guards against silent SQL regressions of the same kind that affected
``merkl_claims_ethereum.sql`` (wrong topic2 filter → $0 silently).

Concretely, the query filters on:

    topic0 = Deposit/Withdraw event signature  (two different hashes)
    topic1 = sender  (ALM address, for Deposit)
    topic2 = receiver (ALM address, for Withdraw)

Any of these could be mis-wired while unit tests remain green.

Pinned against the one event known to have occurred for E8 (JAAA) in
March 2026:

    2026-03-11  block 24,634,700  Withdraw
    tx 0x604f2079af65f48a962e36e8d4bc30f2e7116b70ad685f84c5bcfde574f1c503
    assets_raw = 326,858,573,655,125   (raw 6-decimal USDC integer)
    → daily_inflow = −$326,858,573.655125

    Source: verified via inspect_e8_txns.py against the Alchemy RPC +
    confirmed via Dune ethereum.logs query 2026-05-22.

Live, gated behind ``@pytest.mark.live`` AND ``DUNE_API_KEY``.
Default ``pytest`` runs skip it.  Run explicitly:

    DUNE_API_KEY=... pytest tests/integration/test_centrifuge_flow_e2e.py -m live -v -s
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from settle.domain import Period
from settle.domain.config import load_prime
from settle.domain.primes import Chain
from settle.normalize.positions import _erc4626_event_inflow_timeseries

_REPO = Path(__file__).resolve().parents[2]

# March 2026 EOM pin block (last ETH block on 2026-03-31 23:59:59 UTC)
_MAR_2026_EOM_BLOCK = 24_781_026

# The single Withdraw event on 2026-03-11: assets_raw / 1e6
_EXPECTED_WITHDRAWAL_USDC = Decimal("326858573.655125")
# Same event's shares field — raw uint256, 6 share-decimals on JAAA
_EXPECTED_WITHDRAWAL_SHARES_RAW = 318_545_940_695_568

# Raw uint256 values divided by 10^6 are exact in Decimal; allow only sub-cent
# drift to absorb any DataFrame-cast quirks.
_TOLERANCE = Decimal("0.001")


@pytest.mark.live
def test_erc4626_flow_sql_returns_march_withdrawal_for_e8():
    """The SQL must return the known March 11 Withdraw event for E8 JAAA.

    This test catches SQL regressions such as:
      - wrong topic0 (event signature hash) → returns 0 rows
      - wrong topic2 (receiver filter) → misses the Withdraw
      - wrong data slice offsets → assets_raw decodes to garbage
      - wrong ``concat(0x000000..., {{holder}})`` padding → no rows matched

    The ``_erc4626_event_inflow_timeseries`` function is called directly
    (same as the Merkl e2e calls ``_merkl_claims_revenue_usd``) so the
    test exercises the full stack from Dune query to DataFrame output.
    """
    if not os.environ.get("DUNE_API_KEY"):
        pytest.skip("DUNE_API_KEY not set — required to hit ethereum.logs on Dune")

    prime = load_prime(_REPO / "config" / "grove.yaml")

    venues_by_id = {v.id: v for v in prime.venues}
    e8 = venues_by_id.get("E8")
    assert e8 is not None, "E8 not found in grove.yaml"
    assert e8.centrifuge_vault is not None, (
        "E8 has no centrifuge_vault — config may have changed"
    )

    period = Period(
        start=date(2026, 3, 1),
        end=date(2026, 3, 31),
        pin_blocks={Chain.ETHEREUM: _MAR_2026_EOM_BLOCK},
    )

    df = _erc4626_event_inflow_timeseries(
        prime, e8, period, block_resolver=None,
    )

    assert not df.empty, (
        "erc4626_centrifuge_flow.sql returned no rows for E8 March 2026 — "
        "check topic0/topic1/topic2 filters in the SQL"
    )

    # March 2026 should contain exactly one event: the March 11 Withdraw.
    march_rows = df[
        (df["block_date"] >= date(2026, 3, 1)) &
        (df["block_date"] <= date(2026, 3, 31))
    ]
    assert len(march_rows) == 1, (
        f"Expected 1 row for March 2026 E8, got {len(march_rows)}:\n{march_rows}"
    )

    row = march_rows.iloc[0]
    assert row["block_date"] == date(2026, 3, 11), (
        f"Expected event on 2026-03-11, got {row['block_date']}"
    )

    # Withdraw → daily_inflow is negative (assets left the ALM)
    daily_inflow = Decimal(str(row["daily_inflow"]))
    assert daily_inflow < 0, (
        f"Withdraw should produce negative daily_inflow, got {daily_inflow}"
    )
    assert abs(daily_inflow + _EXPECTED_WITHDRAWAL_USDC) <= _TOLERANCE, (
        f"March 11 daily_inflow ${daily_inflow:,.6f} differs from expected "
        f"-${_EXPECTED_WITHDRAWAL_USDC:,.6f} by more than ${_TOLERANCE}"
    )

    # Period net inflow = just this one withdrawal
    period_net = Decimal(str(df["daily_inflow"].sum()))
    assert abs(period_net + _EXPECTED_WITHDRAWAL_USDC) <= _TOLERANCE, (
        f"March 2026 period net inflow ${period_net:,.6f} ≠ expected "
        f"-${_EXPECTED_WITHDRAWAL_USDC:,.6f}"
    )

    # Raw shares decoding — pins the second 32-byte slice of `data` in the
    # SQL (``substr(data, 33, 32)``).  A wrong offset here would still produce
    # a valid integer but with the wrong value, and would not be caught by
    # the assets assertions above.
    daily_net_shares_raw = int(row["daily_net_shares_raw"])
    assert daily_net_shares_raw == -_EXPECTED_WITHDRAWAL_SHARES_RAW, (
        f"daily_net_shares_raw {daily_net_shares_raw} ≠ expected "
        f"{-_EXPECTED_WITHDRAWAL_SHARES_RAW} (Withdraw decoded as negative)"
    )

    print()
    print(f"E8 JAAA  March 2026  event date: {row['block_date']}")
    print(f"  daily_inflow   : ${daily_inflow:>20,.6f}")
    print(f"  period net     : ${period_net:>20,.6f}")
    print(f"  expected       : ${-_EXPECTED_WITHDRAWAL_USDC:>20,.6f}")
    print(f"  shares (raw)   : {daily_net_shares_raw:>20,}")
