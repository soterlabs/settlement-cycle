"""Identify performance-fee shares separately from funded ERC4626 deposits."""
from collections import defaultdict

from ..domain.primes import Chain
from ..extract._keccak import keccak256
from ..extract.transfer_logs import TRANSFER_TOPIC0

# Limit this adapter to the reviewed MetaMorpho deployment. Do not infer fees
# from arbitrary ERC4626 mints or the size of a receipt.
VAULTS = {Chain.BASE: {'0x7bfa7c4f149e7415b73bdedfe609237e29cbf34a'}}
ACCRUE_INTEREST = '0x' + keccak256(b'AccrueInterest(uint256,uint256)').hex()
ZERO_TOPIC = '0x' + '0' * 64


def fee_mints(chain, rows, tracked):
    """Match each fee event to the immediately preceding mint it specifies.

    MetaMorpho._accrueFee emits Transfer(0, feeRecipient, feeShares), followed
    immediately by AccrueInterest(newTotalAssets, feeShares). Deposit shares
    are minted separately and must not be counted as fees.
    https://github.com/morpho-org/metamorpho/blob/ded84e59668155b34d3c24906c4f7461c12828af/src/MetaMorpho.sol
    Real Base example, with a third-party deposit and fees minted to Spark ALM:
    https://basescan.org/tx/0x5f2cc01503367db6dd55ff4a2c2b4fa5e561053dfd4182cdbc6c07399eff5efa
    """
    by_index = {r.log_index: r for r in rows}
    fees = defaultdict(int)
    for row in by_index.values():
        if row.address not in VAULTS.get(chain, set()) or row.topic0 != ACCRUE_INTEREST:
            continue
        if len(row.data) != 130:
            raise ValueError('Invalid MetaMorpho fee event')
        amount = int(row.data[66:], 16)
        if not amount:
            continue
        mint = by_index.get(row.log_index - 1)
        # The holder-filtered history need not contain mints to other fee
        # recipients. Only classify a payment actually observed to our ALM.
        if mint is None:
            continue
        if (mint.address == row.address and mint.topic0 == TRANSFER_TOPIC0
                and mint.topic1 == ZERO_TOPIC and mint.topic2
                and (row.address, '0x' + mint.topic2[-40:]) in tracked):
            if len(mint.data) != 66 or int(mint.data, 16) != amount:
                raise ValueError('MetaMorpho fee mint disagrees with accrual')
            fees[(row.address, '0x' + mint.topic2[-40:])] += amount
    return fees
