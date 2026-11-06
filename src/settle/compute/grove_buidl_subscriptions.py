"""Exact Grove BUIDL subscription links, independent of revenue recognition.

The July 24, 2025 spell authorizes USDC transfers to BUIDL_DEPOSIT:
https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20250724/GroveEthereum_20250724.sol
Its test demonstrates transferAsset(USDC, BUIDL_DEPOSIT, mintAmount), followed
by issuer delivery. Stop at this issuer entrypoint; do not trace commingled cash.

The 26 actual payments below fund 20 later issuances (February-April 2026).
Each reviewed subscription finishes before the next starts. Six pairs have a
$15,000 fee, consistent with config/grove.yaml's existing fixed-fee policy.
The canonical Transfer logs are in tests/fixtures/grove_buidl_subscription_events.json.
Only these reviewed identities are linked; ordinary rewards and unknown mints
are not matched by a size threshold, amount or nearest date at runtime.
"""
from dataclasses import replace
from decimal import Decimal as D

HOLDER = '0x491edfb0b8b608044e227225c715981a30f3a44e'
USDC = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
TOKEN = '0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041'
DEPOSIT_WALLET = '0xd1917664be3fdaea377f6e8d5bf043ab5c3b1312'
CASH = f'ethereum:{HOLDER}:{USDC}'
SHARES = f'ethereum:{HOLDER}:{TOKEN}'
SUFFIX = ':buidl-subscription'
# ((cash tx, block, paid USD), ...), issue tx, block, delivered USD, fee USD.
SUBSCRIPTIONS = (
    ((
        ('0xc85a2e406f6dec20c1439e555b5873cea5016546f7a437f3e4cd9b47d7617aad', 24391800, D('1000')),
        ('0x8c994654e4844fd01fa92c48935294ced2c89dfa4d0dd110145a8904d08fce80', 24391857, D('49999000')),
    ), '0x8bec4131ad2540ee7759c6f3a5f98fe1df9ab5520f4ca82171cf57a48f6cfbcf', 24392806, D('49985000'), D('15000')),
    ((
        ('0xe1f4fd4e2fa05beac131031313e25c03a0ee4907b8d83ad5319a05121677c6c4', 24399360, D('50000000')),
    ), '0x791a722719049eeaa0bc60a31551e7a27d2a8333a984bde3a272809a165de0cf', 24399959, D('49985000'), D('15000')),
    ((
        ('0x86df855c2acf17cb1b401f459047a0d7fc62f7462318f9b8ac436e4b518c7680', 24477701, D('1000')),
        ('0xf54888c2cc65ed46f3e1080dd2551063ac75c7c4c254d507d95133f092e8f926', 24477915, D('49999000')),
    ), '0xa418c583e8b4a92e139c31212a32fcf73437bc8b95f1a13266150d003f779985', 24478735, D('49985000'), D('15000')),
    ((
        ('0xce02f6f4a2534bf7ef72953724fec50d2c5497ec92b657e9e7ef0d5cf86a478e', 24485284, D('1000')),
        ('0xb9173cdd63b0e9e19d880867ffbee58eda3da9057c4b5d4476dba4cc3198faa0', 24485370, D('49999000')),
    ), '0x9d9f5c9f00a0d070c39a84a894be542ab21be28ca11814533bfec99645dd270b', 24485905, D('49985000'), D('15000')),
    ((
        ('0xad4b2b6dd6f07a4790415b96be0487cc098c7d0045bafdd66f2ca591b8632fcd', 24549202, D('50000000')),
    ), '0x5213c04071a57297f8d847db52733c34461a46d2a2db3ae2ee1db55e93c8fd2b', 24550451, D('49985000'), D('15000')),
    ((
        ('0x2cfbfabc9c0aeb6a9c75c82bbfaa0cf8ab4a2f11d3b78f0894dce071e68b3bad', 24570867, D('50000000')),
    ), '0xfe67f42564925bd41e37be3a7851c7902d425fe6ef45f12593a0c1e8065655fb', 24571941, D('49985000'), D('15000')),
    ((
        ('0x24913ee2fa9f4b50e3412b1ccde8d757c49f3d6d368219f7ecab3e863672822a', 24721652, D('1000')),
        ('0x28c403166a4fe856517555f4b60782b3106e589331b1107a04e2e09e9143df89', 24721826, D('49999000')),
    ), '0x278e1eb474b38a83f7b64a460d80a51798c173e921b7a370f0209849a3b3d849', 24722103, D('50000000'), D('0')),
    ((
        ('0xd28f083efd14049f2d5aa2ffa7358687b620a93f229b58517cfb4c8e52dfff55', 24728274, D('25000000')),
    ), '0x9ee7d7cb4a3f2baf685f5ed206b394e9a6f141d95a6b279e71fd0a70b05e5405', 24729279, D('25000000'), D('0')),
    ((
        ('0x637b985cf3581e8c9956c5fe1880566be372e06ce248ab9c49f2913a1451510a', 24734597, D('25000000')),
    ), '0x1b8de5c5e85e286d9e9a4f28efa7738fe0e2877d82a8c7df728e87408fd79d0b', 24736444, D('25000000'), D('0')),
    ((
        ('0x3f5c004a04385b3e74e86d34aff89c380b65f2201fa241ab4e512a8fb3b9809b', 24742575, D('25000000')),
    ), '0x2ce44dc910cff16b6dc9ba4b5d9ba51dc699824be18e0f14160747ea6921d965', 24743613, D('25000000'), D('0')),
    ((
        ('0x03b2dc503f9b79d8d856c5da1abacb2f40fbe533e5ab306616f9de44eb4d4ede', 24748102, D('25000000')),
    ), '0xc72adc8b9777d636ad900fe39967c32e5b57993652e5ff9526eb9eee36ce5b34', 24750776, D('25000000'), D('0')),
    ((
        ('0x26ddddfa518db772925b12ac143dd715f1efed2baa941633ec2965cee84d1c18', 24769347, D('50000000')),
    ), '0x80dd652e2eb52baea619b1f8903accd8d9409dba3b091333e09d0e1c867af812', 24772297, D('50000000'), D('0')),
    ((
        ('0x8e0ebfcfbc38f1e70046e297ce2c9123af812ac9fc173d1a3c6321a27a391e1b', 24776672, D('25000000')),
    ), '0x0a1d484a1b3999bf243352528a33de80ad741f121f7358fc485be72a8efb183a', 24779471, D('25000000'), D('0')),
    ((
        ('0x262e9ae350fccf5d55d1b0e3e2e9a7ee06d2301ec96958a9a65d080e37c7f4a5', 24785383, D('25000000')),
    ), '0xfbcea8eff9439b96595e7cdfa0d1793eaa2473e92e4ed52b37ecd12a2d42a1ad', 24786631, D('25000000'), D('0')),
    ((
        ('0xbf02195f6052c9f037a9127a9a252f1cbc89b7fe5bfcbe36e5be91d9eb6b881e', 24792333, D('25000000')),
    ), '0x6a1e632728a3b509e103d49a56e093dc8c5b63d46c063142a0551e2e101e3460', 24793806, D('25000000'), D('0')),
    ((
        ('0xde82b4f4217685eb699893518ee1556f010b9534b3407e35c1650f7a18b583b7', 24820901, D('1000')),
        ('0x3b80d58e3c4faf886342d43567cc57e0ed9cc4331a7b1521df9ff71e917acf49', 24820950, D('49999000')),
    ), '0xb95c2603c4a1e28c2be46288c49ac73b79de12e6aac06b2612e82c4513cf6327', 24822515, D('50000000'), D('0')),
    ((
        ('0x392c73635e97c824c45044c083f49ce944866dab36097c281539e09b409f06fa', 24828977, D('50000000')),
    ), '0x1a135ca8765c0171dbe777395c0cae99bd1f4960c1c1f867625708994e2c9082', 24829691, D('50000000'), D('0')),
    ((
        ('0xe3e6aea5d677286b4452969911af59ba693944c1b1bfdece884d50d2504713b0', 24836104, D('25000000')),
    ), '0x6ff228e9ffd1a255e68d2933b9484372afeb1a1afdbe664768f760108dd75ce9', 24836872, D('25000000'), D('0')),
    ((
        ('0xf4749450ecda36010edf3fe0717c73a77782d65c3a223836688467061be13339', 24849758, D('50000000')),
    ), '0xb81e7239f9a27ea6cf344b526966a6328d0da78687e56f6256ddf4d8dc027738', 24851207, D('50000000'), D('0')),
    ((
        ('0xd092eaf18c7f69366aea985b8a374df95d78979b7fd98d51de433a9fd6d2c0d4', 24871615, D('1000')),
        ('0xe3795ac069feb717d9d430751cd628a969f49d09d5f043443f0a241fd75a88a3', 24871911, D('49999000')),
    ), '0x543a72632ef5acf8909fd4ba464b3bea6b6a5335905d385514444c7958f038f6', 24872735, D('50000000'), D('0')),
)


