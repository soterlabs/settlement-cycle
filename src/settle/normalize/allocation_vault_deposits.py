"""Price actual ERC-4626 deposits without dropping other incoming shares.

Spark's April 29, 2026 transaction both deposited USDS into sUSDS and bought
additional sUSDS for USDT. Substituting only Deposit.assets for the transaction's
entire positive share change discarded the purchased shares:
https://etherscan.io/tx/0xbf6382cb7c44bb47b75366e1d7ed5bc1959af028521a83a444cd6c4aae268683
"""

from decimal import Decimal


def deposit_transaction_value(*, cash, deposited_shares, fee_shares, net_shares, price, scale):
    """For transactions with no outgoing shares; fee mints remain income.

    Cash is already in USD. Only the shares covered by Deposit events use that
    acquisition cost. Other received shares retain their execution-time NAV.
    """
    extra = net_shares - deposited_shares - fee_shares
    if extra < 0:
        raise ValueError("ERC4626 deposit and fee shares exceed incoming shares")
    return cash + Decimal(fee_shares + extra) * price / scale
