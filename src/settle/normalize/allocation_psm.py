"""Preserve borrowed basis while an ALM holds non-transferable PSM3 shares."""
from decimal import Decimal

from .sources.hypersync_psm3 import DEPOSIT, WITHDRAW


class PsmCapital:
    def __init__(self, prime, chain):
        self.config = prime.psm.get(chain)
        self.holder = prime.alm[chain]
        self.shares = 0
        self.account = (f"psm:{chain.value}:{self.config.address.hex}:{self.holder.hex}"
                        if self.config else None)

    def movements(self, logs, asset_value):
        from .allocation_capital import AssetMovement

        if self.config is None:
            return []
        who = '0x' + self.holder.value.hex().rjust(64, '0')
        deposits = withdrawals = 0
        paid = received = Decimal(0)
        for row in logs:
            if row.address != self.config.address.hex or row.topic0 not in (DEPOSIT, WITHDRAW):
                continue
            owner = row.topic3 if row.topic0 == DEPOSIT else row.topic2
            if owner != who:
                continue
            raw = row.data.removeprefix('0x')
            if len(raw) != 128 or row.topic1 is None:
                raise ValueError('Invalid PSM3 capital event')
            amount, shares = int(raw[:64], 16), int(raw[64:], 16)
            if shares <= 0:
                if amount:
                    raise ValueError('PSM3 capital amount without shares')
                continue
            value = asset_value('0x' + row.topic1[-40:], amount)
            if row.topic0 == DEPOSIT:
                deposits += shares
                paid += value
            else:
                withdrawals += shares
                received += value
        if not deposits and not withdrawals:
            return []
        if self.shares + deposits < withdrawals:
            raise ValueError('PSM3 withdrawal exceeds reconstructed shares')
        # Use actual paid/received assets, avoiding a rounded one-share quote.
        # For an exit, price the opening holding at the realized exchange rate.
        if withdrawals:
            before = (received if self.shares == withdrawals else
                      received * Decimal(self.shares) / Decimal(withdrawals))
        else:
            before = paid * Decimal(self.shares) / Decimal(deposits)
        self.shares += deposits - withdrawals
        return [AssetMovement(self.account, before, paid - received, preserve_basis=True)]
