"""ERC-7540 beneficial ownership through subscriptions and redemptions.

Vaults are discovered from the prime's own events and authenticated through
share()/asset(), including superseded vaults absent from today's pricing config.
Pending assets/shares stay attributed to their allocation until claimed.
"""
from collections import defaultdict
from decimal import Decimal

from ..domain.pricing import PricingCategory
from ..domain.primes import Address
from ..extract import rpc
from ..extract._keccak import keccak256
from ..extract.publication import optional_revert


def _topic(sig):
    return "0x" + keccak256(sig.encode()).hex()


DEPOSIT = _topic("Deposit(address,address,uint256,uint256)")
WITHDRAW = _topic("Withdraw(address,address,address,uint256,uint256)")
DEPOSIT_REQUEST = _topic("DepositRequest(address,address,uint256,address,uint256)")
REDEEM_REQUEST = _topic("RedeemRequest(address,address,uint256,address,uint256)")
CANCEL_DEPOSIT = _topic("CancelDepositClaim(address,address,uint256,address,uint256)")
CANCEL_REDEEM = _topic("CancelRedeemClaim(address,address,uint256,address,uint256)")
EVENTS = {DEPOSIT, WITHDRAW, DEPOSIT_REQUEST, REDEEM_REQUEST, CANCEL_DEPOSIT, CANCEL_REDEEM}


class AsyncVaultCapital:
    def __init__(self, chain, mapping):
        self.chain, self.mapping = chain, mapping
        self.metadata = {}
        self.active_vaults = {}
        self.pending = defaultdict(int)
        self.custody_accounts = defaultdict(list)

    def _read_address(self, vault, sig, block):
        try:
            with optional_revert():
                raw = rpc.eth_call(self.chain, Address.from_str(vault),
                                   "0x" + keccak256(sig.encode())[:4].hex(), block)
        except rpc.EVMRevert:
            return None
        return "0x" + raw[-40:] if len(raw) == 66 and int(raw, 16) else None

    def prepare(self, logs):
        selected = []
        for row in sorted(logs, key=lambda r: r.log_index):
            if row.topic0 not in EVENTS:
                continue
            who_topic = row.topic3 if row.topic0 == WITHDRAW else row.topic2
            if who_topic is None:
                continue
            owner = "0x" + who_topic[-40:]
            direct = (row.address, owner)
            if direct in self.mapping and self.mapping[direct].pricing_category == PricingCategory.ERC4626_VAULT:
                # Ordinary ERC-4626 deposits are handled by the token adapter.
                if row.topic0 in (DEPOSIT, WITHDRAW) and row.address not in self.metadata:
                    continue
                share = row.address
            else:
                if not any(v.pricing_category == PricingCategory.RWA_TRANCHE for v in self.mapping.values()):
                    continue
                share = self._read_address(row.address, "share()", row.block_number)
            if share is None or (share, owner) not in self.mapping:
                continue
            key = (share, owner)
            venue = self.mapping[key]
            asset = self._read_address(row.address, "asset()", row.block_number)
            if venue.underlying is None or asset != venue.underlying.address.hex:
                raise ValueError(f"Async capital vault asset mismatch: {row.address}")
            self.metadata[row.address] = key
            self.active_vaults[key] = row.address
            selected.append((row, key))
        return selected

    def price(self, key, block):
        vault = self.active_vaults.get(key)
        if vault is None:
            return None
        venue = self.mapping[key]
        # Avoid rounding a six-decimal share quote before multiplying by a
        # hundred-million-dollar position. This is an execution-vault quote.
        shares = 10 ** (venue.token.decimals + 12)
        assets = rpc.convert_to_assets(self.chain, Address.from_str(vault), shares, block)
        if assets <= 0:
            raise ValueError(f"No historical capital price at vault {vault}")
        return Decimal(assets) / Decimal(10 ** (venue.underlying.decimals + 12))

    def movements(self, selected):
        from .allocation_capital import AssetMovement

        movements = []
        deposit_amounts = defaultdict(Decimal)
        for row, key in selected:
            words = [int(row.data[i:i + 64], 16) for i in range(2, len(row.data), 64)]
            if len(words) != 2:
                raise ValueError("Invalid async capital event layout")
            venue = self.mapping[key]
            owner = key[1]
            deposit_side = row.topic0 in (DEPOSIT_REQUEST, DEPOSIT, CANCEL_DEPOSIT)
            side = "subscribe" if deposit_side else "redeem"
            pending_key = (row.address, owner, side)
            old = self.pending[pending_key]
            account = f"async:{self.chain.value}:{row.address}:{owner}:{side}"
            asset_scale = Decimal(10**venue.underlying.decimals)
            share_scale = Decimal(10**venue.token.decimals)
            if row.topic0 == DEPOSIT_REQUEST:
                delta = words[1]
                before, change = Decimal(old) / asset_scale, Decimal(delta) / asset_scale
            elif row.topic0 in (DEPOSIT, CANCEL_DEPOSIT):
                delta = -(words[0] if row.topic0 == DEPOSIT else words[1])
                if -delta > old:
                    raise ValueError(f"Async deposit claim exceeds tracked request: {row.transaction_hash}")
                before, change = Decimal(old) / asset_scale, Decimal(delta) / asset_scale
                if row.topic0 == DEPOSIT:
                    deposit_amounts[key] += -change
            elif row.topic0 == REDEEM_REQUEST:
                delta = words[1]
                price = self.price(key, row.block_number)
                before, change = Decimal(old) * price / share_scale, Decimal(delta) * price / share_scale
            else:
                delta = -words[1]
                if -delta > old:
                    raise ValueError(f"Async redemption exceeds tracked shares: {row.transaction_hash}; "
                                     f"venue={venue.id} expected={-delta} pending={dict(self.pending)}")
                if row.topic0 == WITHDRAW:
                    cash = Decimal(words[0]) / asset_scale
                    before, change = cash * Decimal(old) / Decimal(-delta), -cash
                else:
                    price = self.price(key, row.block_number)
                    before, change = Decimal(old) * price / share_scale, Decimal(delta) * price / share_scale
            self.pending[pending_key] += delta
            if account not in self.custody_accounts[venue.id]:
                self.custody_accounts[venue.id].append(account)
            movements.append(AssetMovement(account, before, change))
        return movements, deposit_amounts
