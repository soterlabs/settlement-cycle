"""Reviewed Agora AUSD redemptions: preserve capital until USDC arrives.

The January 29, 2026 spell authorizes distinct mint and redemption wallets:
https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20260129/GroveEthereum_20260129.sol
The mint wallet also pays USDC back; the AUSD recipient is the redeem wallet.
The paired canonical logs are in grove_agora_redemption_events.json. Both
assets have SIX decimals. Twenty-nine non-overlapping reviewed groups settle
at par except a demonstrated $0.004662 difference; no unexplained receipt is
assigned by proximity or amount at runtime. The issuer's internal balances
are outside our allocation boundary.
"""
from decimal import Decimal as D

HOLDER = '0x491edfb0b8b608044e227225c715981a30f3a44e'
WALLET = '0x748b66a6b3666311f370218bc2819c0bee13677e'
REDEEM_WALLET = '0xab8306d9fefbe8183c3c59ca897a2e0eb5befe67'
AUSD = '0x00000000efe302beaa2b3e6e1b18d08d69a9012a'
USDC = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
SOURCE = f'ethereum:{HOLDER}:{AUSD}'
CASH = f'ethereum:{HOLDER}:{USDC}'
GROUPS = (
    ((
        ('0x0524bf5610424613f20c6e8b9b4c114e99319bea51337d317d0a507c05e90ad0', 24470795, D('1000')),
    ), (
        ('0xd95f28cfb11655fc53d8a8f636ce87f7ed81d4efd6ea8e8bc26d837808bcd70b', 24471487, D('1000')),
    ), False),
    ((
        ('0x887a9edcde89384f2c269d6ec79c15d51412f4d56eec1d37a0bb7852603219ef', 24471513, D('4987867')),
    ), (
        ('0xf798a47489ec5936b0737822556bc349b54015ecb815a29520175043d11f4b5a', 24471614, D('4987867')),
    ), False),
    ((
        ('0x84be7885d8d4b8135195cc53647f0100d69fd30bafdbd499fc8f74d620b4acaf', 24535065, D('1000')),
        ('0x0a589b440856f962116e45083c2bce09addea52883e2151360f8c4fecb9ad737', 24535174, D('3999000')),
    ), (
        ('0x6ff298f0efde88d3bffbc268696bfa2677e3da58e5b2afe2c650262797cabd35', 24535308, D('1000')),
        ('0xc60d8a2dbb46bd1f0e81f0a1ce9ae20710b9e69f976e8cabd8a01241fcd548bc', 24535480, D('3999000')),
    ), False),
    ((
        ('0x6ce2ec9f6919f11f431afa7df2984b9d14e11a3fb5c60919c648106737ea6a10', 24536781, D('5999000')),
    ), (
        ('0x59251fd12886d244131aa3236236a282a87d3f1a64bd372d83b25830090f2372', 24536933, D('5999000')),
    ), False),
    ((
        ('0x784cac7305a22ceb3bc579c1786540ef6a63f757fb00e47f709622955420ab95', 24628211, D('1000')),
        ('0x1486c74f52f109a53aa422b95eae8c8f8a7e826f2dde8791ea3b61ba4cf11c89', 24628781, D('560262.52')),
    ), (
        ('0x4865d7719dac0e6b58ef75acd296aad5b49fd7c44aab13a375b55d76de2fa7bc', 24629418, D('1000')),
        ('0x0a68a2b64824c42b6364c0a0b7a54cf56f0d9e9f0d675832e4120b0205dbbeb4', 24629500, D('560262.52')),
    ), False),
    ((
        ('0xa07909cb5749d3cb0402d9907609d8ac66f229b02548c1adc1b2e18b0d8360ec', 24829087, D('1000')),
        ('0xf9294b6a75f6903f08175e19cdade35bae017da3e7db3e846b6ae4950667b70a', 24829123, D('1999000')),
        ('0x3aa1ddd3565440d943d1139499073720e32307f13ca67a4a912de602404aad4b', 24829353, D('998754.81')),
    ), (
        ('0x9c15a567bdf9eac48d1098512dcb9073919038bd7310a38d2f20b551b3008e8e', 24829795, D('2998754.81')),
    ), False),
    ((
        ('0xa67684111164714c459ef52fbb3418fa5f23c519096cc30f943c525c606914b9', 24842244, D('3999683.294662')),
    ), (
        ('0xac5f80b816d26c39bea6474d9b73e722dc7d4c35361fc424fabc60937ee94ae0', 24844956, D('3999683.29')),
    ), False),
    ((
        ('0xd79ddf551f03584bee52eea7fc46d5c064271c746f3367e21e7a7babc42f3af4', 24929465, D('1000')),
        ('0x7a3d23bdf381b69ba035d145da0c99c49c38383daadfb382968c54e38653a5d7', 24929496, D('1999000')),
    ), (
        ('0xe863e8fbd3dc32fa7a4ab1cc420ef302dde234a83709b783a59c42b694534c7c', 24929855, D('2000000')),
    ), False),
    ((
        ('0x47f1154c8fd12a2242be1613d79f316087b1d6af8f07bb70ce54f637a2b44892', 24981531, D('1000')),
        ('0xe3c4f1e9559edf73a1c45d2a8338c3d493cccf37de205ea5fccc6b6b683915c8', 24983709, D('3182000')),
    ), (
        ('0x0d17c7c658d28a2eea807200e628a1bf57d5084d1d9b5d30430b1984ff2094a2', 24985669, D('3183000')),
    ), False),
    ((
        ('0x06f50229d944971b9c90750ac6336eedd6981601b018bcbf5157848a40d362bb', 24989531, D('2000000')),
    ), (
        ('0xe07c9b7beb186688d8846e5036bfc9b8f38dfed5a32d6a7d12bf8b58eef15808', 24989656, D('2000000')),
    ), False),
    ((
        ('0xeb520a17e45cf8446a5e91ad546d09631f9131a620d5dd1da6f10232bca4a9c9', 24992816, D('1000000')),
    ), (
        ('0x593437f56e95a142d454017e430b9fd6ec47e94f2ddd86ed83da7c09054db86b', 24993671, D('1000000')),
    ), False),
    ((
        ('0x22b844db3e519cf3f665fc48647a93ce9cf57ef0eb5a49ad61c19bb24e8fd528', 24995081, D('1000000')),
    ), (
        ('0x9f89cbd80b4167a01cf0ee17c016188fe80442e6a054f477590cdbe1b30db56e', 24995213, D('1000000')),
    ), False),
    ((
        ('0x8331095ac827bbfd225c48d0be9c7c822a5e2894f3378151f8b4424e2165b647', 25073985, D('100')),
        ('0x748565361907986b3d6dad22bdd92b4f120b33e5a2f87b603ad4e4bfd40dd75f', 25074068, D('2999900')),
    ), (
        ('0xbac97dd3abc071e5b164a2a3eb4c4ec6a48495943757dce335b9780e6653e5e4', 25074148, D('3000000')),
    ), False),
    ((
        ('0x1850cc83a9af48ff69eb451de42bdf119a1a9becec09d572faf2107d167e8cf7', 25086771, D('2000000')),
    ), (
        ('0x9d83943d65c361656bc139f19a908d2c17162a51775e8ff98093ba999137de37', 25087155, D('2000000')),
    ), False),
    ((
        ('0x0c7f88b1db765f2c5aa16c651c4850ae333b7f8260ba33dd9ee74cd26805193d', 25095869, D('2000000')),
    ), (
        ('0x6038340240b89da6664bc687e95d80433443533410d8d4f03dbe4262b59deb3e', 25095929, D('2000000')),
    ), False),
    ((
        ('0xcf6427f5f921d4a586c19c90c2f872b42bf45061395e9dd61cc9ad05add15bec', 25153465, D('100')),
        ('0x1b8812b13ba00b40c63541fd73536cc351e3770ab65eca4c2738e15373196086', 25153608, D('3999900')),
    ), (
        ('0xf0c43f86fc001ddf16e47a5b6b716349eae8d2b51c9d3c5f4cdf87cf899290ee', 25153771, D('4000000')),
    ), False),
    ((
        ('0x1e5db5d4e0a82be9f09c843b3f9c3a3f4609e207e6630450fa9daf45a7f3ac34', 25223078, D('100')),
        ('0x91dc1e54b96bb10ba801aecebbf4068f58e39760b2c172780a2b21dfbfde83cc', 25223315, D('2399900')),
    ), (
        ('0x83a1ec989a5700af4396746d479e474983c3dd4bfe455717fff812ab9f8e0d79', 25223439, D('2400000')),
    ), False),
    ((
        ('0x57b42e28a4563f4e402a77e9dbc0b48b295f47bab6d3ae449b5ca7267af437d6', 25245909, D('1000')),
        ('0x640ed3fb32db2423bb60928cda504ca6e734db68c6acbfa860fd7bc528f8e43c', 25246028, D('999000')),
    ), (
        ('0xd60c80f7651fc68d25c766c3ea232849b10abc924707eb3a46cd2aff886e8af2', 25246135, D('1000000')),
    ), False),
    ((
        ('0x53768bf11a592d0a0132240d7af7abd5fa3f8addca765285e8838bd4f6040803', 25403008, D('1000')),
        ('0xcc1c3ea77a384ae99aed33f92b70569a2ca15cb8b21e4b0ab802bfc3f9d5b8d5', 25403137, D('2677830.24345')),
    ), (
        ('0xbfeb88de5e556654e5684d642316d396a4515c6e64cb9cd497efb6748e61d57b', 25403186, D('2678830.24345')),
    ), False),
    ((
        ('0x9ac3bd4bdd87bda7cbb02bc4103be9d76604ae2c7eb97caf9ea5f9f7be0c6d5d', 25432146, D('1000')),
        ('0xe521625622eb781de1924a560806e8e2e7a3d400a8cd321e2a6b8d73859941c0', 25432211, D('2546978.216107')),
    ), (
        ('0xf54c70316b87c080e9441821550c12211d302f13c5cac4c68da79800c2bcdd2e', 25432257, D('2547978.216107')),
    ), False),
    ((
        ('0xf6817cf4197e60c6307cd0fdf9796bf4907d9aed6ffafa3fc08760e685a85fa3', 25473880, D('1')),
        ('0x9b7483a57965c38ad3d0ef90b9b68b0c2101c1f1fb184f970101d4c0328f5234', 25474265, D('3847256.514618')),
    ), (
        ('0x182290ab0080aeac8177aa98d1512bc01f8ee7cf82f7aa52205576c5b7ef9f81', 25474386, D('3847257.514618')),
    ), False),
    ((
        ('0xb1755ddced9c21d0d902aff9e6b99ed1f804949ffab1d79085178e2fbf223407', 25495450, D('3511953.450548')),
    ), (
        ('0x6659e5f7c1e1cdb5c921ccb4f81c801570d9ed2983e9562d6f618b38df190b02', 25495641, D('3511953.450548')),
    ), False),
    ((
        ('0x895999b96c6009a7d552ad2a5c0bce575b11606fe75b182c5114f0842ca5453c', 25703653, D('10')),
        ('0xf652cc65aec60d72623dd6c3d4c4e77555e3ad8bfdd99a92b0a2a1754d78a7a0', 25703916, D('4499990')),
    ), (
        ('0x08156bbadb56fae42e637571529a6caf61c2b72c85b03a786650afe6bf22ba2a', 25704059, D('4500000')),
    ), False),
    ((
        ('0xa15216fadae646b5251c695b531cf127111560d95195987613c1d17b68ef2c14', 25726001, D('10')),
        ('0x08d962dcdf29065bf6ae90fcfef7ec5fbaba9b964e7d8355200d3433da392d24', 25726136, D('1499990')),
    ), (
        ('0x824dd1adfdfac5098d8b6fc5e1e935fd96a3c581b3ef6c7910c938e8c8020956', 25726234, D('1500000')),
    ), False),
    ((
        ('0x402d3aeb8049f2ac33ffbda112e6a16ad9babd4cf1e44d2c10d835d71882dbc1', 25739632, D('10')),
        ('0x3b2f94ca433264748a3534dc3357f0f91015c451008c4521fea21192210e9d4c', 25739723, D('999990')),
    ), (
        ('0x37777325e2e97118a6ad415e53d795d390550b5816bbb323cc2c4128e5a91d87', 25739826, D('1000000')),
    ), False),
    ((
        ('0x83bc7c8cb7ba5dc5df4c0fbe5ec2245eddfd699e9d247b57be257ea2179a19bb', 25747161, D('10')),
        ('0xcea125ef88aa02d8f3346e9218e32e55f978f7bbc0c3eed6d1b44be84674211d', 25747282, D('999990')),
    ), (
        ('0xba94486b67b4f1027c8ef08d73adeda1b8e7a8b78ae3a1e9c1c69de9aac3931e', 25747426, D('1000000')),
    ), False),
    ((
        ('0x35688c0713992347dfa32aac00272eab175f9c9941a65976fe9f6cdb3ab9cc86', 25755631, D('10')),
        ('0x4d57ba5c91b402244baffe38d2e686ac3c84ac2c75ae89abd37badaed0de2ec8', 25755742, D('1074897.922474')),
    ), (
        ('0xa9dc1b1b73ae2a30d1c9fa72a597ec94fa2ea0edbf6365c9937caa6f93a50248', 25755804, D('1074907.922474')),
    ), False),
    ((
        ('0xa6dd3600674ed763c2b7c76c74b8adcaa9316b6e17d853798f9c9d2b541aef40', 25775996, D('10')),
        ('0x7f92060711e8f0eccd043e3737630f7a7603eb46ff30e266d2d2f8bcba8cb108', 25776129, D('3281493.065878')),
    ), (
        ('0x62533bf75281aba11fa2f085e9631dbfbe984a4222dd26365b260d48470bdfe8', 25776231, D('3281503.065878')),
    ), False),
    ((
        ('0x14e51f7d1eccd6fd1ba6c4f0e11415947a9fc8264279febfc6e031786c87beb8', 25799649, D('10')),
        ('0x3d88145d2e340817d35febcdf326f91e4162aaef2c54410830d8d195079de083', 25799793, D('2324956.656788')),
    ), (
        ('0x9847d5c0446b11f9bbe2496b2061c365e50a03180f29f76eeb0f94eb5be92c2c', 25800155, D('2324966.656788')),
    ), False),
)


def link_grove_agora_redemptions(history):
    from .reviewed_issuer_conversions import link_reviewed_issuer_conversions

    return link_reviewed_issuer_conversions(history, source=SOURCE, cash=CASH,
        groups=GROUPS, route='agora', label='Grove Agora')
