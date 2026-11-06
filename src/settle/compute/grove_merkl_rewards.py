"""Classify two reviewed wrapper claims in pre-fix, pinned Grove histories.

Fresh extraction authenticates Claimed -> Mint.caller -> BalanceTransfer in
normalize/allocation_merkl.py. These receipts implement the same existing
Merkl revenue policy for saved histories, without changing debt or revenue.
Canonical events are retained in tests/fixtures/grove_merkl_wrapper_events.json.
"""
from dataclasses import replace
from decimal import Decimal as D

HOLDER = '0x491edfb0b8b608044e227225c715981a30f3a44e'
TOKENS = ('0xfa82580c16a31d0c1bc632a36f82e83efef3eec0',
          '0xe3190143eb552456f88464662f0c0c4ac67a77eb')
# https://etherscan.io/tx/0x8a81d6dda6b7aca33469aa5f4f6bb008ffb79ca7924ab609e88c2a767825704a
# https://etherscan.io/tx/0xd374d598dafa7ba02bc0b1d837acfd2ca92d002013ba7ae7f0785100964de3e7
CLAIMS = (
    ('0x8a81d6dda6b7aca33469aa5f4f6bb008ffb79ca7924ab609e88c2a767825704a', 24399845,
     (2963561635045723390973932, 821306031708030049765073)),
    ('0xd374d598dafa7ba02bc0b1d837acfd2ca92d002013ba7ae7f0785100964de3e7', 24950742,
     (1411897309885636179598609, 978913670671502534892758)),
)


def recognize_grove_merkl_rewards(history):
    accounts = tuple(f'ethereum:{HOLDER}:{t}' for t in TOKENS)
    if not set(accounts) & set(history.venue_accounts.values()):
        return history
    claims = {'ethereum:' + tx: (block, dict(zip(accounts, amounts, strict=True)))
              for tx, block, amounts in CLAIMS}
    batches = []
    for b in history.batches:
        reviewed = claims.get(b.identity)
        if reviewed is None:
            batches.append(b)
            continue
        block, amounts = reviewed
        if (b.block != block or b.chain != 'ethereum' or b.minted
                or len({m.account for m in b.movements}) != len(b.movements)):
            raise ValueError('Grove Merkl claim transaction differs from reviewed receipt')
        movements = []
        for m in b.movements:
            if m.account in amounts:
                # Raw ray reconstruction differs by < one token wei. Decimal
                # normalization has 28 significant digits, still far below this.
                if (abs(m.change - D(amounts[m.account]) / 10**18) > D('1e-18')
                        or m.external_income not in (D(0), m.change)):
                    raise ValueError('Grove Merkl claim amount differs from reviewed receipt')
                m = replace(m, external_income=m.change)
            movements.append(m)
        batches.append(replace(b, movements=tuple(movements)))
    return replace(history, batches=tuple(batches))
