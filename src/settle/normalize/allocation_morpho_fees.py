"""Identify performance-fee shares separately from funded ERC4626 deposits."""
from collections import defaultdict

from ..domain.primes import Chain
from ..extract._keccak import keccak256
from ..extract.transfer_logs import TRANSFER_TOPIC0

# Limit this adapter to the reviewed MetaMorpho deployment. Do not infer fees
# from arbitrary ERC4626 mints or the size of a receipt.
VAULTS = {
    Chain.BASE: {'0x7bfa7c4f149e7415b73bdedfe609237e29cbf34a'},
    # Spark Blue Chip USDC and the DAI/USDS vaults use MetaMorpho V1. Fee mints to
    # the ALM and the immediately following AccrueInterest authenticate
    # this income; feeRecipient() independently agrees at the August pin.
    Chain.ETHEREUM: {
        '0x56a76b428244a50513ec81e225a293d128fd581d',
        '0x73e65dbd630f90604062f6e02fab9138e713edd9',
        '0xe41a0583334f0dc4e023acd0bfef3667f6fe0597',
    },
}
ACCRUE_INTEREST = '0x' + keccak256(b'AccrueInterest(uint256,uint256)').hex()
ACCRUE_INTEREST_V2 = '0x' + keccak256(b'AccrueInterest(uint256,uint256,uint256,uint256)').hex()
# Both Spark USDT vaults have the ALM as performance-fee recipient; the
# migration spell explicitly checks their matching 10% performance fee:
# https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20260507/Spell_20260507.t.sol
V2_VAULTS = {Chain.ETHEREUM: {
    '0xc7cdcfdefc64631ed6799c95e3b110cd42f2bd22',
    '0xb0c424116172b55cbb6dd3136f5989f7959e5b91',
}}
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
    if len(by_index) != len(rows):
        raise ValueError('Duplicate Morpho fee event index')
    fees = defaultdict(int)
    for row in by_index.values():
        if row.address in V2_VAULTS.get(chain, set()) and row.topic0 == ACCRUE_INTEREST_V2:
            if len(row.data) != 258:
                raise ValueError('Invalid Morpho V2 fee event')
            # V2 emits AccrueInterest FIRST, then nonzero performance and
            # management fee mints, in that order. Missing other-holder mints
            # still occupy their log positions in holder-filtered histories.
            # https://github.com/morpho-org/vault-v2/blob/f19803940f3d690a6dc6d4a0a2edfa39fc5203aa/src/VaultV2.sol#L609
            next_index = row.log_index + 1
            for amount in (int(row.data[130:194], 16), int(row.data[194:258], 16)):
                if not amount:
                    continue
                mint = by_index.get(next_index)
                next_index += 1
                if mint is None:
                    continue
                if (mint.address == row.address and mint.topic0 == TRANSFER_TOPIC0
                        and mint.topic1 == ZERO_TOPIC and mint.topic2
                        and (row.address, '0x' + mint.topic2[-40:]) in tracked):
                    if (mint.transaction_hash != row.transaction_hash or len(mint.data) != 66
                            or int(mint.data, 16) != amount):
                        raise ValueError('Morpho V2 fee mint disagrees with accrual')
                    fees[(row.address, '0x' + mint.topic2[-40:])] += amount
            continue
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
            if (mint.transaction_hash != row.transaction_hash or len(mint.data) != 66
                    or int(mint.data, 16) != amount):
                raise ValueError('MetaMorpho fee mint disagrees with accrual')
            fees[(row.address, '0x' + mint.topic2[-40:])] += amount
    return fees
