"""Apply the existing configured Galaxy yield classification to saved histories.

Fresh extraction now reads venue.cash_distributions, including distributions
on a different chain. These exact ALM receipts let previously pinned Grove
histories benefit from that same correction without re-extracting all chains.
The distinct ARCH principal payer is intentionally absent. Values match the
existing revenue policy; this adapter changes no reported revenue.
"""
from dataclasses import replace
from decimal import Decimal as D

CASH = 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
RECEIPTS = (
    ('0xde27d338c5eed540a3f93c41509341e4650c9dd0932e0ad5bd7dbb98faa324a0', 24221609, D('3')),
    ('0xd755b61a6afc821194cdaa6a36290a495551c0983f90c5c1a174d2f68f3f1391', 24221821, D('209133.25')),
    ('0x0e95fb1c153c3919bb8b37af83d30fd3b03d9186ad2e927ca843cb7f632cff6a', 24429112, D('3')),
    ('0x0f982d4cf1aac26cdb5f0800653c40adfba3ae489156560071b33b0dffd6862e', 24429620, D('402936.81')),
    ('0x148e3bc75a43f704b1051eda2ecca483c8d124d7d796acf79e4e1cf1a87473bb', 24628698, D('363584.08')),
    ('0xae05ecbbd2e290514be7aadda0ce4f4d6a9e267a1e02c8816f88571789d9db08', 24850284, D('402742.96')),
    ('0x042b2964ef638d74533b3f4364d1b0bc671405e35eb09ca8af3abde8bc0a5681', 25073542, D('389109.9')),
    ('0x5647bf504ec6bea27556729f154372a68c9f7ad1f5cc5d604f5d07b53e7e8ae4', 25287472, D('371738.69')),
    ('0x666f0a26e42d64f6a0416532203b7bde06252cd52f512d73d42d3b6d2b07438b', 25497428, D('0.25')),
    ('0xe138a148ccb08585fb879aa9ae0c07a266298b81f69162a23bd0331a71962ce4', 25502822, D('267818.71')),
    ('0x3e2e256b5f1a165f3764dfd1905749156dc7601000fe44f4904f648066a49989', 25725609, D('222936.27')),
    ('0x2d5b514fb59d52479712a54d7f2bfd999f9ce92fe7fc54310a5e7d9eb96dcf1c', 25726234, D('20')),
    ('0x67b4556fab01bdb15ed20534a693f8e6cd7264094436c60fa368e1206a184f17', 25726528, D('474468.89')),
)


def recognize_grove_cash_distributions(history):
    return recognize_reviewed_distributions(history, CASH, RECEIPTS)


def recognize_reviewed_distributions(history, account, receipts):
    if account not in history.venue_accounts.values():
        return history
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    for tx, block, amount in receipts:
        identity = 'ethereum:' + tx
        b = index.get(identity)
        if b is None:
            continue
        if (b.chain != 'ethereum' or b.block != block or b.minted
                or len(b.movements) != 1 or b.movements[0].account != account
                or b.movements[0].change != amount or b.movements[0].external_income not in (D(0), amount)):
            raise ValueError('Grove cash distribution differs from its reviewed receipt')
        index[identity] = replace(b, movements=(replace(b.movements[0], external_income=amount),))
    return replace(history, batches=tuple(index.values()))
