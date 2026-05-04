"""Snapshot computation — `compute_snapshot(prime) -> Snapshot`.

Reuses the existing extract + normalize layers (no duplicate pricing math).
The only new code here is:
  - resolving "now" pin blocks per chain (latest block, not period-end)
  - aggregating per-venue values into BA's `assets / treasury / idle / debt / nav` shape
  - reading additional non-venue idle holdings (subproxy USDS/sUSDS, ALM USDS)
  - the Vat.ilks() debt read for the prime's ilk
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from ..domain.primes import Address, Chain, Prime
from ..domain.sky_tokens import USDS_ETHEREUM, sUSDS_ETHEREUM
from ..extract.rpc import balance_of, convert_to_assets, eth_call
from ..normalize.positions import get_position_value
from ..normalize.registry import (
    get_balance_source,
    get_block_resolver,
    get_convert_to_assets_source,
    get_position_balance_source,
)
from .types import IdleHolding, Snapshot, VenueSnapshot


@dataclass(frozen=True, slots=True)
class _DefaultSources:
    """Local, all-None default container for ``compute_snapshot``'s optional
    source overrides. Field names match ``compute.monthly_pnl.Sources`` so
    callers can pass either; we read attrs by name (duck-typed) and never
    import from the ``compute`` layer (PRD §4 — snapshot is a peer of
    compute, not a consumer)."""
    block_resolver: object = None
    balance: object = None
    position_balance: object = None
    convert_to_assets: object = None
    nav_oracle_resolver: object = None
    curve_pool: object = None
    v3_position: object = None

_log = logging.getLogger(__name__)

# MakerDAO Vat — Sky's AllocatorVault calls frob() on this Vat using the
# prime's ilk. Per-prime debt is `Vat.ilks(ilk).Art × Vat.ilks(ilk).rate / 1e45`
# (Art is wad ×1e18, rate is ray ×1e27; product is rad ×1e45 → USD whole units).
VAT_ADDRESS = Address.from_str("0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B")
SEL_VAT_ILKS = "0xd9638d36"   # ilks(bytes32) → (Art, rate, spot, line, dust)


def _read_debt_ilk(prime: Prime, eth_block: int) -> Decimal | None:
    """Return total prime debt = Σ over all urns in the ilk of (art × rate / RAY).
    One eth_call to Vat.ilks(ilk_bytes32). Returns None on RPC failure."""
    data = SEL_VAT_ILKS + prime.ilk_bytes32.hex()
    try:
        raw = eth_call(Chain.ETHEREUM, VAT_ADDRESS, data, eth_block)
    except Exception as e:
        _log.warning("Vat.ilks read failed for prime=%s: %s", prime.id, e)
        return None
    hx = raw[2:] if raw.startswith("0x") else raw
    if len(hx) < 128:
        return None
    Art = int(hx[0:64], 16)
    rate = int(hx[64:128], 16)
    return Decimal(Art) * Decimal(rate) / Decimal(10**45)


# Bridged USDS contract addresses on each non-Ethereum chain. Sourced from
# Sky's deployments + the Grove address registry. ALMs on L2/L3s hold these
# bridged variants (1:1 with Eth USDS) as in-flight or pre-allocation cash;
# BA reports them under ``idle_assets``. ``None`` means no canonical USDS
# bridge address known yet for that chain — we skip silently.
USDS_BY_CHAIN: dict[Chain, Address | None] = {
    Chain.ETHEREUM: USDS_ETHEREUM.address,
    Chain.BASE:        Address.from_str("0x820c137fa70c8691f0e44dc420a5e53c168921dc"),
    Chain.ARBITRUM:    Address.from_str("0x6491c05a82219b8d1479057361ff1654749b876b"),
    Chain.OPTIMISM:    Address.from_str("0x4f13a96ec5c4cf34e442b46bbd98a0791f20edc3"),
    Chain.UNICHAIN:    Address.from_str("0x7e10036acc4b56d4dfca3b77810356ce52313f9c"),
    Chain.AVALANCHE_C: None,    # No canonical USDS bridge on Avalanche today
    Chain.PLUME:       None,    # ditto
}

# USDC contract per chain — used to read idle USDC sitting at subproxy/ALM
# that's awaiting allocation (Grove subproxy holds ~$0.75M; Spark too).
USDC_BY_CHAIN: dict[Chain, Address | None] = {
    Chain.ETHEREUM:    Address.from_str("0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),
    Chain.BASE:        Address.from_str("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"),
    Chain.ARBITRUM:    Address.from_str("0xaf88d065e77c8cc2239327c5edb3a432268e5831"),
    Chain.OPTIMISM:    Address.from_str("0x0b2c639c533813f4aa9d7837caf62653d097ff85"),
    Chain.UNICHAIN:    Address.from_str("0x078d782b760474a361dda0af3839290b0ef57ad6"),
    Chain.AVALANCHE_C: Address.from_str("0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e"),
    Chain.PLUME:       None,
}


def _read_idle_holdings(
    prime: Prime, pin_blocks: dict[Chain, int],
) -> list[IdleHolding]:
    """Read non-venue token balances at the prime's subproxy.

    ALM-side holdings are owned by the YAML venue inventory (E13–E18 for
    Grove; S27/S28/S32 + L2 USDS-POL for Spark). Reading them again here
    would double-count, so we **only** scan the subproxy.

    Per BA labs categorisation (`/stars/{prime}/`):
      - subproxy USDS on Ethereum → ``treasury_balance``
      - subproxy sUSDS + USDC + ... → ``idle_assets``
    """
    out: list[IdleHolding] = []

    eth_block = pin_blocks.get(Chain.ETHEREUM)
    subproxy = prime.subproxy.get(Chain.ETHEREUM) if eth_block else None
    if not subproxy:
        return out

    USDS_eth = USDS_ETHEREUM.address
    SUSDS = sUSDS_ETHEREUM.address
    USDC_eth = USDC_BY_CHAIN[Chain.ETHEREUM]

    usds_raw = balance_of(Chain.ETHEREUM, USDS_eth, subproxy, eth_block)
    susds_raw = balance_of(Chain.ETHEREUM, SUSDS, subproxy, eth_block)
    usdc_raw = balance_of(Chain.ETHEREUM, USDC_eth, subproxy, eth_block)

    if usds_raw > 0:
        value = Decimal(usds_raw) / Decimal(10**18)
        out.append(IdleHolding(
            label="subproxy_USDS", chain=Chain.ETHEREUM,
            holder_address=subproxy, token_address=USDS_eth,
            token_symbol="USDS", block=eth_block,
            shares=value, value_usd=value, category="treasury",
        ))
    if susds_raw > 0:
        assets_raw = convert_to_assets(Chain.ETHEREUM, SUSDS, susds_raw, eth_block)
        out.append(IdleHolding(
            label="subproxy_sUSDS", chain=Chain.ETHEREUM,
            holder_address=subproxy, token_address=SUSDS,
            token_symbol="sUSDS", block=eth_block,
            shares=Decimal(susds_raw) / Decimal(10**18),
            value_usd=Decimal(assets_raw) / Decimal(10**18),
            category="idle",
        ))
    if usdc_raw > 0:
        value = Decimal(usdc_raw) / Decimal(10**6)
        out.append(IdleHolding(
            label="subproxy_USDC", chain=Chain.ETHEREUM,
            holder_address=subproxy, token_address=USDC_eth,
            token_symbol="USDC", block=eth_block,
            shares=value, value_usd=value, category="idle",
        ))

    return out


def compute_snapshot(
    prime: Prime,
    *,
    sources=None,    # optional source-overrides container (duck-typed; accepts
                     # ``compute.monthly_pnl.Sources`` or ``_DefaultSources``)
    pin_blocks: dict[Chain, int] | None = None,
) -> Snapshot:
    """Compute a live point-in-time snapshot for ``prime``.

    Pins to ``pin_blocks`` if given, otherwise resolves each chain's current
    head block via the registered block resolver. Per-venue pricing reuses
    ``normalize.positions.get_position_value`` (so V3 NFT, Curve LP, RWA,
    aToken, ERC-4626, par-stable all share the production code path).
    """
    if sources is None:
        sources = _DefaultSources()

    # Resolve pin blocks per chain. Always materialize a block_resolver
    # (per-venue value pricing needs it for cross-chain NAV oracles like
    # ACRDX-on-Plume reading Chronicle on Ethereum).
    resolver = sources.block_resolver or get_block_resolver()
    if pin_blocks is None:
        pin_blocks = {}
        anchor = datetime.now(timezone.utc)
        wanted = prime.chains | set(getattr(prime, "psm", {}).keys())
        for chain in wanted:
            try:
                pin_blocks[chain] = resolver.block_at_or_before(chain.value, anchor)
            except Exception as e:
                _log.warning("block resolver failed for %s: %s", chain.value, e)
        # Surface the failure list explicitly: silently dropping a chain
        # zeroes every venue on it (note='no pin_block for chain') and those
        # zeros are summed into ``venues_total_usd`` — easy to miss.
        missing = [c.value for c in wanted if c not in pin_blocks]
        if missing:
            _log.warning(
                "snapshot prime=%s: %d chain(s) have NO pin_block — venues on them "
                "will be zero in venues_total_usd: %s",
                prime.id, len(missing), ", ".join(missing),
            )

    # Per-venue valuations.
    venues: list[VenueSnapshot] = []
    bal_src = sources.position_balance or get_position_balance_source()
    c2a_src = sources.convert_to_assets or get_convert_to_assets_source()
    bal_dune_src = sources.balance or get_balance_source()

    for v in prime.venues:
        if v.skip:
            venues.append(VenueSnapshot(
                venue_id=v.id, label=v.label, chain=v.chain,
                token_address=v.token.address,
                holder_address=v.holder_override or prime.alm.get(v.chain) or v.token.address,
                pricing_category=v.pricing_category.value,
                block=pin_blocks.get(v.chain, 0),
                shares=Decimal("0"), value_usd=Decimal("0"),
                pps=None, note="venue.skip=true",
            ))
            continue
        block = pin_blocks.get(v.chain)
        if block is None:
            venues.append(VenueSnapshot(
                venue_id=v.id, label=v.label, chain=v.chain,
                token_address=v.token.address,
                holder_address=v.holder_override or prime.alm.get(v.chain) or v.token.address,
                pricing_category=v.pricing_category.value, block=0,
                shares=Decimal("0"), value_usd=Decimal("0"),
                pps=None, note="no pin_block for chain",
            ))
            continue
        holder = v.holder_override or prime.alm.get(v.chain) or prime.subproxy.get(v.chain)
        try:
            value = get_position_value(
                prime, v, block,
                balance_source=bal_src,
                erc4626_source=c2a_src,
                block_resolver=resolver,
                nav_oracle_resolver=sources.nav_oracle_resolver,
                curve_pool_source=sources.curve_pool,
                v3_position_source=sources.v3_position,
            )
            shares = (
                Decimal(balance_of(v.chain, v.token.address, holder, block))
                / Decimal(10 ** v.token.decimals)
                if v.lp_kind != "uniswap_v3" else Decimal("0")
            )
            note = ""
        except Exception as e:
            value = Decimal("0")
            shares = Decimal("0")
            note = f"ERR: {type(e).__name__}: {str(e)[:120]}"
            _log.warning("snapshot venue %s value failed: %s", v.id, e)
        venues.append(VenueSnapshot(
            venue_id=v.id, label=v.label, chain=v.chain,
            token_address=v.token.address, holder_address=holder,
            pricing_category=v.pricing_category.value, block=block,
            shares=shares, value_usd=value, pps=None, note=note,
        ))

    # Idle holdings (treasury + idle) — multi-chain.
    idle: list[IdleHolding] = _read_idle_holdings(prime, pin_blocks)
    eth_block = pin_blocks.get(Chain.ETHEREUM)

    # Aggregates.
    venues_total = sum((v.value_usd for v in venues), Decimal("0"))
    treasury = sum(
        (h.value_usd for h in idle if h.category == "treasury"), Decimal("0"),
    )
    idle_assets = sum(
        (h.value_usd for h in idle if h.category == "idle"), Decimal("0"),
    )
    in_transit = Decimal("0")  # cross-chain bridge tracking — Phase 3+
    debt = _read_debt_ilk(prime, eth_block) if eth_block else None

    return Snapshot(
        prime_id=prime.id,
        generated_at_utc=datetime.now(timezone.utc),
        pin_blocks=pin_blocks,
        venues=venues, idle=idle,
        venues_total_usd=venues_total,
        treasury_balance_usd=treasury,
        idle_assets_usd=idle_assets,
        in_transit_assets_usd=in_transit,
        debt_usd=debt,
    )