def link_grove_buidl_subscriptions(history):
    from ..normalize.allocation_capital import AssetMovement

    if SHARES not in history.venue_accounts.values():
        return history
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    custody = {v: list(accounts) for v, accounts in history.custody_accounts.items()}
    venue = next(v for v, a in history.venue_accounts.items() if a == SHARES)
    for payments, issue_tx, issue_block, delivered, fee in SUBSCRIPTIONS:
        payment_ids = ['ethereum:' + tx for tx, _, _ in payments]
        issue_id = 'ethereum:' + issue_tx
        route_ids = [*payment_ids, issue_id]
        if any(identity + SUFFIX in index for identity in route_ids):
            if any(identity in index for identity in route_ids):
                raise ValueError('Cannot append raw events to linked Grove BUIDL subscription')
            continue
        pending = D(0)
        account = 'subscription:ethereum:grove:buidl:' + issue_tx
        last_timestamp = 0
        for identity, (_, block, amount) in zip(payment_ids, payments, strict=True):
            b = index.get(identity)
            if b is None:
                continue  # A pinned history may end before a subsequent payment.
            cash = [m for m in b.movements if m.account == CASH]
            available = b.minted - sum((m.change - m.external_income for m in b.movements), D(0))
            if (b.chain != 'ethereum' or b.block != block or b.timestamp <= last_timestamp
                    or len(cash) != 1 or cash[0].external_income or cash[0].change > 0
                    or available < amount - D('.01')):
                raise ValueError('Grove BUIDL subscription lacks its normalized cash funding')
            index[identity + SUFFIX] = replace(b, identity=identity + SUFFIX,
                movements=(*b.movements, AssetMovement(account, pending, amount, preserve_basis=True)))
            del index[identity]
            pending += amount
            last_timestamp = b.timestamp
            if account not in custody.setdefault(venue, []):
                custody[venue].append(account)
        b = index.get(issue_id)
        if b is None:
            continue  # Outstanding paid subscriptions remain assigned to E10.
        if (b.chain != 'ethereum' or b.block != issue_block or b.timestamp <= last_timestamp
                or b.minted or len(b.movements) != 1 or b.movements[0].account != SHARES
                or b.movements[0].external_income or b.movements[0].change != delivered
                or pending != delivered + fee):
            raise ValueError('Grove BUIDL issuance lacks its exact funded subscription')
        # Issuance changes custody, not the amount borrowed and paid into
        # this allocation. Keep the full subscription basis even when an
        # explicit issuer fee reduces the token face value. A later cash
        # redemption can realize a principal shortfall; this share delivery
        # does not retire debt or create gains. Revenue already accounts for
        # the fee independently and is not recalculated here.
        index[issue_id + SUFFIX] = replace(b, identity=issue_id + SUFFIX,
            movements=(*b.movements, AssetMovement(account, delivered, -delivered,
                                                   preserve_basis=True)))
        del index[issue_id]
    return replace(history, batches=tuple(index.values()), custody_accounts=custody)
