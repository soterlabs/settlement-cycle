"""Economic exposure during asynchronous subscriptions and redemptions.

Atlas A.2.4.2.3: deposits enter on share issuance, redemptions leave on cash
settlement. Collection of already-issued shares is only a custody transfer.
Protocol adapters supply balances; valuation and SDE apply the same identity.
"""
from dataclasses import dataclass
from decimal import Decimal

from ..domain import Address
from ..domain.primes import Prime, Venue

ZERO = Decimal(0)
SEL_ASYNC_MANAGER = "0xb7570e0c"  # asyncManager()
SEL_INVESTMENTS = "0x97780020"  # investments(address,address)


@dataclass(frozen=True)
class IssuedDeposit:
    shares: Decimal = ZERO
    capital_usd: Decimal = ZERO


@dataclass(frozen=True)
class AsyncVaultExposure:
    deposit: IssuedDeposit = IssuedDeposit()
    redemption_shares: Decimal = ZERO
    redemption_cash: Decimal = ZERO

    @property
    def shares(self) -> Decimal:
        return self.deposit.shares + self.redemption_shares

    def value(self, nav: Decimal) -> Decimal:
        return self.shares * nav + self.redemption_cash


def decode_issued_deposit(raw: str, share_decimals: int, asset_decimals: int) -> IssuedDeposit:
    """Decode the verified async manager's investment-state ABI and cost basis."""
    if not raw.startswith("0x") or len(raw) != 2 + 64 * 10:
        raise ValueError("Invalid async investment state (expected ten ABI words)")
    words = [int(raw[i:i + 64], 16) for i in range(2, len(raw), 64)]
    if any(n >= 2**128 for n in words[:8]) or any(n not in (0, 1) for n in words[8:]):
        raise ValueError("Invalid async investment state field")
    shares_raw, price = words[0], words[2]
    if shares_raw and not price:
        raise ValueError("Issued deposit has no acquisition price")
    assets_raw = (shares_raw * price * 10**asset_decimals) // (10**18 * 10**share_decimals)
    return IssuedDeposit(
        Decimal(shares_raw) / Decimal(10**share_decimals),
        Decimal(assets_raw) / Decimal(10**asset_decimals),
    )


def issued_deposit(prime: Prime, venue: Venue, block: int) -> IssuedDeposit:
    """Already-issued shares allocated to the controller but not yet collected.

    Read the deployed manager's *unfiltered* investment state: maxMint() and
    maxDeposit() can return zero solely because transfers are restricted.
    Pending deposits and cancelled cash are deliberately excluded. The stored
    deposit price is the weighted acquisition basis, not today's NAV.
    Neither contract addresses nor historical windows are hardcoded.
    """
    if venue.centrifuge_vault is None:
        return IssuedDeposit()
    from ..extract import rpc
    from ..extract._abi import pad_address

    holder = venue.holder_override or prime.alm.get(venue.chain)
    if holder is None:
        raise ValueError(f"No async-vault controller for {prime.id}/{venue.id}")
    try:
        if not rpc.is_contract_deployed(venue.chain, venue.centrifuge_vault, block):
            return IssuedDeposit()
        raw_manager = rpc.eth_call(venue.chain, venue.centrifuge_vault, SEL_ASYNC_MANAGER, block)
        if len(raw_manager) != 66 or int(raw_manager, 16) == 0 or int(raw_manager, 16) >= 2**160:
            raise ValueError("Invalid asyncManager address")
        manager = Address.from_str("0x" + raw_manager[-40:])
        data = SEL_INVESTMENTS + pad_address(venue.centrifuge_vault) + pad_address(holder)
        raw = rpc.eth_call(venue.chain, manager, data, block)
        underlying_decimals = venue.underlying.decimals if venue.underlying else 6
        # Match PricingLib.shareToAssetAmount(..., Rounding.Down), including
        # differing share/underlying decimals, with integer arithmetic.
        return decode_issued_deposit(raw, venue.token.decimals, underlying_decimals)
    except Exception as exc:
        raise RuntimeError(
            f"Issued deposit read failed for {prime.id}/{venue.id} at block {block}; "
            "refusing to assume zero in-transit exposure"
        ) from exc


def async_vault_exposure(
    prime: Prime, venue: Venue, block: int, *, redemption_cash: Decimal = ZERO,
) -> AsyncVaultExposure:
    from .positions import _centrifuge_in_flight_shares

    return AsyncVaultExposure(
        deposit=issued_deposit(prime, venue, block),
        redemption_shares=_centrifuge_in_flight_shares(prime, venue, block),
        redemption_cash=redemption_cash,
    )


def deposit_capital_adjustment(
    prime: Prime, venue: Venue, opening_block: int, closing_block: int,
) -> Decimal:
    """Move capital recognition from collection to issuance at period boundaries.

    Vault Deposit events still measure actual collection cash amounts. Adding
    closing minus opening uncollected acquisition basis recognizes issued
    capital exactly once, including partial claims and cross-month issuance.
    """
    return (issued_deposit(prime, venue, closing_block).capital_usd
            - issued_deposit(prime, venue, opening_block).capital_usd)
