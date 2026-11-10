"""Protocol ownership states beneath the shared async exposure interface.

Maple Queue's lockedShares(owner) only covers manual-processed shares: queued
userEscrowedShares must be added. Pool managers are routing contracts, not
owner-specific escrows. Ethena cooldown amounts are USDe, never sUSDe shares.
"""

from decimal import Decimal

from ..domain import Address
from ..extract import rpc
from ..extract._keccak import keccak256
from .prices import par_stable_price


def call(chain, contract, signature, block, owner=None, words=1):
    data = "0x" + keccak256(signature.encode())[:4].hex()
    if owner is not None:
        data += owner.hex[2:].zfill(64)
    raw = rpc.eth_call(chain, contract, data, block)
    if not isinstance(raw, str) or not raw.startswith("0x") or len(raw) != 2 + words * 64:
        raise ValueError(f"Invalid async ownership response: {signature}")
    return tuple(int(raw[2 + n * 64 : 2 + (n + 1) * 64], 16) for n in range(words))


def address_view(chain, contract, signature, block):
    (value,) = call(chain, contract, signature, block)
    if not 0 < value < 2**160:
        raise ValueError(f"Invalid async address response: {signature}")
    return Address.from_str("0x" + format(value, "040x"))


def protocol_exposure(prime, venue, block):
    from .async_vaults import AsyncVaultExposure

    policy = venue.async_allocation
    owner = venue.holder_override or prime.alm[venue.chain]
    if not rpc.is_contract_deployed(venue.chain, venue.token.address, block):
        return AsyncVaultExposure()
    if policy.kind == "maple":
        if venue.underlying is None:
            raise ValueError("Maple async allocation needs its underlying token")
        asset = address_view(venue.chain, venue.token.address, "asset()", block)
        if asset != venue.underlying.address:
            raise ValueError("Maple asset differs from configured underlying")
        manager = address_view(venue.chain, venue.token.address, "manager()", block)
        active = address_view(venue.chain, manager, "withdrawalManager()", block)
        if active not in {m.address for m in policy.managers}:
            raise ValueError("Unreviewed Maple withdrawal-manager deployment")
        claims = []
        total = Decimal(0)
        for escrow in policy.managers:
            if not rpc.is_contract_deployed(venue.chain, escrow.address, block):
                if escrow.address == active:
                    raise ValueError("Active Maple withdrawal manager is undeployed")
                continue
            implementation = address_view(venue.chain, escrow.address, "implementation()", block)
            if implementation not in escrow.implementations:
                raise ValueError("Unreviewed Maple withdrawal-manager implementation")
            if address_view(venue.chain, escrow.address, "pool()", block) != venue.token.address:
                raise ValueError("Maple withdrawal manager belongs to a different pool")
            if escrow.kind == "queue":
                (queued,) = call(
                    venue.chain, escrow.address, "userEscrowedShares(address)", block, owner
                )
                (manual,) = call(
                    venue.chain, escrow.address, "manualSharesAvailable(address)", block, owner
                )
            else:
                (queued,) = call(venue.chain, escrow.address, "lockedShares(address)", block, owner)
                manual = 0
            total += Decimal(queued + manual) / 10**venue.token.decimals
            claims.append(
                dict(
                    protocol="maple",
                    owner=owner.hex,
                    token=venue.token.address.hex,
                    manager=escrow.address.hex,
                    implementation=implementation.hex,
                    block=block,
                    queued_raw=str(queued),
                    manual_raw=str(manual),
                    quantity_raw=str(queued + manual),
                    units="vault_shares",
                    valuation="current_share_NAV_until_actual_redemption",
                )
            )
        return AsyncVaultExposure(redemption_shares=total, claims=tuple(claims))
    if policy.kind == "ethena":
        if venue.underlying is None:
            raise ValueError("Ethena async allocation needs its underlying token")
        if (
            address_view(venue.chain, venue.token.address, "asset()", block)
            != venue.underlying.address
        ):
            raise ValueError("Ethena asset differs from configured underlying")
        silo = address_view(venue.chain, venue.token.address, "silo()", block)
        expiry, quantity = call(
            venue.chain, venue.token.address, "cooldowns(address)", block, owner, words=2
        )
        if expiry >= 2**104 or quantity >= 2**152:
            raise ValueError("Invalid Ethena cooldown ownership widths")
        # The stored underlying quantity remains owned after expiry. The
        # existing underlying price applies; share NAV cannot reprice USDe.
        amount = Decimal(quantity) / 10**venue.underlying.decimals
        price = par_stable_price(venue.underlying)
        return AsyncVaultExposure(
            redemption_cash=amount * price,
            claims=(
                dict(
                    protocol="ethena",
                    owner=owner.hex,
                    silo=silo.hex,
                    block=block,
                    asset=venue.underlying.address.hex,
                    quantity_raw=str(quantity),
                    units="underlying_assets",
                    cooldown_end=expiry,
                    valuation="underlying_token_price",
                    price_usd=str(price),
                ),
            ),
        )
    if policy.kind == "issuer":
        from .issuer_redemptions import exposure

        return exposure(prime, venue, block)
    raise ValueError("Unsupported async allocation protocol")
