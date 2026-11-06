"""Six reviewed issuer advances preceding their larger BUIDL settlement.

Each group has one outstanding request, an initial small USDC payment, then
its large same-day payment from the configured issuer payer. Their combined
cash matches the request less the observed 5bp redemption charge within $5.
The event stream does not emit a cash-to-request ID: these are reviewed,
uniquely supported associations, not cryptographic message links or gifts.
Canonical events are retained in grove_buidl_partial_payment_events.json.

This applies only to capital provenance. Published 2025 revenue and reports
are unchanged. A partial receipt releases only its slice; the final payment
closes the remaining claim and realizes the actual aggregate exit shortfall.
"""
from dataclasses import replace
from decimal import Decimal

CASH = 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
MARKER = ':buidl-partial'
LINKS = (
    ('0x5ebafa5e7253ffaa7e1f596d197258c68f7648b935f538e4d73380f710b072bb', 344, 23068867, Decimal('100000000.00'), '0x2f03e74c7dcbacbd8d4dd474d90bc38c4649231a1130d39a0a524ae2df0dfec0', 23069652, Decimal('0.444758'), '0x092c6464116cc908dacb4f71e4a64b72a9fa6a4368a92b774490b2d59625aa79', 23069924, Decimal('99949998.438074')),
    ('0xb91127cd37947c01b60e8f0d8162d6ca56f11dda198f2d1a372dc84ad8f96390', 21, 23276269, Decimal('50000000.00'), '0xa81c7795202a5ee2ff33b322f4760aacdbaa1d5eb383fa239032fac54a364842', 23276952, Decimal('0.288745'), '0x8294ca481adbb8053752f10aeb58d69045304c20c55823dfcc18d4b07c741d07', 23277513, Decimal('49974998.364591')),
    ('0xea90a573a7d667637a5f4b1daec2916b524662b8f27450fbc6641e5d7520a6c3', 211, 23283398, Decimal('100000000.00'), '0xad78a6ce0db054435ac251f96bad0f547889c3cfe102b2fb6ca85a8810ee5838', 23284149, Decimal('1.213638'), '0x88ed070bd1bbc10140d8faf4094b5e6213acfeeb302adfd04130b06b6f795192', 23284347, Decimal('99949997.217881')),
    ('0x97c7f42781d6b2bc395fc3782906473ac024ebb6eaaba7ca7e31609bf6085581', 195, 23370485, Decimal('50000000.00'), '0xa7fe4ce7188746d65e0b4590ea032163d826c031f79f1c5ae488d300a4063483', 23376217, Decimal('0.796026'), '0x51418b4e19e5c0722e087ba42a0d8a6226f07e4bed7fb235ef5b3f7646e52253', 23377304, Decimal('49974997.18334')),
    ('0x1e4938d5374609a1f359614ac88e2e5b8e74c0ba3ea9fb64c6f5bfe8c05e76a8', 335, 23390747, Decimal('50000000.00'), '0x8163d32234b9dc09e81d5836d7b9df391bb8edf132ce5f85660fe2e92efeb15f', 23391346, Decimal('0.183147'), '0xee4b9bf42fe1dc061fb63eca1095779c1ed152ef6c2778542f47f5251a2197cd', 23391871, Decimal('49974997.039649')),
    ('0xf8f733ce12a56f7b80c6e7a0a53f1f030d73c71edd95141b0214b4e7427eb270', 170, 24026888, Decimal('85000000.00'), '0x69f334bbd7a39121fd0f45c0e7893d3aa84cb4931ae50d883fe2ad8289cc25c7', 24027247, Decimal('1.614203'), '0x08b74c7ef07d8b02fadc2de4319de31f3451a81204d7cf3acdda16dce4d750b1', 24027744, Decimal('84957497.621275')),
)


def link_grove_buidl_partial_payments(history):
    from ..normalize.allocation_capital import AssetMovement

    if CASH not in history.venue_accounts.values():
        return history
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate BUIDL capital transaction')
    for req, log, rb, face, partial, pb, advance, final, fb, cash in LINKS:
        account = f'redemption:ethereum:{req}:{log}'
        source = index.get('ethereum:' + req)
        if source is None:
            # September 2 multicall also contains the independently linked
            # JAAA transfer; that adapter preserves this BUIDL claim intact.
            source = index.get('ethereum:' + req + ':jaaa-crosschain')
        first = index.get('ethereum:' + partial)
        last = index.get('ethereum:' + final)
        if first is None:
            continue  # Already transformed, or beyond the pinned cutoff.
        if source is None:
            raise ValueError('BUIDL advance lacks its funded request')
        sources = [m for m in source.movements if m.account == account]
        if (source.block != rb or len(sources) != 1 or sources[0].change != face
                or sources[0].value_before != 0 or sources[0].external_income
                or first.block != pb or first.chain != 'ethereum' or first.minted
                or not rb < pb < fb or len(first.movements) != 1
                or first.movements[0].account != CASH or first.movements[0].change != advance
                or first.movements[0].external_income):
            raise ValueError('BUIDL partial payment differs from reviewed events')
        if last is not None:
            outgoing = [m for m in last.movements if m.account == account]
            receipt = [m for m in last.movements if m.account == CASH]
            if (last.block != fb or last.chain != 'ethereum' or last.minted
                    or len(outgoing) != 1 or outgoing[0].change != -cash
                    or outgoing[0].value_before != cash or outgoing[0].external_income
                    or len(receipt) != 1 or receipt[0].change != cash or receipt[0].external_income):
                raise ValueError('BUIDL final payment differs from reviewed events')
        # No future amount is used at the advance: debit its known funded
        # claim at the amount actually received and retain everything else.
        index[first.identity] = replace(first, identity=first.identity + MARKER,
            movements=(*first.movements, AssetMovement(account, face, -advance)))
    return replace(history, batches=tuple(index.values()))
