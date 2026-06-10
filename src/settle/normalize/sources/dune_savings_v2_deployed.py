"""On-chain ``ISavingsV2DeployedSource`` — reads ``spUSDC_V2.totalAssets``.

For Spark S32 (Ethereum raw sUSDS POL), the ALM's ``balanceOf(sUSDS)``
reading mixes two slices:

* **Debt-sourced** — true POL (Spark drew USDS from the ilk), plus the
  sUSDS held at the ALM as collateral backing for spUSDT / spPYUSD / spETH
  Savings V2 vault depositors. The depositor capital in those vaults
  arrives as USDT / PYUSD / ETH, which is collateralised via ``vat.frob``
  to mint NEW ilk debt → USDS → sUSDS. The new debt enters ``cum_debt``
  and BR is correctly charged on it via the standard machinery. From the
  prime ↔ Sky settlement perspective, these slices look exactly like
  clean POL — Case 1 logic applies.

* **Depositor-sourced** — the slice backing the spUSDC V2 vault. spUSDC
  depositors send USDC, which is swapped to USDS via PSM (a non-borrowing
  operation — no new ilk debt), then to sUSDS. The SSR appreciation on
  this slice belongs to spUSDC depositors via the vault's pricePerShare;
  it does NOT belong to the prime/Sky pair. Crediting Spark for this
  SSR would over-credit by ``SSR × spUSDC_TA`` per period.

This source returns the **USD-equivalent of the depositor-sourced
slice at a given block** — i.e. ``spUSDC_V2.totalAssets(block)``,
denominated in USDC at par. The consumer subtracts this from S32's
``value_som`` / ``value_eom`` before computing per-period revenue,
isolating the debt-sourced slice for the standard Case 1 logic.

Other Savings V2 vaults DO NOT need a carve-out:

* spUSDT / spPYUSD / spETH (V2) — debt-sourced (``vat.frob`` against
  collateral mints new USDS, included in ``cum_debt``).
* sUSDC V1 (``0xBc65…45FE``) — also PSM-routed but holds its sUSDS at
  the vault contract itself (NOT at the ALM), so it doesn't enter
  S32's reading in the first place.

Re-classification context: see PRD ``docs/PRD_revenue_gross_net_audit.md``
§10 (Case 2).
"""

from __future__ import annotations

import logging
from decimal import Decimal

from ...domain.primes import Address, Chain
from ...extract.rpc import eth_call

_log = logging.getLogger(__name__)


# spUSDC V2 vault — the only depositor-sourced sUSDS slice that ends up
# at the Spark Eth ALM. Other Savings V2 vaults either route through
# ``vat.frob`` (= debt-sourced, no carve-out needed) or hold their own
# sUSDS at the vault contract (V1 sUSDC, doesn't enter S32's reading).
_SPUSDC_V2 = Address.from_str("0x28B3a8fb53B741A8Fd78c0fb9A6B2393d896a43d")

# ERC-4626 ``totalAssets()`` selector — returns the underlying-asset
# amount the vault currently owes its depositors. For spUSDC V2 the
# underlying is USDC (par-stable), so the returned value is directly
# the USD-equivalent.
_SEL_TOTAL_ASSETS = "0x01e1d114"

# spUSDC underlying is USDC (6 decimals).
_USDC_SCALE = Decimal(10**6)


class DuneSavingsV2DeployedSource:
    """On-chain ``ISavingsV2DeployedSource``.

    Despite the legacy name (kept for protocol compatibility), this is
    an RPC-backed implementation — the original Dune table
    ``dune.sparkdotfi.result_savings_v_2_deployment_metrics`` is no
    longer accessible, and direct ``totalAssets`` reads are simpler and
    more precise anyway (no daily Dune ingestion lag).
    """

    def at_block(self, block: int) -> Decimal:
        """USD-equivalent depositor-sourced sUSDS at the given block.

        Computed as ``spUSDC_V2.totalAssets(block)`` denominated in
        USDC at par. Returns ``Decimal("0")`` if the call reverts
        (e.g. block is before vault deployment) so the deduction
        degrades gracefully — preserves the pre-fix behaviour for
        pre-deployment blocks while picking up the correct carve-out
        once the vault exists.
        """
        try:
            raw = eth_call(Chain.ETHEREUM, _SPUSDC_V2, _SEL_TOTAL_ASSETS, block)
        except Exception as e:
            _log.warning(
                "DuneSavingsV2DeployedSource.at_block(%d) failed (%s) — "
                "treating depositor-sourced slice as 0 for this block. "
                "The consumer (``_savings_v2_depositor_ssr``) carries the "
                "previous day's value forward when a 0 follows a non-zero "
                "day, so a transient mid-series outage degrades to a "
                "carry-forward, not a dropped day.",
                block, e,
            )
            return Decimal("0")
        return Decimal(int(raw, 16)) / _USDC_SCALE
