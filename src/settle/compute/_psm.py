"""Per-chain PSM contract addresses.

USDS the prime has parked at PSM (net of withdrawals) is treated as idle USDS
in ``compute_sky_revenue.utilized`` — the prime is reimbursed the base rate
on the parked balance per prime-settlement-methodology Step 2.

Currently registered: Spark PSM on Ethereum (Spark uses this as the
USDS↔USDC routing module). Add other chains as primes onboard PSMs there.
"""

from __future__ import annotations

from ..domain.primes import Address, Chain

PSM_BY_CHAIN: dict[Chain, Address] = {
    Chain.ETHEREUM: Address.from_str("0x37305b1cd40574e4c5ce33f8e8306be057fd7341"),
}
