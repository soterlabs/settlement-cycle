"""Economic exposure during asynchronous subscriptions and redemptions.

Atlas A.2.4.2.3: deposits enter on share issuance, redemptions leave on cash
settlement. Collection of already-issued shares is only a custody transfer.
Protocol adapters supply balances; valuation and SDE apply the same identity.
"""
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pandas as pd

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


def decode_async_vault_exposure(raw: str, share_decimals: int, asset_decimals: int) -> AsyncVaultExposure:
    """Decode ownership, not permission to transfer, from the manager state.

    Pending and cancelled redemptions still own shares. Processed redemptions
    own a fixed underlying-asset amount, independent of subsequent share NAV.
    """
    if not raw.startswith("0x") or len(raw) != 2 + 64 * 10:
        raise ValueError("Invalid async investment state (expected ten ABI words)")
    words = [int(raw[i:i + 64], 16) for i in range(2, len(raw), 64)]
    if any(n >= 2**128 for n in words[:8]) or any(n not in (0, 1) for n in words[8:]):
        raise ValueError("Invalid async investment state field")
    shares_raw, price = words[0], words[2]
    if shares_raw and not price:
        raise ValueError("Issued deposit has no acquisition price")
    assets_raw = (shares_raw * price * 10**asset_decimals) // (10**18 * 10**share_decimals)
    deposit = IssuedDeposit(
        Decimal(shares_raw) / Decimal(10**share_decimals),
        Decimal(assets_raw) / Decimal(10**asset_decimals),
    )
    return AsyncVaultExposure(
        deposit=deposit,
        redemption_shares=Decimal(words[5] + words[7]) / Decimal(10**share_decimals),
        redemption_cash=Decimal(words[1]) / Decimal(10**asset_decimals),
    )


def decode_issued_deposit(raw: str, share_decimals: int, asset_decimals: int) -> IssuedDeposit:
    """Acquisition basis used by the offline deposit replay."""
    return decode_async_vault_exposure(raw, share_decimals, asset_decimals).deposit


def async_vault_exposure(
    prime: Prime, venue: Venue, block: int, *, redemption_cash: Decimal | None = None,
) -> AsyncVaultExposure:
    """All controller-owned shares and cash still outside the wallet.

    Read the deployed manager's *unfiltered* investment state: maxMint() and
    maxDeposit() can return zero solely because transfers are restricted.
    The same applies to maxRedeem/maxWithdraw: restrictions do not extinguish
    a redemption claim. Pending deposits and cancelled deposit cash remain
    excluded. The deposit price is weighted acquisition basis, not today's NAV.
    Neither contract addresses nor historical windows are hardcoded.
    """
    if venue.centrifuge_vault is None:
        if redemption_cash is None:
            from .redemption_settlements import pending_redemption_value_at_block
            redemption_cash = pending_redemption_value_at_block(prime, venue, block)
        return AsyncVaultExposure(redemption_cash=redemption_cash)
    if redemption_cash:
        raise ValueError('Cannot combine async manager and external cash-ledger exposure')
    from ..extract import rpc
    from ..extract._abi import pad_address

    holder = venue.holder_override or prime.alm.get(venue.chain)
    if holder is None:
        raise ValueError(f"No async-vault controller for {prime.id}/{venue.id}")
    try:
        if not rpc.is_contract_deployed(venue.chain, venue.centrifuge_vault, block):
            return AsyncVaultExposure()
        raw_manager = rpc.eth_call(venue.chain, venue.centrifuge_vault, SEL_ASYNC_MANAGER, block)
        if len(raw_manager) != 66 or int(raw_manager, 16) == 0 or int(raw_manager, 16) >= 2**160:
            raise ValueError("Invalid asyncManager address")
        manager = Address.from_str("0x" + raw_manager[-40:])
        data = SEL_INVESTMENTS + pad_address(venue.centrifuge_vault) + pad_address(holder)
        raw = rpc.eth_call(venue.chain, manager, data, block)
        underlying_decimals = venue.underlying.decimals if venue.underlying else 6
        # Match PricingLib.shareToAssetAmount(..., Rounding.Down), including
        # differing share/underlying decimals, with integer arithmetic.
        return decode_async_vault_exposure(raw, venue.token.decimals, underlying_decimals)
    except Exception as exc:
        raise RuntimeError(
            f"Async vault exposure read failed for {prime.id}/{venue.id} at block {block}; "
            "refusing to assume zero in-transit exposure"
        ) from exc


def issued_deposit(prime: Prime, venue: Venue, block: int) -> IssuedDeposit:
    return async_vault_exposure(prime, venue, block).deposit


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


def async_capital_inflow_timeseries(
    prime: Prime, venue: Venue, period, capital_flows: pd.DataFrame, *,
    opening_block: int, block_resolver, pending_redemptions: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Recognize daily capital at issuance and remove collection-only inflows.

    Vault cash-event flows gain the change in uncollected deposit basis.
    Share-transfer flows (BUIDL) gain the change in matched cash receivables.
    In either case positions and capital carry the same pending-value bridge,
    so principal is never recognized as income. Both PnL and average holdings
    consume the result. Raw share events remain available for fee detection.
    """
    columns = ['block_date', 'daily_inflow', 'cum_inflow']
    daily = {}
    for row in capital_flows.to_dict('records'):
        day = row['block_date']
        if day <= period.end:
            daily[day] = daily.get(day, ZERO) + row['daily_inflow']
    use_cash_ledger = venue.centrifuge_vault is None
    if use_cash_ledger:
        if pending_redemptions is None:
            from .redemption_settlements import pending_redemption_values
            pending_redemptions = pending_redemption_values(prime, venue, period)
        pending_by_date = dict(zip(
            pending_redemptions['block_date'], pending_redemptions['pending_value_usd'], strict=True,
        ))
        previous = async_vault_exposure(prime, venue, opening_block).redemption_cash
    else:
        previous = issued_deposit(prime, venue, opening_block).capital_usd
    day = period.start
    while day <= period.end:
        if use_cash_ledger:
            current = pending_by_date.get(day, ZERO)
        else:
            block = block_resolver.block_at_or_before(
                venue.chain.value, datetime.combine(day, time.max, tzinfo=UTC),
            )
            block = min(block, period.pin_blocks[venue.chain])
            current = issued_deposit(prime, venue, block).capital_usd
        daily[day] = daily.get(day, ZERO) + current - previous
        previous = current
        day += timedelta(days=1)
    rows, cumulative = [], ZERO
    for day, inflow in sorted(daily.items()):
        cumulative += inflow
        rows.append({'block_date': day, 'daily_inflow': inflow, 'cum_inflow': cumulative})
    return pd.DataFrame(rows, columns=columns)
