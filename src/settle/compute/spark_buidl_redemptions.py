"""Two reviewed Spark BUIDL exits paid directly by the known issuer payer.

The complete ALM boundary stream has one outstanding request in each group;
combined cash equals its face less 5bp within $3. These are reviewed issuer
cash associations, not on-chain payment/request IDs. The July payment has a
small advance before the final settlement. An unrelated April 4,999.75 receipt
is deliberately not assigned. No issuer-wallet interior is traced.

Evidence: tests/fixtures/spark_buidl_redemptions.json.gz and
 docs/spark/buidl-redemptions-2026-10-08.md. Tracing only, no revenue restatement.
"""
from dataclasses import replace
from decimal import Decimal as D

HOLDER = '0x1601843c5e9bc251a3272907010afa41fa18347e'
SHARES = f'ethereum:{HOLDER}:0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041'
CASH = f'ethereum:{HOLDER}:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
SUFFIX = ':spark-buidl-redemption'
# request tx, block, time, face; (cash tx, block, time, amount), in order.
LINKS = (
    ('0xea83b684e5db29239501ba7b5839634d28f88222aa87059905dee4d6e1fd585b', 22482359, 1747237295, D('250000'), (
        ('0xf79e894c03a379f636f0451690b55ff5289b9e6904f665ef5700630548ed8b0f', 22482792, 1747242563, D('249874.297369')),
    )),
    ('0x3cc7028c9cbe39daaab0ac3b142dfb171aac3f85060c6f058060bd39a4589847', 22947529, 1752859079, D('200000000'), (
        ('0xcf6ba0c45a0c85e4deb822d5a70f2c7f7e6e3074684666526abd887b4840a244', 22948631, 1752872327, D('98.543517')),
        ('0xbb0d744db0f957e891378dd3d5ae3bee60d034cbf53602926b2d32f4fee9cfad', 22948693, 1752873071, D('199899898.685658')),
    )),
)


def link_spark_buidl_redemptions(history):
    from ..normalize.allocation_capital import AssetMovement

    if history.venue_accounts.get('S19') != SHARES:
        return history
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    custody = {k: list(v) for k, v in history.custody_accounts.items()}
    changed = False
    for tx, block, stamp, face, payments in LINKS:
        identity = 'ethereum:' + tx
        ids = [identity, *('ethereum:' + p[0] for p in payments)]
        if any(i + SUFFIX in index for i in ids):
            if any(i in index for i in ids):
                raise ValueError('Cannot append raw Spark BUIDL settlement events')
            continue
        source = index.get(identity)
        if source is None:
            continue  # A receipt alone does not prove existing funded capital.
        if (source.chain != 'ethereum' or source.block != block or source.timestamp != stamp
                or source.minted or len(source.movements) != 1
                or source.movements[0].account != SHARES
                or source.movements[0].change != -face or source.movements[0].external_income):
            raise ValueError('Spark BUIDL request differs')
        claim = 'redemption:spark:buidl:' + tx
        owners = custody.setdefault('S19', [])
        if claim not in owners:
            owners.append(claim)
        index[identity + SUFFIX] = replace(source, identity=identity + SUFFIX,
            movements=(replace(source.movements[0], preserve_basis=True),
                       AssetMovement(claim, D(0), face, preserve_basis=True)))
        del index[identity]
        remaining, missing = face, False
        for n, (cash_tx, cb, cs, amount) in enumerate(payments):
            cash_id = 'ethereum:' + cash_tx
            receipt = index.get(cash_id)
            if receipt is None:
                missing = True
                continue
            if (missing or receipt.chain != 'ethereum' or receipt.block != cb or receipt.timestamp != cs
                    or receipt.minted or len(receipt.movements) != 1
                    or receipt.movements[0].account != CASH or receipt.movements[0].change != amount
                    or receipt.movements[0].external_income or not stamp < cs or amount > remaining):
                raise ValueError('Spark BUIDL cash settlement differs or lacks prior payment')
            # Partial cash debits only its amount. The final receipt closes the
            # remaining claim at actual proceeds, realizing any funded loss.
            before = amount if n == len(payments) - 1 else remaining
            index[cash_id + SUFFIX] = replace(receipt, identity=cash_id + SUFFIX,
                movements=(*receipt.movements, AssetMovement(claim, before, -amount)))
            del index[cash_id]
            remaining -= amount
        changed = True
    return replace(history, batches=tuple(index.values()), custody_accounts=custody) if changed else history
