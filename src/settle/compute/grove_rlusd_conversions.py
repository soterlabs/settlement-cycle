"""Reviewed RLUSD/USDC issuer settlements at Grove's Ripple entrypoint.

The December 11, 2025 spell explicitly approves both tokens for this wallet:
https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20251211/GroveEthereum_20251211.sol

These exact historical groups contain 11 conversions and two returned test
transfers. Partial USDC payments release existing principal at par; the final
observed payment closes the claim and realizes the demonstrated shortfall.
No future payout is fetched or used to close an incomplete pinned history.
An issuer wallet's other funds are outside this allocation boundary.
"""
from dataclasses import replace
from decimal import Decimal as D

HOLDER = '0x491edfb0b8b608044e227225c715981a30f3a44e'
WALLET = '0xd178a90c41ff3dcffbfdef7de0baf76cbfe6a121'
RLUSD = '0x8292bb45bf1ee4d140127049757c2e0ff06317ed'
USDC = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
SOURCE = f'ethereum:{HOLDER}:{RLUSD}'
CASH = f'ethereum:{HOLDER}:{USDC}'
SUFFIX = ':rlusd-conversion'
# Reviewed payments, reviewed receipts, same-token test refund.
# Amounts are token units at par; fees vary and are not inferred by a matcher.
GROUPS = (
    ((
        ('0x8237464915bf28d77bab4f75e41c976a7b5966b2cb757c50f5a01235b0ee1c2d', 24597947, D('1000')),
    ), (
        ('0x28b2786fe6bc9ecddfb54fa1d617a8a78d56ec784e84919fb7a80794af81d389', 24598057, D('1000')),
    ), True),
    ((
        ('0xe5dbcefa613aa133dbc204add944ef2b4f8b56f31c7c3be1f199e394c18a63d4', 24598829, D('50000000')),
    ), (
        ('0x3c2118e70e466dfc4431c3cee6f86c3e8fc3dd4390b7c65b370a5a87d00e5dc0', 24598923, D('1000')),
        ('0x6143f746085e540ee44f8b5af2f8e2c71c8f753c5142f91bdb0fe5dd5ed4447e', 24598967, D('9989000')),
        ('0x59bd914eeec91b64a783083c2e35e0b2313a8afeaf7d81cc8a348546a5c34f75', 24598969, D('9000000')),
        ('0x0d8b1d5715011bd6e66edb4746b490089aa80cddafc07203b622a13d0368f625', 24598973, D('9000000')),
        ('0xfa8e13ff24f9ac46796c2a84dcecf69eac4ce152d7989013846fbc19f7d10413', 24599023, D('9000000')),
        ('0x88ab4de3dcc7e8cb6c7b8983357a98a7e66dcc5f499ae93f7299e0141c13d39d', 24599025, D('9000000')),
        ('0x8943920fc23408a986efb650f7fbff64a86f7058d530be58975b75ef8116f22d', 24599088, D('4000000')),
    ), False),
    ((
        ('0xa0c9e5df3d9137d4a1ffcabea21dde549e6ead6686f6d6d2234e4f04be31ef92', 24621162, D('20000000')),
    ), (
        ('0x4982a1aa8071046676b74e315ff8915c91a65d18c32c0cd16b076cadbc922b9e', 24621371, D('9000000')),
        ('0xedcc6e4b2f046049e9a4471c89b91ae1f5aa3800b29942a872730eab110e4a03', 24621372, D('9000000')),
        ('0x044de74793a206d559a60c3a11095e8dd9c970e7b6720b8cfd38cf46ea58e270', 24621377, D('1996000')),
    ), False),
    ((
        ('0x4424a61a7c71aeb0e7c2de43fca8965396451653b8d8bba37ffe2a6111eba5b5', 24692807, D('1000')),
    ), (
        ('0xb19ad820a58ee52586f964175746c582c01abd62c76451cfe1b6102a6a082088', 24693107, D('1000')),
    ), True),
    ((
        ('0x272746ee9de83b322fa7efa74c039aeef45ab513b655bea17dbfb06ccb4e0c81', 24693202, D('50000000')),
    ), (
        ('0x9623f7189e25369fdaa5a205751e473ad2094b333b6f5b069524789543d58c2d', 24693840, D('19000000')),
        ('0xe59c8a199efd006534abd7b3ac6b10a7d6bf7b7bd6442ec755d4df45b4d0e198', 24693843, D('11990002')),
        ('0xa6439e0a668c58570a59f8c8dfcf5d6edc59f2af751e223e143a9903f618fa81', 24693845, D('19000000')),
    ), False),
    ((
        ('0x75447101fabdcf3e083d95ef5e90707b424eca53a9b8dbe00da2ef061e547bd3', 24900541, D('1000')),
        ('0xe15e729c621bfab7cc4815dffd5c038aeeafa7e4e30bb8f6cfad645a66cc14a3', 24900584, D('24999000')),
    ), (
        ('0xb27f265bf6f32de2199b631efa7cc32062cd4956ad7f765f51d6144554b3361b', 24900639, D('15000000')),
        ('0x15b5b43d19c010d9c104d95a27a943e4bcfaabf66ab0361a6d3069c29d560cc8', 24900649, D('9995001')),
    ), False),
    ((
        ('0xd388cf28c91bd3bea55b6e0db47533f2845edff42d1ed604514893ccf536abc0', 25230628, D('100')),
        ('0xabb0c96beb523de8fbb278269d6f068f548bf0b000b871d2b41706e8c5c7089f', 25230708, D('24999900')),
    ), (
        ('0xe14ce829cfa02cdc7615359312ea1cfc8582161b63ce87c5e85f6c40271af805', 25231013, D('4995001')),
        ('0xb9b6705f2e2857eb8aa5d4ff03145d5343f0711383e11c69e6ebde9a38865906', 25231015, D('20000000')),
    ), False),
    ((
        ('0x5d45edb1430566f2b9f1f56fb848163ca6e5031ebe47e9d8a2463845dc8fea97', 25246647, D('25000000')),
    ), (
        ('0x837b5b9b37e5806372dfd2b3f90cc07f87c360078c7a594646639d17747b5e3b', 25246735, D('10000000')),
        ('0x2d6ca0a0b2d9dd1cbdf8604fd21182a4f11f70fabff7b3a66bbab9d8db296fac', 25246736, D('14995001')),
    ), False),
    ((
        ('0xb30cfd281495b4d47643abfc37814930118200600d8416bd241942df19562f52', 25296443, D('10')),
        ('0x49f8e64bb9128164dd58fd7a049822a0701a37b13281a6dcc874db6ef6e03210', 25296863, D('19999990')),
    ), (
        ('0x972f7a7f20b1adf97c97237d08918965a8d65fbafc689538945b5c32f117d438', 25297017, D('19996000.8')),
    ), False),
    ((
        ('0x62950281cc3856fce1c6abcddffb8b1d536f7165554831227aabccf5eec0f4f6', 25447928, D('10')),
        ('0xf18ff4d1b5e342ca89f0a443b5ab04c51a1faf1475384854f38661ffcf218b73', 25448268, D('12999990')),
    ), (
        ('0x05776857ec8be89b918d87af95673d3d6760216e271fd880ed6f0c65d4eac80b', 25448842, D('12997400.52')),
    ), False),
    ((
        ('0x0f36f278ecf2d1bf7bbdaa8822932833f93e7e6933da69fac4fa7233b12ae436', 25497336, D('10')),
        ('0x0ac0eb560b95af40ea20887d52a122c7d1fb2ee0b2fb2f752ea5b71ff59da2c8', 25497414, D('9999990')),
    ), (
        ('0xcbaae51126f468003a44a3d7e034a44d995ae453bdb175473ac0adc6b38c5326', 25497691, D('9998000.4')),
    ), False),
    ((
        ('0xa13cca790f5d5529a27479b357204be6b6203a1aeea557b81312a3cfcd519023', 25574621, D('10')),
        ('0xeaa102590788e2e0ac25b60c012706084260d2da75aadc3f229da2f19ef5292f', 25574693, D('50000000')),
        ('0xd156fd040c9e381152fb79d0040d3ae7d29aa71f9ce3f27c1921f66534c4b918', 25576890, D('14999990')),
    ), (
        ('0x218a7a8d38b94191900b70bc6cea88acf038978f0d8ef7299bcf0b1f8ef9b5db', 25577677, D('10')),
        ('0x12d74d52ddc4d192d07eccf7b80aa5608dd71e571ca32fe70b540a7b252a3618', 25578018, D('20000000')),
        ('0xe55f31a910cb9fb9a7527110bf7d7c6e4f5004be00ef1267a62dcaf05b759cfb', 25578018, D('20000000')),
        ('0xddeb3df04d76d3b2cd3ca3bc9f4381de96d45db04a0920e6ccb3688e545f5ccb', 25578022, D('20000000')),
        ('0x5f377bbcadf56e74f826440e41cda3011080d40cff0b93046e5c143c612b18bc', 25578028, D('4999990')),
    ), False),
    ((
        ('0x2abdc63605873e3c8afe42fd449317177d42450719074ef7573f6f4d610070a7', 25581388, D('10')),
        ('0x5b898eacc59049f4390c336b8251419080f3436cc8d7fd6ae70bbc5ea80ea125', 25581542, D('20285901.1')),
    ), (
        ('0x823a9c51e23e2c6fac6ec33c33c91d9b33c93970d98f41c72ccc65f9d1278fb3', 25581613, D('19000000')),
        ('0xb750e9e87c65bd9a8f180550270510ab2d8850ceb63d53d0a4061bdce040cac2', 25581616, D('1285911.1')),
    ), False),
)


