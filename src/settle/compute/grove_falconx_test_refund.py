"""Recognize the November 24 FalconX onboarding test's two USDC returns.

ALM paid $1,000 to its configured E36 deposit wallet, which forwarded exactly
$1,000 to 0x1157a207... in the next six minutes. That wallet returned $10.002600
and $989.997399 to the ALM before the $25m production deposit. These exact
receipts refund the test, not a new source of borrowed capital. No blanket
allowlist for the commingled return wallet or downstream balance tracing.
Evidence: tests/fixtures/grove_falconx_test_refund_events.json.

Rebuild the existing E36 principal cap after the refund. Otherwise later AUSD
returns would release the same $999.999999 twice. This changes capital tracing
only; no published revenue or settlement is regenerated.
"""
from dataclasses import replace
from decimal import Decimal as D

ACCOUNT = 'eoa-allocation:ethereum:E36'
CASH = 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
AUSD = 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0x00000000efe302beaa2b3e6e1b18d08d69a9012a'
SOURCE = 'ethereum:0x70d06e83db92ef91591139f8381c604da987d183dc2b08ef51a68c8c01ca5a1d'
REFUNDS = (
    ('ethereum:0x181febfca47970a8ed18618d3b306d856f47951dcd386ddd45284f5e956726b2', 23871525, D('10.0026')),
    ('ethereum:0x5079cbd06b4cd9fb4b04c9215ffd914698c6774e2b6f9bf8a830a8909c0db86d', 23871552, D('989.997399')),
)
MARKER = ':falconx-test-refund'


def link_falconx_test_refund(history):
    from ..normalize.allocation_capital import AssetMovement

    if ACCOUNT not in history.venue_accounts.values():
        return history
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate FalconX capital transaction')
    if not any(tx in index for tx, _, _ in REFUNDS):
        return history
    if any(tx + MARKER in index for tx, _, _ in REFUNDS):
        raise ValueError('Partially transformed FalconX refund history')
    source = index.get(SOURCE)
    source_ms = [m for m in source.movements if m.account == ACCOUNT] if source else []
    if (source is None or source.block != 23869771 or len(source_ms) != 1
            or source_ms[0].value_before != 0 or source_ms[0].change != D(1000)):
        raise ValueError('FalconX refund lacks its reviewed $1,000 deposit')
    refunds = {tx: (block, amount) for tx, block, amount in REFUNDS}
    original_balance = balance = D(0)
    batches = []
    for b in sorted(history.batches, key=lambda b: (b.timestamp, b.chain, b.block, b.log_index)):
        refund = refunds.get(b.identity)
        owned = [m for m in b.movements if m.account == ACCOUNT]
        if refund:
            block, amount = refund
            if (b.chain != 'ethereum' or b.block != block or b.minted
                    or len(b.movements) != 1 or b.movements[0].account != CASH
                    or b.movements[0].change != amount or b.movements[0].external_income
                    or balance < amount):
                raise ValueError('FalconX refund differs from reviewed cash receipt')
            b = replace(b, identity=b.identity + MARKER,
                        movements=(*b.movements, AssetMovement(ACCOUNT, balance, -amount,
                                                               preserve_basis=True)))
            balance -= amount
        elif owned:
            if len(owned) != 1 or owned[0].value_before != original_balance or owned[0].external_income:
                raise ValueError('FalconX boundary principal history is inconsistent')
            m = owned[0]
            original_balance += m.change
            ms = list(b.movements)
            if m.change < 0:
                returns = [x for x in ms if x.account == AUSD]
                if (len(returns) != 1 or returns[0].change <= 0
                        or returns[0].change - returns[0].external_income != -m.change):
                    raise ValueError('FalconX subsequent return has mixed attribution')
                cash = returns[0]
                principal = min(balance, cash.change)
                ms = [replace(x, external_income=cash.change - principal) if x.account == AUSD
                      else x for x in ms]
                change = -principal
            else:
                change = m.change
            ms = [replace(x, value_before=balance, change=change) if x.account == ACCOUNT
                  else x for x in ms]
            balance += change
            b = replace(b, movements=tuple(ms))
        batches.append(b)
    return replace(history, batches=tuple(batches))
