"""Classify the existing E38 cash-incentive policy in saved Grove histories.

E38 is skipped for position pricing because its AUSD is already held under
E14; its cash_distributions still recognize income. These actual receipts
from the two configured Agora payers create no borrowed principal.
"""
from decimal import Decimal as D

ACCOUNT = 'ethereum:0x491edfb0b8b608044e227225c715981a30f3a44e:0x00000000efe302beaa2b3e6e1b18d08d69a9012a'
RECEIPTS = (
    ('0xf0d1a3598fe3debd7f5d63aa1112a90fdd1a2a0887b178b45852c0b702d3d79c', 24536842, D('25')),
    ('0x5c01e26000297f95c45339984b5de96fe4faa46bc2cd6e7069c37867595ba0a6', 24581357, D('32851.71')),
    ('0x89091f3d8c0094d2806fc04996ebc02f3507fac58237fd581f27cb7687a1d1cf', 24588809, D('226376.97')),
    ('0x9eb68c24f27f340c30ab3701f0b88c8faa699ed9f1375ae5a9b1f241ba8566c9', 24588817, D('302033.84')),
    ('0x4237e16786e6397e3bc5c86d09a0b91066a7dfec6d40b3ee045dcd15aaded0f0', 24889248, D('455323')),
    ('0xba3939c357bdf33879a19cfd496553b29cb1699f64d25e197107d16f6d353c37', 24889248, D('402261')),
    ('0x9808ac9ca925603649a444f66c9aa28b1513ab1091324c38ad8a0aed09134a26', 25202482, D('398324.92')),
    ('0xbe8c60e79230555cb3b959c5934b00eaf90d479729d5017054cc7d43bb4d0065', 25840963, D('857964')),
)


def recognize_grove_agora_incentives(history):
    from .grove_cash_distributions import recognize_reviewed_distributions

    return recognize_reviewed_distributions(history, ACCOUNT, RECEIPTS)