def link_grove_rlusd_conversions(history):
    from ..normalize.allocation_capital import AssetMovement

    if SOURCE not in history.venue_accounts.values():
        return history
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    custody = {v: list(accounts) for v, accounts in history.custody_accounts.items()}
    venue = next(v for v, a in history.venue_accounts.items() if a == SOURCE)
    for payments, receipts, same_token in GROUPS:
        identities = ['ethereum:' + tx for tx, _, _ in (*payments, *receipts)]
        if any(identity + SUFFIX in index for identity in identities):
            if any(identity in index for identity in identities):
                raise ValueError('Cannot append raw events to linked Grove Ripple conversion')
            continue
        claim = 'conversion:ethereum:grove:rlusd:' + payments[0][0]
        pending, last_order = D(0), (-1, -1, -1)
        for tx, block, amount in payments:
            identity = 'ethereum:' + tx
            b = index.get(identity)
            if b is None:
                continue
            source = [m for m in b.movements if m.account == SOURCE]
            available = b.minted - sum((m.change - m.external_income for m in b.movements), D(0))
            if (b.chain != 'ethereum' or b.block != block or (b.timestamp, b.block, b.log_index) <= last_order
                    or len(source) != 1 or source[0].external_income or source[0].change > 0
                    or available < amount - D('.01')):
                raise ValueError('Grove Ripple conversion lacks its normalized source funding')
            index[identity + SUFFIX] = replace(b, identity=identity + SUFFIX,
                movements=(*b.movements, AssetMovement(claim, pending, amount, preserve_basis=True)))
            del index[identity]
            pending += amount
            last_order = (b.timestamp, b.block, b.log_index)
            if claim not in custody.setdefault(venue, []):
                custody[venue].append(claim)
        remaining = sum(amount for _, _, amount in payments)
        for n, (tx, block, amount) in enumerate(receipts):
            identity = 'ethereum:' + tx
            b = index.get(identity)
            if b is None:
                # A later receipt in the input cannot silently skip a missing
                # earlier payout; remaining then differs from actual pending.
                remaining -= amount
                continue
            destination = SOURCE if same_token else CASH
            if (b.chain != 'ethereum' or b.block != block or (b.timestamp, b.block, b.log_index) <= last_order
                    or b.minted or pending != remaining or pending < amount
                    or len(b.movements) != 1 or b.movements[0].account != destination
                    or b.movements[0].external_income or b.movements[0].change != amount):
                raise ValueError('Grove Ripple payout lacks its exact outstanding conversion')
            final = n == len(receipts) - 1
            # Keep the unreleased principal pending through partial payments.
            # Only the final observed settlement can realize the known fee.
            value = amount if final else pending
            index[identity + SUFFIX] = replace(b, identity=identity + SUFFIX,
                movements=(*b.movements, AssetMovement(claim, value, -amount,
                                                       preserve_basis=same_token)))
            del index[identity]
            pending = D(0) if final else pending - amount
            remaining -= amount
            last_order = (b.timestamp, b.block, b.log_index)
    return replace(history, batches=tuple(index.values()), custody_accounts=custody)
