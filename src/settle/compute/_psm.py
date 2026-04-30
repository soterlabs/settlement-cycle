"""PSM-tracking helpers (replaces the old hardcoded ``PSM_BY_CHAIN`` dict).

USDS the prime has parked at a PSM (net of withdrawals) is treated as idle USDS
in ``compute_sky_revenue.utilized`` — the prime is reimbursed the base rate on
the parked balance per prime-settlement-methodology Step 2.

PSM addresses now live in each prime's YAML (``addresses.<chain>.psm``) so the
Spark / Grove / future-prime PSMs are configured per-prime instead of a
module-level global. See ``Prime.psm`` and ``PsmConfig``.

Two PSM mechanics are supported:

* ``PsmKind.DIRECTED_FLOW`` — Sky LITE-PSM-USDC pattern (Grove / OBEX / Spark
  on Ethereum). The PSM is a USDS↔USDC swap conduit; it doesn't issue shares.
  We track ``(subproxy + ALM) → PSM`` minus ``PSM → (subproxy + ALM)`` in the
  underlying token (USDS, par-stable).

* ``PsmKind.ERC4626_SHARES`` — Spark PSM3 pattern (Base / Arbitrum / Optimism
  / Unichain). The PSM3 is a basket vault holding USDC + USDS + sUSDS shares;
  the ALM holds shares of the PSM3 contract itself. USDS-equivalent value
  ``= balance_of(psm3, alm, block) × convertToAssets(10**18, block) / 10**18``.
"""

from __future__ import annotations

from ..domain.primes import Address  # re-exported for callers; new code should import from domain
