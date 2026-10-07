"""Reviewed October 2025 JTRSY bridge/redemption route funding Plume Apollo.

Governance configured destination domain 4 and Grove's Plume ALM here:
https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20251002/GroveEthereum_20251002.sol
The companion GrovePlume_20251002.sol enables JTRSY redemption and ACRDX.

Exact cross-chain message payloads, share mints, redemption requests and cash
withdrawals are pinned in tests/fixtures/grove_plume_jtrsy_events.json. These
are reviewed executions, not amount/date guesses or new notional funding.
"""
from dataclasses import replace
from decimal import Decimal, localcontext

GROVE = '0x491edfb0b8b608044e227225c715981a30f3a44e'
PLUME = '0x1db91ad50446a671e2231f77e00948e68876f812'
SOURCE = f'ethereum:{GROVE}:0x8c213ee79581ff4984583c6a801e5263418c4b86'
PENDING = 'jtrsy-plume:2025-10:beneficial-custody'
MARKER = ':jtrsy-plume'
# source block/tx, actual destination mint block/tx, raw shares (6 decimals).
TRANSFERS = (
    (23569102, '0x8fd8753896fb1241c3c2bf772f8dafa24a37b5680150d30d5df765220b1fc012',
     33206768, '0xca2048e3c4440d4709db3c805ce973aa84e6135a8f353f0d09643fad4626ae00', 18494562293919),
    (23576967, '0x62994a29311c71f6b668a3572ca1684124ee6e0ca408157e9c8d9030a2379362',
     33390122, '0x715709306c73fd2146b6ce83e2dcbd67f13b4a947ab4f46121dd5ac95a43533e', 18487141730747),
    (23584256, '0xb6536bca914b8403fbbe473fd1d7c4982041da0a2674f630a78f90dab12ae72a',
     33546322, '0xdab9f27c19fb429d3bad7d53930cdfa056acd41ecd83cf13ab2b020988eb9b57', 9242801936921),
)
# Cash claim block/tx, actual escrowed shares discharged, received USDC.
# ERC7540 Withdraw rounds its reported shares UP by one raw unit; the request
# and escrow burn establish the actual units. All three exhaust the corridor.
REDEMPTIONS = (
    (33410455, '0x419fc99c6ec84f1c53138e01908b59fecf4a26a1a39d7aa47c56e18053730359',
     18494562293919, '20008042.283908'),
    (33573578, '0x8cd2d5d675c3d42e4e3872c80cdcad1946e863f5a1f82bd03c158c26ef093c98',
     18468654589016, '19981663.966797'),
    (33732405, '0x9856d21030eefdb5a564f9982e07d7c3ec1cc179dd47a245be2fa36f3efc453b',
     9261289078652, '10020691.280307'),
)


def link_grove_plume_jtrsy(history):
    from ..normalize.allocation_capital import AssetMovement

    if SOURCE not in history.venue_accounts.values():
        return history
    batches = {b.identity: b for b in history.batches}
    if len(batches) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    raw_ids = {'ethereum:' + t[1] for t in TRANSFERS} | {'plume:' + r[1] for r in REDEMPTIONS}
    if any(k.endswith(MARKER) for k in batches):
        # Full replay idempotence is supported; extending an already adapted
        # snapshot with new raw route events is not. Reload normalized inputs.
        if any(k in batches for k in raw_ids):
            raise ValueError('Cannot append raw events to linked Grove Plume history')
        return history
    sends = {'ethereum:' + t[1]: t for t in TRANSFERS}
    receipts = {'plume:' + r[1]: r for r in REDEMPTIONS}
    units = Decimal(0)
    delivered_units = Decimal(0)
    deliveries = {'plume:' + t[3]: t for t in TRANSFERS}
    custody = {v: list(a) for v, a in history.custody_accounts.items()}
    venue = next(v for v, a in history.venue_accounts.items() if a == SOURCE)
    linked = []
    with localcontext() as ctx:
        ctx.prec = 60
        for b in sorted(history.batches, key=lambda x: (x.timestamp, x.chain, x.block, x.log_index)):
            if b.identity in sends:
                block, _, mint_block, mint_tx, raw_units = sends[b.identity]
                outgoing = [m for m in b.movements if m.account == SOURCE]
                if (b.chain != 'ethereum' or b.block != block or len(outgoing) != 1
                        or outgoing[0].change >= 0 or outgoing[0].external_income):
                    raise ValueError('Grove Plume JTRSY source mismatch')
                delivery = batches.get('plume:' + mint_tx)
                if delivery is not None and (delivery.block != mint_block or delivery.chain != 'plume'
                        or delivery.timestamp <= b.timestamp or delivery.movements or delivery.minted):
                    raise ValueError('Grove Plume JTRSY delivery mismatch')
                out = outgoing[0]
                amount = -out.change
                price = amount / Decimal(raw_units)
                # Split this authenticated custody leg from unrelated draws/
                # claims in the same transaction (notably October 15's JAAA).
                linked.append(replace(b, identity=b.identity + MARKER,
                    movements=(replace(out, preserve_basis=True),
                               AssetMovement(PENDING, units * price, amount)),
                    minted=Decimal(0), minted_by_ilk={}))
                remaining = tuple(m for m in b.movements if m.account != SOURCE)
                if remaining or b.minted:
                    linked.append(replace(b, identity=b.identity + ':other' + MARKER,
                                          movements=remaining, log_index=b.log_index + 1))
                units += raw_units
                if PENDING not in custody.setdefault(venue, []):
                    custody[venue].append(PENDING)
                continue
            if b.identity in deliveries:
                source_block, source_tx, mint_block, _, raw_units = deliveries[b.identity]
                original = batches.get('ethereum:' + source_tx)
                if (original is None or original.block != source_block or b.block != mint_block
                        or b.chain != 'plume' or original.timestamp >= b.timestamp
                        or b.movements or b.minted):
                    raise ValueError('Grove Plume JTRSY delivery lacks its source execution')
                delivered_units += raw_units
            if b.identity in receipts:
                block, _, redeemed, amount_string = receipts[b.identity]
                amount = Decimal(amount_string)
                if (b.chain != 'plume' or b.block != block or b.minted or units < redeemed or delivered_units < redeemed
                        or any(m.external_income for m in b.movements)
                        or abs(sum((m.change for m in b.movements), Decimal(0)) - amount) > Decimal('0.000001')):
                    raise ValueError('Grove Plume JTRSY redemption mismatch')
                # The pooled beneficial holding includes in-transit, wallet and
                # escrow shares. Use actual redeemed units for partial releases;
                # cash price determines realized gain/loss, never new basis.
                value = amount if units == redeemed else amount * units / Decimal(redeemed)
                linked.append(replace(b, identity=b.identity + MARKER,
                    movements=(*b.movements, AssetMovement(PENDING, value, -amount))))
                units -= redeemed
                delivered_units -= redeemed
                continue
            linked.append(b)
    return replace(history, batches=tuple(linked), custody_accounts=custody)
