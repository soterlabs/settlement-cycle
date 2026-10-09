"""Inception-to-pin ALM capital movements from raw on-chain events.

Keep both sides of exchanges together. In particular, a withdrawal followed by
a deposit is not an external capital injection, and an aToken's Transfer sum is
not its principal: scaled balances and the execution-block index are used.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal, localcontext

from ..domain.pricing import PricingCategory
from ..domain.primes import Address, Chain, Prime, Token, Venue
from ..domain.sky_tokens import PAR_STABLES_BY_CHAIN, USDS_BY_CHAIN
from ..extract import aave_reconstruct, hypersync, hypersync_store, rpc
from ..extract._keccak import keccak256
from ..extract.transfer_logs import TRANSFER_TOPIC0
from .allocation_async_vaults import CAPITAL_ASSETS, REDEEM_REQUEST, AsyncVaultCapital
from .allocation_bridges import funded_cctp_burns, link_cctp
from .allocation_morpho_fees import (
    ACCRUE_INTEREST,
    ACCRUE_INTEREST_V2,
    V2_VAULTS,
    VAULTS,
    fee_mints,
)
from .allocation_nfts import NFTCapital
from .allocation_principal_returns import principal_return_logs
from .allocation_psm import PsmCapital
from .prices import get_unit_price, is_par_stable
from .sources.hypersync_balances import _addr_topic
from .sources.hypersync_debt import _FROB_T0, _VAT, _decode_dart

ZERO = Decimal("0")
_log = logging.getLogger(__name__)
REBASING = {PricingCategory.AAVE_ATOKEN, PricingCategory.SPARKLEND_SPTOKEN}
DEPOSIT = "0x" + keccak256(b"Deposit(address,address,uint256,uint256)").hex()
WITHDRAW = "0x" + keccak256(b"Withdraw(address,address,address,uint256,uint256)").hex()
QUEUE_CREATED = {"0x" + keccak256(f"RequestCreated({size},address,uint256)".encode()).hex()
                 for size in ("uint128", "uint256")}
QUEUE_PROCESSED = {"0x" + keccak256(f"RequestProcessed({size},address,uint256,uint256)".encode()).hex()
                   for size in ("uint128", "uint256")}


@dataclass(frozen=True)
class AssetMovement:
    account: str
    value_before: Decimal
    change: Decimal
    # Explicit rewards are never funded by another outgoing leg of the tx.
    external_income: Decimal = ZERO
    preserve_basis: bool = False


@dataclass(frozen=True)
class ExternalFundingOperation:
    kind: str
    source: str
    amount: Decimal


@dataclass(frozen=True)
class CapitalBatch:
    identity: str
    day: date
    timestamp: int
    chain: str
    block: int
    movements: tuple[AssetMovement, ...]
    minted: Decimal = ZERO
    log_index: int = 0
    minted_by_ilk: dict[str, Decimal] = field(default_factory=dict)
    external_funding: tuple[ExternalFundingOperation, ...] = ()
    funding_assumption: str | None = None


@dataclass(frozen=True)
class CapitalHistory:
    batches: tuple[CapitalBatch, ...]
    venue_accounts: dict[str, str]
    unsupported: dict[str, str]
    custody_accounts: dict[str, list[str]] = field(default_factory=dict)
    idle_accounts: set[str] = field(default_factory=set)
    analytics_only_venues: tuple[str, ...] = ()
    covered_by_boundary: dict[str, str] = field(default_factory=dict)


def _account(chain: Chain, token: Address, holder: Address) -> str:
    return f"{chain.value}:{holder.hex}:{token.hex}"


def _susds_capital_price(token, block, *, block_resolver=None):
    """Identify canonical sUSDS by address, including its bridge representations."""
    from ..domain.sky_tokens import PSM3_LEG_TOKENS, sUSDS_ETHEREUM

    bridged = PSM3_LEG_TOKENS.get(token.chain, {}).get("sUSDS")
    if token.chain == Chain.ETHEREUM and token.address == sUSDS_ETHEREUM.address:
        origin_block = block
    elif bridged is not None and token.address == bridged.address:
        if block_resolver is None:
            raise ValueError("Bridged sUSDS capital pricing requires a block resolver")
        stamp = hypersync.block_timestamp(token.chain.value, block)
        origin_block = block_resolver.block_at_or_before(
            Chain.ETHEREUM.value, datetime.fromtimestamp(stamp, UTC))
    else:
        return None
    raw = rpc.convert_to_assets(Chain.ETHEREUM, sUSDS_ETHEREUM.address,
                                10 ** sUSDS_ETHEREUM.decimals, origin_block)
    if raw <= 0:
        raise ValueError("Invalid sUSDS capital price")
    return Decimal(raw) / Decimal(10**18)


def _capital_asset_price(token, block, *, block_resolver=None):
    price = _susds_capital_price(token, block, block_resolver=block_resolver)
    if price is not None:
        return price
    from .prices import par_stable_price
    return par_stable_price(token)


def _capital_unit_price(venue, block, *, block_resolver=None):
    """Capital-only prices, including nested sUSDS and Superstate shares."""
    from .allocation_superstate import uscc_capital_price, ustb_capital_price

    price = ustb_capital_price(venue.token, block)
    if price is not None:
        return price
    price = uscc_capital_price(venue.token, block)
    if price is not None:
        return price
    price = _susds_capital_price(venue.token, block, block_resolver=block_resolver)
    if price is not None:
        return price
    price = get_unit_price(venue, block, block_resolver=block_resolver)
    if venue.pricing_category == PricingCategory.ERC4626_VAULT and venue.underlying:
        # Legacy report pricing treats sUSDS assets as par. Capital transfers
        # must use the same USD price on the cash and wrapped-share legs.
        price *= _capital_asset_price(venue.underlying, block, block_resolver=block_resolver)
    return price


def fetch_capital_history(prime: Prime, pins: dict[Chain, int], *,
                          block_resolver=None) -> CapitalHistory:
    """Read funded holdings and frob draws without changing the debt source.

    Scan from block zero: a configured reporting start date is not evidence of
    a zero opening balance. The shared log store makes subsequent runs
    incremental. Unsupported custody remains explicit, never a fabricated
    opening borrowed balance.
    """
    from .allocation_eoa import boundary_scope, eoa_boundaries, link_eoa_boundaries

    prime, covered_by_boundary = boundary_scope(prime)
    analytics_only = tuple(v.id for v, _ in eoa_boundaries(prime))
    if prime.id == 'grove' and not any(v.id == 'E12_PAU' for v in prime.venues):
        # Separate PAU-held NFT1352494 in the same AUSD/USDC pool. This is
        # tracing-only: adding custody coverage must not regenerate revenues
        # or silently introduce a zero-revenue row into settlement accounting.
        legacy = next((v for v in prime.venues if v.id == 'E12'), None)
        if legacy:
            pau = replace(legacy, id='E12_PAU', notional_principal_usd=None,
                          holder_override=Address.from_str('0x0dcd9298e163dfd3c0b5b00f0d9093c36e40a153'))
            prime = replace(prime, venues=[*prime.venues, pau])
            analytics_only = (*analytics_only, 'E12_PAU')
    assets: dict[Chain, dict[tuple[str, str], Venue]] = defaultdict(dict)
    venue_accounts: dict[str, str] = {}
    unsupported: dict[str, str] = {}
    custody_accounts: dict[str, list[str]] = defaultdict(list)
    for v in prime.venues:
        if v.skip:
            continue
        if v.holder_override and v.pricing_category == PricingCategory.RWA_TRANCHE and is_par_stable(v.token):
            unsupported[v.id] = 'Pass-through custody does not measure outstanding loan principal'
            continue
        if v.lp_kind in ("uniswap_v3", "uniswap_v4"):
            venue_accounts[v.id] = f"nft:{v.chain.value}:{v.id}"
            continue
        if v.pricing_category in (PricingCategory.EOA, PricingCategory.SPARK_SAVINGS_V2):
            unsupported[v.id] = "Requires non-fungible or beneficial-custody capital events"
            continue
        if v.notional_principal_usd and v.lp_kind != "curve_stableswap":
            unsupported[v.id] = "Requires off-chain principal payment matching"
            continue
        holder = v.holder_override or prime.alm[v.chain]
        account = _account(v.chain, v.token.address, holder)
        key = (v.token.address.hex, holder.hex)
        if key in assets[v.chain]:
            raise ValueError(f"Duplicate capital account for venue {v.id}")
        assets[v.chain][key] = v
        venue_accounts[v.id] = account
    # Cash is needed even when it has no report venue (e.g. OBEX USDS/USDC).
    for chain, holder in prime.alm.items():
        stables = dict(PAR_STABLES_BY_CHAIN.get(chain, {}))
        if chain in CAPITAL_ASSETS:
            t = CAPITAL_ASSETS[chain]
            stables[t.address.value] = (t.symbol, t.decimals)
        if chain in USDS_BY_CHAIN:
            t = USDS_BY_CHAIN[chain]
            stables[t.address.value] = (t.symbol, t.decimals)
        for v in prime.venues:
            if v.chain == chain and v.underlying is not None:
                t = v.underlying
                # Reuse the pricing module's strict stable classification.
                if is_par_stable(t):
                    stables[t.address.value] = (t.symbol, t.decimals)
        for address, (symbol, decimals) in stables.items():
            token = Token(chain, Address(address), symbol, decimals)
            key = (token.address.hex, holder.hex)
            assets[chain].setdefault(key, Venue(
                id=f"cash:{symbol}", chain=chain, token=token,
                pricing_category=PricingCategory.PAR_STABLE,
            ))

    basin_rows = []
    batches: list[CapitalBatch] = []
    bridge_burns = []
    verified_burns = set()
    for chain, mapping in assets.items():
        if chain not in pins:
            raise ValueError(f"Missing capital-history pin for {chain}")
        # Index the custody addresses once. This avoids a large OR of
        # per-token filters and keeps the durable stream stable when venues
        # are added. Unknown token contracts are not assigned a USD value.
        nft = NFTCapital(prime, chain)
        psm = PsmCapital(prime, chain)
        # Secondary ALMs and configured beneficial custodians can swap cash
        # before investing or repaying. Track both cash legs at those holders,
        # not only at the primary ALM and NFT holders.
        cash_holders = {Address.from_str(h) for _, h in mapping} | set(nft.holders.values())
        for holder in cash_holders:
            for (token, _), cash_venue in list(mapping.items()):
                if cash_venue.pricing_category == PricingCategory.PAR_STABLE:
                    mapping.setdefault((token, holder.hex), cash_venue)
        holders = sorted({_addr_topic(bytes.fromhex(h[2:])) for _, h in mapping})
        selections = [
            {"topics": [[], holders]},
            {"topics": [[], [], holders]},
            {"topics": [[], [], [], holders]},
        ]
        ilks = [i for i in [prime.ilk_bytes32, *prime.extra_ilks] if i]
        if chain == Chain.ETHEREUM and ilks:
            selections.append({"address": [_VAT], "topics": [
                [_FROB_T0], ["0x" + i.hex() for i in ilks],
            ]})
        logs = hypersync_store.fetch_logs(
            chain.value, selections, 0, pins[chain],
            log_fields=[*hypersync._DEFAULT_LOG_FIELDS, "transaction_hash"],
        )
        logs = [*logs, *nft.discover(logs, pins[chain])]
        if prime.id == "grove" and chain == Chain.ETHEREUM:
            basin_rows.extend(logs)
        principal_returns = principal_return_logs(prime, chain, mapping, logs)
        # Yield can arrive on a different chain from the investment (Grove's
        # Avalanche GACLO pays USDC on Ethereum). Match the configured token,
        # payer AND receipt ALM; do not mark every asset from that EOA as income.
        distribution_routes = {
            (src.token.hex, _addr_topic(src.payer.value), _addr_topic(prime.alm[chain].value))
            # skip suppresses position pricing, not explicitly configured yield
            # (e.g. Grove E38 incentives and E42 warehouse distributions).
            for venue in prime.venues
            for src in venue.cash_distributions
            if (src.chain or venue.chain) == chain and chain in prime.alm
        }
        grouped = defaultdict(list)
        for row in logs:
            if not row.transaction_hash:
                raise ValueError("Missing transaction identity in capital history")
            grouped[(row.block_number, row.transaction_hash)].append(row)
        fee_vaults = sorted({token for token, _ in mapping}
                           & (VAULTS.get(chain, set()) | V2_VAULTS.get(chain, set())))
        if fee_vaults:
            # Fee events have no indexed holder, so the ALM topic selections
            # above cannot see them. Attach only to already tracked transactions.
            for row in hypersync_store.fetch_logs(chain.value, [
                {'address': fee_vaults, 'topics': [[ACCRUE_INTEREST, ACCRUE_INTEREST_V2]]},
            ], 0, pins[chain], log_fields=[*hypersync._DEFAULT_LOG_FIELDS, 'transaction_hash']):
                key = (row.block_number, row.transaction_hash)
                if key in grouped:
                    grouped[key].append(row)
        units: dict[tuple[str, str], int] = defaultdict(int)
        queues: dict[tuple[str, str], tuple[tuple[str, str], int]] = {}
        queue_managers: dict[tuple[str, str], str] = {}
        async_vaults = AsyncVaultCapital(chain, mapping)
        ordered_groups = sorted(grouped.items(), key=lambda item: (
            item[0][0], min(r.log_index for r in item[1]),
        ))
        _log.info("Capital history %s/%s: %d transactions", prime.id, chain, len(ordered_groups))
        for batch_index, ((block, tx_hash), block_logs) in enumerate(ordered_groups):
            if batch_index and batch_index % 100 == 0:
                _log.info("Capital history %s/%s: %d/%d", prime.id, chain, batch_index, len(ordered_groups))
            block_logs = list({r.log_index: r for r in block_logs}.values())
            fee_shares = fee_mints(chain, block_logs, mapping)
            async_selected = async_vaults.prepare(block_logs)
            async_movements, async_deposits = async_vaults.movements(async_selected)
            nft_movements, nft_fees = nft.movements(block_logs)
            custody_sends = {_account(chain, Address.from_str(key[0]), Address.from_str(key[1]))
                             for row, key in async_selected if row.topic0 == REDEEM_REQUEST}
            tracked_usdc = {key for key, v in mapping.items()
                            if v.token.symbol == "USDC" and v.token.decimals == 6}
            bridge_burns.extend(funded_cctp_burns(chain, block_logs, tracked_usdc, verified_burns))
            changes: dict[tuple[str, str], int] = defaultdict(int)
            gifts: dict[tuple[str, str], int] = defaultdict(int)
            issuer_mints: dict[tuple[str, str], list[int]] = defaultdict(list)
            minted = ZERO
            minted_by_ilk: dict[str, Decimal] = defaultdict(Decimal)
            indices = {}
            deposits: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
            withdrawals = {}
            senders = {_addr_topic(a.value) for a in prime.external_alm_sources.get(chain, [])}
            from .allocation_merkl import wrapper_gift_transfers
            wrapper_gifts = wrapper_gift_transfers(block_logs, senders)
            seen = set()
            for row in sorted(block_logs, key=lambda r: r.log_index):
                if row.log_index in seen:
                    continue
                seen.add(row.log_index)
                if row.address == _VAT and row.topic0 == _FROB_T0:
                    ilk = bytes.fromhex(row.topic1[2:])
                    rate = rpc.ilk_rate(chain, Address.from_str(_VAT), ilk, block)
                    with localcontext() as ctx:
                        ctx.prec = 60
                        drawn = Decimal(_decode_dart(row.data)) * Decimal(rate) / Decimal(10**45)
                        minted += drawn
                        minted_by_ilk["0x" + ilk.hex()] += drawn
                    continue
                for key in mapping:
                    token, holder = key
                    if token != row.address:
                        continue
                    who = _addr_topic(bytes.fromhex(holder[2:]))
                    if row.topic0 == WITHDRAW and row.topic3 == who:
                        words = aave_reconstruct._words(row.data)
                        underlying = mapping[key].underlying
                        if len(words) != 2 or underlying is None:
                            raise ValueError("Invalid ERC4626 withdrawal event")
                        cash, shares = withdrawals.get(key, (ZERO, 0))
                        withdrawals[key] = (cash + Decimal(words[0]) / Decimal(10**underlying.decimals),
                                            shares + words[1])
                        continue
                    if row.topic0 == DEPOSIT and row.topic2 == who:
                        words = aave_reconstruct._words(row.data)
                        underlying = mapping[key].underlying
                        if len(words) != 2 or underlying is None:
                            raise ValueError("Invalid ERC4626 deposit event")
                        deposits[key] += Decimal(words[0]) / Decimal(10**underlying.decimals)
                        continue
                    if mapping[key].pricing_category in REBASING:
                        if row.topic0 not in {aave_reconstruct.MINT_T0, aave_reconstruct.BURN_T0, aave_reconstruct.BT_T0}:
                            continue
                        words = aave_reconstruct._words(row.data)
                        expected_words = 2 if row.topic0 == aave_reconstruct.BT_T0 else 3
                        if len(words) != expected_words:
                            raise ValueError("Unexpected aToken capital event layout")
                        indices[key] = words[-1]
                        if row.topic0 == aave_reconstruct.MINT_T0 and row.topic2 == who:
                            amount = words[0] - words[1]
                            signed = aave_reconstruct.ray_div(abs(amount), words[2])
                            changes[key] += signed if amount >= 0 else -signed
                        elif row.topic0 == aave_reconstruct.BURN_T0 and row.topic1 == who:
                            changes[key] -= aave_reconstruct.ray_div(words[0] + words[1], words[2])
                        elif row.topic0 == aave_reconstruct.BT_T0:
                            if row.topic1 == who:
                                changes[key] -= words[0]
                            if row.topic2 == who:
                                changes[key] += words[0]
                                if row.topic1 in senders or (row.transaction_hash, row.log_index) in wrapper_gifts:
                                    gifts[key] += words[0]
                        continue
                    if row.topic0 != TRANSFER_TOPIC0 or len(row.data) != 66:
                        continue
                    amount = int(row.data, 16)
                    if row.topic2 == who:
                        changes[key] += amount
                        if (row.topic1 in senders
                                or (row.address, row.topic1, row.topic2) in distribution_routes):
                            gifts[key] += principal_returns.get((row.block_number, row.log_index), amount)
                        elif (row.topic1 == '0x' + '0' * 64
                              and mapping[key].pricing_category == PricingCategory.RWA_TRANCHE
                              and mapping[key].min_transfer_amount_usd):
                            issuer_mints[key].append(amount)
                    if row.topic1 == who:
                        changes[key] -= amount
            movements = [*async_movements, *nft_movements]
            transaction_prices = {}
            for key in deposits.keys() | withdrawals.keys():
                asset_price = _capital_asset_price(mapping[key].underlying, block,
                                                   block_resolver=block_resolver)
                if key in deposits:
                    deposits[key] *= asset_price
                if key in withdrawals:
                    cash, shares = withdrawals[key]
                    withdrawals[key] = (cash * asset_price, shares)
            # Async adapters already return USD; do not convert those twice.
            deposits.update(async_deposits)
            for key, raw_change in changes.items():
                v = mapping[key]
                holder = Address.from_str(key[1])
                scale = Decimal(10**v.token.decimals)
                if v.pricing_category in REBASING:
                    # Mint/Burn/BalanceTransfer carry the actual execution index.
                    # This avoids both rebasing Transfer double-counting and
                    # a full reserve-history download for each capital event.
                    price = Decimal(indices[key]) / Decimal(10**27)
                else:
                    price = async_vaults.price(key, block)
                    if price is None:
                        price = _capital_unit_price(v, block, block_resolver=block_resolver)
                value_before = Decimal(units[key]) * price / scale
                transaction_prices[key[0]] = price
                change = Decimal(raw_change) * price / scale
                gift = Decimal(gifts[key]) * price / scale + nft_fees.get(key, ZERO)
                performance_fee = Decimal(fee_shares.get(key, 0)) * price / scale
                gift += performance_fee
                # Honor the existing per-venue issuer-distribution policy
                # (BUIDL), rather than treating its small yield mints as
                # unexplained capital. Check each mint, not the batch sum.
                gift += sum((Decimal(amount) * price / scale for amount in issuer_mints[key]
                             if Decimal(amount) * price / scale < v.min_transfer_amount_usd), ZERO)
                if key in withdrawals and key not in deposits:
                    cash, shares = withdrawals[key]
                    fee_units = fee_shares.get(key, 0)
                    if shares and shares == fee_units - raw_change:
                        # Release the redeemed share fraction using actual
                        # proceeds, not a rounded unit-price approximation.
                        value_before = cash * Decimal(units[key]) / Decimal(shares)
                        # Fee shares can be minted immediately before the
                        # withdrawal, even leaving a positive net share change.
                        # Price that known gift at the same execution ratio so
                        # the outgoing leg is exactly the observed cash.
                        actual_fee = cash * Decimal(fee_units) / Decimal(shares)
                        gift += actual_fee - performance_fee
                        performance_fee = actual_fee
                        change = actual_fee - cash
                units[key] += raw_change
                if units[key] < 0:
                    raise ValueError(f"Negative reconstructed capital holding: {v.id} {tx_hash}")
                if raw_change > 0 and key in deposits:
                    # Actual cash paid, not a rounded one-share NAV quotation.
                    # A simultaneous redeem is kept on the generic path.
                    burns = any(r.address == key[0] and r.topic0 == TRANSFER_TOPIC0
                                and r.topic1 == _addr_topic(holder.value) for r in block_logs)
                    if not burns:
                        change = deposits[key] + performance_fee
                movements.append(AssetMovement(
                    _account(chain, v.token.address, holder), value_before, change, gift,
                ))
            def psm_asset_value(token, amount, *, chain=chain, block=block,
                                transaction_prices=transaction_prices):
                from ..domain.sky_tokens import PSM3_LEG_TOKENS

                leg = next((t for t in PSM3_LEG_TOKENS.get(chain, {}).values()
                            if t.address.hex == token), None)
                if leg is None:
                    raise ValueError(f"Unknown PSM3 capital asset: {token}")
                price = transaction_prices.get(token)
                if price is None:
                    # A deposit can name the ALM as receiver while another
                    # account pays. Such funding remains unmatched in replay.
                    venue = Venue(id='psm-leg', chain=chain, token=leg,
                                  pricing_category=PricingCategory.PAR_STABLE)
                    price = _capital_unit_price(venue, block, block_resolver=block_resolver)
                return Decimal(amount) * price / Decimal(10**leg.decimals)

            movements.extend(psm.movements(block_logs, psm_asset_value))
            queue_redemptions = {}
            for row in sorted(block_logs, key=lambda r: r.log_index):
                if row.topic0 not in QUEUE_CREATED | QUEUE_PROCESSED:
                    continue
                owner = "0x" + row.topic2[-40:]
                queue_key = (row.address, owner)
                words = aave_reconstruct._words(row.data)
                if row.topic0 in QUEUE_CREATED:
                    matches = []
                    for key, venue in mapping.items():
                        if key[1] != owner or venue.pricing_category != PricingCategory.ERC4626_VAULT:
                            continue
                        sent = [r for r in block_logs if r.address == key[0] and r.topic0 == TRANSFER_TOPIC0
                                and r.topic1 == _addr_topic(bytes.fromhex(owner[2:]))
                                and int(r.data, 16) == words[0]]
                        if not sent:
                            continue
                        # Maple sends shares through PoolManager before the
                        # queue. Authenticate both contracts at the event block.
                        manager_raw = rpc.eth_call(chain, venue.token.address,
                                                  "0x" + keccak256(b"manager()")[:4].hex(), block)
                        manager = Address.from_str("0x" + manager_raw[-40:])
                        queue_raw = rpc.eth_call(chain, manager,
                                                "0x" + keccak256(b"withdrawalManager()")[:4].hex(), block)
                        if ("0x" + queue_raw[-40:] == row.address and
                                any(r.topic2 == _addr_topic(manager.value) for r in sent)):
                            matches.append(key)
                            queue_managers[queue_key] = manager.hex
                    if len(matches) != 1:
                        continue
                    key = matches[0]
                    old = queues.get(queue_key, (key, 0))[1]
                    delta = words[0]
                    price = _capital_unit_price(mapping[key], block, block_resolver=block_resolver)
                    scale = Decimal(10**mapping[key].token.decimals)
                    before_value, change = Decimal(old) * price / scale, Decimal(delta) * price / scale
                    custody_sends.add(_account(chain, mapping[key].token.address, Address.from_str(owner)))
                else:
                    if queue_key not in queues:
                        continue
                    key, old = queues[queue_key]
                    if len(words) != 2 or words[0] <= 0 or words[0] > old:
                        raise ValueError(f"Invalid queue redemption: {tx_hash}")
                    delta = -words[0]
                    underlying = mapping[key].underlying
                    assets_out = Decimal(words[1]) / Decimal(10**underlying.decimals)
                    # Multiple requests can settle in one transaction. Use
                    # total paid cash / total redeemed shares, not the first
                    # request's rounded conversion rate for the whole exit.
                    entry = queue_redemptions.setdefault(queue_key, [key, old, 0, ZERO])
                    entry[2] += words[0]
                    entry[3] += assets_out
                    queues[queue_key] = (key, old + delta)
                    continue
                queues[queue_key] = (key, old + delta)
                account = f"queue:{chain.value}:{row.address}:{owner}:{key[0]}"
                if account not in custody_accounts[mapping[key].id]:
                    custody_accounts[mapping[key].id].append(account)
                movements.append(AssetMovement(account, before_value, change))
            for (queue, owner), (key, opening, shares, cash) in queue_redemptions.items():
                account = f"queue:{chain.value}:{queue}:{owner}:{key[0]}"
                if account not in custody_accounts[mapping[key].id]:
                    custody_accounts[mapping[key].id].append(account)
                movements.append(AssetMovement(account, cash * Decimal(opening) / Decimal(shares), -cash))
            # Cancelled requests return shares via the PoolManager. These are
            # transfers of the same beneficial position, not new funding.
            for (queue, owner), (key, old) in list(queues.items()):
                if changes.get(key, 0) <= 0:
                    continue
                manager = queue_managers[(queue, owner)]
                returned = sum(int(r.data, 16) for r in block_logs
                               if r.address == key[0] and r.topic0 == TRANSFER_TOPIC0
                               and r.topic2 == _addr_topic(bytes.fromhex(owner[2:]))
                               and r.topic1 in {_addr_topic(bytes.fromhex(a[2:])) for a in (queue, manager)})
                if not returned:
                    continue
                if returned > old:
                    raise ValueError(f"Queue refund exceeds owned shares: {tx_hash}")
                v = mapping[key]
                price = _capital_unit_price(v, block, block_resolver=block_resolver)
                scale = Decimal(10**v.token.decimals)
                account = f"queue:{chain.value}:{queue}:{owner}:{key[0]}"
                movements.append(AssetMovement(account, Decimal(old) * price / scale,
                                               -Decimal(returned) * price / scale, preserve_basis=True))
                queues[(queue, owner)] = (key, old - returned)
            combined = {}
            for m in movements:
                if m.account in custody_sends:
                    m = replace(m, preserve_basis=True)
                if m.account not in combined:
                    combined[m.account] = m
                else:
                    before = combined[m.account]
                    combined[m.account] = AssetMovement(m.account, before.value_before,
                                                        before.change + m.change,
                                                        before.external_income + m.external_income,
                                                        before.preserve_basis or m.preserve_basis)
            if prime.id == 'grove' and chain == Chain.ETHEREUM:
                from .allocation_curve_swaps import curve_swap_income
                for _, _, _, account, change, gain in curve_swap_income(block_logs):
                    if account not in combined or combined[account].change != change:
                        raise ValueError('Curve swap gain lacks normalized raw cash movement')
                    m = combined[account]
                    combined[account] = replace(m, external_income=m.external_income + gain)
            first = block_logs[0]
            batches.append(CapitalBatch(
                f"{chain.value}:{tx_hash}", datetime.fromtimestamp(first.block_time, UTC).date(),
                first.block_time, chain.value, block, tuple(combined.values()), minted,
                min(r.log_index for r in block_logs), dict(minted_by_ilk),
            ))
        for vid, accounts in async_vaults.custody_accounts.items():
            custody_accounts[vid].extend(accounts)
        for vid, accounts in nft.custody_accounts.items():
            custody_accounts[vid].extend(accounts)
        from .allocation_custody import link_buidl_claims, link_facility

        batches = link_facility(prime, chain, batches, logs, venue_accounts, unsupported, mapping)
        batches = link_buidl_claims(prime, chain, pins[chain], batches, logs, custody_accounts)
        batches = link_eoa_boundaries(prime, chain, batches, logs, venue_accounts, unsupported)
    batches = link_cctp(prime, pins, batches, bridge_burns)
    from .allocation_ethena import link_ethena_cooldowns

    batches, custody_accounts = link_ethena_cooldowns(prime, pins, batches, custody_accounts)
    idle_accounts = {_account(c, USDS_BY_CHAIN[c].address, holder)
                     for c, holder in prime.alm.items() if c in USDS_BY_CHAIN}
    history = CapitalHistory(tuple(sorted(batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index))),
                          venue_accounts, unsupported, dict(custody_accounts), idle_accounts,
                          analytics_only, covered_by_boundary)

    if basin_rows:
        from .allocation_basin import basin_events, link_basin_shares
        history = link_basin_shares(history, basin_events(basin_rows))
        from .allocation_paxos import link_paxos_boundary, paxos_events
        history = link_paxos_boundary(history, paxos_events(basin_rows))
    return history
