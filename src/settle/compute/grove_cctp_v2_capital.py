"""Reviewed Grove CCTP v2 routes, authenticated by Circle message nonce.

Grove's January 15, 2026 Ethereum/Base spells enable these v2 controllers:
https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20260115/GroveEthereum_20260115.sol
https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20260115/GroveBase_20260115.sol

V2 source MessageSent has an empty nonce. Circle's transaction-scoped Iris
response binds it to the nonce authenticated by destination MessageReceived.
Canonical source funding/burn/message, Iris bindings and destination mint/logs
are retained in grove_cctp_v2_proofs.json. Tests verify all fields and amounts.
These 77 exact historical messages carry existing basis, not new debt. Future
or unrelated receipts remain unresolved; no runtime amount/time matcher or
network dependency is introduced by this historical adapter.
"""
from dataclasses import replace
from decimal import Decimal as D

HOLDERS = {
    'ethereum': '0x491edfb0b8b608044e227225c715981a30f3a44e',
    'base': '0x9b746dbc5269e1df6e4193bcb441c0fbbf1cecee',
}
TOKENS = {
    'ethereum': '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
    'base': '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',
}
SUFFIX = ':grove-cctpv2'
# Source chain/transaction/block, destination chain/transaction/block, nonce, USDC.
ROUTES = (
    ('ethereum', '0xcf7be3dfe094957c86ee80a21a24c5b33f0cc2bb7c2811f16bf516070b5596be', 24270651,
     'base', '0xf64f09eea0a675e402db86c4f4a26b5b6834ab0062c1d59bd94997caad194bf1', 41030281,
     '0x9269967179ac24cabfce0c5b69b0563c311d9c394a71612f41d0c6707e182dae', D('1000')),
    ('ethereum', '0x3d53d7b796cc492341604a531bf55c447c46dd728d6be6e2cada5abdc423ce85', 24271015,
     'base', '0x597341f5272c898d03e56b5c89547c1731aa3a14245d7f2e7c2e5a54f50ca558', 41031961,
     '0x48a2052b46c6cbfae3dc549e5db6bc2dd9c0510b03ec8f5032ce8e50c561794c', D('1000000')),
    ('ethereum', '0x5c1628698549331e397fb1e7a771e5c240b58cb928ea2a2bb764545e68e6bde8', 24584813,
     'base', '0x919790807811b5bdfe20264e8d6341fe256b5941a8250de2aa2a5a4ca62b9050', 42923727,
     '0x015a23064ebf245dea53aaa1fe2fb8723464d04d03774d1fabca252d16df39d0', D('10000000')),
    ('ethereum', '0x72f448f7bb51686d0299b00675e580a125b85b23007dbdd6b1b4b221be2d7203', 24714054,
     'base', '0x52f1ca39d1bdc9ab976cf21c067741e2a0b9ae6cf06a1b300cc95acc8e996f66', 43703043,
     '0xfc930359fd4e3268149445b7800002e702426e9277c53a04d54559da132af16d', D('10000000')),
    ('ethereum', '0xff653f334a5e824b00c7d397d4fcd1ade43baa44fb71348c608a74c3a1a98ecc', 24749272,
     'base', '0xe0bbcd436f641757a933b471b7bb405cf176c5eb140d20b3489140a0d888b284', 43915403,
     '0x4670d34bdae1b1c38d12d6c54eed42d73d32fa91a506c79342d4938420ae0cfe', D('10000000')),
    ('ethereum', '0x9ad228f51207af67c8432e82969db785cc1d27d205f7d098365deec4464b7f1a', 24758821,
     'base', '0x348945df87d351e7ce5068576b0d6c4a2eda24cc679310da89c4af45b5e56a49', 43972804,
     '0x4e12805dc3be175350292222f5e434dbecafb30831fc0ca8eb8d636a4847d5d8', D('5000000')),
    ('ethereum', '0x6621a3b6e64a77ea13d3eb0553216d70f2c3324371a139895d8eda587e6f3f80', 24850874,
     'base', '0xf19de5f6ee19848d26446094c78c5609d84b38b8b5de2f028a905480a979985d', 44527110,
     '0xb6a2db33f93de5703584ac1b1a3f979473c9ef324ee55cb784cb70140dcfeac0', D('3994379.874274')),
    ('ethereum', '0x470bf23cf0415a8be134dd5586e6807756f558a8468c1a57e25a9b8533375508', 24851538,
     'base', '0x573e5a0c654bf147182324e6fe8b9ba587499ca6d888414f22217b020ec29b0e', 44531142,
     '0x04aef421364264a4ddba6951db6fe5ad07bf4db2e3c3ef8110b5e6ae8a8e6bf3', D('1000000')),
    ('ethereum', '0x9d91ee1756f72951d4ba9681ad3ad9c6528e7b78fe3fc6d90c7dbc0ab3513072', 24886065,
     'base', '0xfeb2bdcaed207efda4a8ee6109cd9e0dd518f65e7f37e748ad61a51dc115ef07', 44738885,
     '0x9d8dae7e37021383d16aa40ce0cb3ed451c02377180dced4c0ff30075096d92d', D('5000000')),
    ('ethereum', '0x5b1dac1818a094073a418fe3f6ccd49116b4b20f03d4c0d4f3d9a210d887072e', 24887411,
     'base', '0xdb9f83d20df3043d443639ecb7047e77820bbaaed8b9029630dbc60ddc309bb2', 44747144,
     '0xab35b761445031aa36f135eb5cd050c0155c8697d04b5e1538f19aec9ce143c4', D('5000000')),
    ('ethereum', '0x3bca8a01d0fde9eb272827726f34be9294abfc27b8a30553580fadc0dfb67425', 24888073,
     'base', '0xd8472720c6acb0d33a8b2a2effe51283d0b471c95f357550d52269f82c278371', 44750989,
     '0x057c133bb204510ec7749419a5efa5cd2694bb9c732b866cadb45fa65a715027', D('8700000')),
    ('ethereum', '0x13cd1d9333e9dbd7eddb69909e02dd8cb6323872a15c474a62a2798e7cc5e048', 24900297,
     'base', '0x065d7769eef4e6d50c98333bb5375ad378b2a0b33018ebedaf589278a9996f13', 44824712,
     '0x74521899b61770393eb253242f5f461f49a3eaa5f08be0bfa0ab2c3f74264243', D('8000000')),
    ('ethereum', '0xbcf04406f38adb5bfbc8118e5725f68ab5ce55208944c684ae1cb6a92be1bf9a', 24914097,
     'base', '0xac8db448cd3fb4d9d4e0ed5e45df773a68a368522e97cff9ca7a2cad7f795036', 44907662,
     '0x1a297608596065781dd019184dbb58461c2401cb5d58276f3003f4338fbc0eaf', D('10000000')),
    ('ethereum', '0x4ea44e025d222b6e843aef6d29b541848f4447a8d5f12979c294de560180b43f', 24914776,
     'base', '0x0555158999768dfe34d60c344b9d23d22db02429a5a42596894cade61d01e044', 44911680,
     '0x51e155f5fe1760ba95781d78fbfad719a93559aae490ff80be5752b70caf3c40', D('5000000')),
    ('ethereum', '0x4fa9c39d819623300a88766283a2cfc0ca63822d52dd0ffb4c21b1ee3d29a9a6', 24928582,
     'base', '0xbf179aa25d86a193b8cc0d9c6c564e223f0d9bd9ab1cd554bb1af1be377174c4', 44994835,
     '0x55596280c4402d66c36781c7de67be52e83d7c0cd838c62e53152fce59237120', D('5000000')),
    ('ethereum', '0xb860d4e6f0bf0b4ddc074f29c67901ae8688fc2b35ab475617b6cd64bb64504b', 25073909,
     'base', '0x87058922d6fb29906432cbc9879a86876f5f7a84ee2d1457bf2f5379476616a0', 45869390,
     '0x7bb3fa300fc2dced942e054ff9cd176351ba15d329a16b2847ccb95433221803', D('1000000')),
    ('ethereum', '0x2e6bdc8d83f18aa9ea5a770af0b310415793223000ebd9537e6f900a8a413af5', 25089890,
     'base', '0x38d95f169de73745989e7ca445098aeea8d293bb775959348f6ae6021c590ab3', 45965774,
     '0x72f9a87d7c5ac8cda0f562a81907bfa0d36fae96533be3640b3756b67db1231e', D('1000000')),
    ('ethereum', '0x0e460b7d50a339e0d3be12d7435898c3315710c4cfd14b5bdb48ad99c2defb20', 25117619,
     'base', '0xcbdc38dc77977a0f07b2778197c8e1c5706faf192ef199960aca7a28b1fb29ab', 46132617,
     '0x6371af5bbaa79f9508cee48dfe18da40b7155bea34372d6e68183b4f3b163eab', D('2000000')),
    ('ethereum', '0x53bb1be5d47512201ab92070a85ae911238daf6312a561e7eed35731bae216b0', 25131861,
     'base', '0x6fafeb59f22abdb7210195c8b1e2ae874b45e427fed02770dad606ff74f682da', 46218245,
     '0x6676a163bb9071e6c4b78c8b9cc6bb55eb4d0a24d6ebe18cc590fa8b3e469150', D('2000000')),
    ('ethereum', '0xb4b0ebdcab021378eb12ebdb8519cf9aee2fbdb477736d520c01747fb6773404', 25132998,
     'base', '0x325c9252f28db23316b88361a3ea6317e29289c220c803abbc4b2b14a2c3f831', 46225161,
     '0x2103edd38e14e63f39f8bd3f5b88856801e341cffd6be75eb0e5dfbeb738aae9', D('4000000')),
    ('ethereum', '0x64a6370998ed73f6e6b30b1c76a88b913ca7f2839e2fb6c670e66fb52c1d8f42', 25166225,
     'base', '0xa17761188c8c19b8ef71ca3d3fdd93ab82fe9e4beb2557095644892d1e82ce12', 46425223,
     '0x0cc2d7320caf7b238b682f7873efdbc11bd7ee24685f8e5f1d5b4fd8eecffa69', D('2000000')),
    ('ethereum', '0xffd8cd7b01fa51b872f106b79470e9e6e9d1345e2f868263fc06b7745fe164cf', 25173133,
     'base', '0xf864a41512636b5b30a34999ff43e2d9631dd3b22846180dd2ad56ae4a1d7911', 46466692,
     '0x7234723109a349977bd6a250518fdc205517f9a11985f8b0115bfbd465a9d274', D('2000000')),
    ('ethereum', '0xe5da90db2ad2a317ebdda831868c64312a2e56616e15d018fdc3914e35ebba32', 25187134,
     'base', '0x5aa991fdae1e1ddb1cdc74a2318f8d18950821471cf359770b20d2c8eedbb900', 46550977,
     '0x5c219da67d6fc58db12a472dda477d012edb4919795794a370ddd164e9f5ac72', D('2000000')),
    ('ethereum', '0x1268578a03b396f4f3c5fb7fa6a80fb00740b133162e90b58d86a634111a9164', 25190661,
     'base', '0x54aa4a3e3f33166e38ac38b987eb9ce97ff459b611aa0f32936f6012a2c61d40', 46572303,
     '0xd8d007c929feb9faae77a799045e3b79853d112d111625e8935de7b7cc7ae69e', D('4000000')),
    ('ethereum', '0x378ef44a789d561d0cce4503271afa424ad00dbdcc7df56b8f002ee1f78f7842', 25195566,
     'base', '0x3d22da9d46137332e3a0b30f3ae9305430938ec8d96a3ec92bd3824e08e62ce4', 46601855,
     '0xab60efa0f6b6dfc0e3017bc78b756adca83479e6b463eb5b59439b4635a34dd2', D('3000000')),
    ('ethereum', '0x59832496b4331d3bab577af9f43b6041ae446e1d4a57992daf686cf78b602ab9', 25201603,
     'base', '0x962f9450ca6108f6dbe849d9ccb96ad6cdab134121f0bacd86f7c348d6be2d1f', 46638152,
     '0x878d8a74b325b74325ddae8fcd7b8f951b2a89155c9b8280c3f4cad09ed9ecb1', D('3000000')),
    ('ethereum', '0xed049cf65f322d81bf090fa6bc61f30dc3ff1274d0010441d10bedac474200ab', 25208903,
     'base', '0x5b46d6380874b24661be4fdc50cb4133a1ed64381b3f8f8429a2f307c097362c', 46690089,
     '0x8648ee8b4bcd8cef9480687d7560cfefd42ad1541bb629790ece92188120adbc', D('3000000')),
    ('ethereum', '0x535d7e8e1f0a87ebc8073a1bb918090a9277df0a9fc5d84c9215d6fb0e794057', 25217451,
     'base', '0x23b5b52e1b4631e8ba4bcea53847ea4caf79e55b12829c729407cf156e04ce4d', 46734444,
     '0x71bf69e64f74b96b1e8f8a6aef80a3656f9ad483c78f2f99672de711fd362dcb', D('3000000')),
    ('ethereum', '0xc3eb445eb9a84945a73639f148214810e702168babb7fe4a563f784d4ac09224', 25222451,
     'base', '0xb287bbcc4fc19ecd7536e1d000926bc3f9f970d038dd63b416b865ecb84a602d', 46767047,
     '0xd0002620757bbd81c39cddbd5fe143e474b647bde368369216260ff21ecefa07', D('3000000')),
    ('ethereum', '0xa1a85a84aa47afc5f26175e371a158d51c831518fe819d18349d2abf318162b7', 25302120,
     'base', '0x1abba2ac490d70011c7229f4b5f55acbb3d3e6d103e1066d72d3cbbe68aea4a4', 47243906,
     '0xa8f0c4cff12f35c0e39edecf8aa78ad53d1bc082a23df4ee81a7dc85a361f080', D('3000000')),
    ('ethereum', '0xa7f029128c8493ba0f6691797fe222c39a3ee612ef10da15ee877e60424f004e', 25309170,
     'base', '0xb18bcfac942d3cc91b6688a8fe5fdafcf066b1bdfc85c6af286c90de4d8bd08b', 47286337,
     '0x1b07a15c12c117e3e0f53efedcfe0592338deb9b2bc1315899ca7e7cc2608481', D('3000000')),
    ('ethereum', '0x45477b85aa25d26ce7c38402c91db0c1e6be95ff4ffe2813a59bd2c1352707fc', 25309882,
     'base', '0xf212e51644f92f5bf948be557eeec4d2920d4350e3e534e27b269a30edaf3e05', 47290574,
     '0xaaedfdc9423317600e7908c593a52a7f8d9319475b11c70a7f05de5a3195b47c', D('5000000')),
    ('ethereum', '0xd61420d6729a6f2074f0bc8ee6b53b6aced3844e1171e14644a929f2e740ca49', 25310629,
     'base', '0x5c2980e574678fe6f09cf8f747efcd9c8bf7c71842af7fc47e82cd82fa8b10bb', 47295166,
     '0x2b0e94375cef04a70023783f6f49cac0b44023d00149b54dc04306425de51e33', D('7000000')),
    ('ethereum', '0xc8b3bb768733c31cbaeff2a0914d9addb14cf59987bf26d1a2434983b2e3281f', 25311304,
     'base', '0x11a7dde9084adfb3b933ae77786176a77958dd973c054a0410ab21985a7e7aa8', 47299205,
     '0x4ad8979a820f79d2e999d1ed55992f01bdd75afebb32eb4b14632214cc6d3a8a', D('3000000')),
    ('ethereum', '0xe6b52ef46e43f536c66507dc74567435f21f811b555d6103c8834d2e9cf0a8d8', 25317184,
     'base', '0x1a495634dcdd1b2d521253cc71d233d68444eccb1d00c8a2bfd1009996634bbe', 47334723,
     '0x762a600dac9c5577aab6c3db2ffcaf825ff5d6abbbe7f03b433a61856ab27ae0', D('5000000')),
    ('ethereum', '0x289afecc875478bad955201de24e6ba5a06f57fdbc955dc33a120de1c9f580b5', 25318587,
     'base', '0x6155db3a1efd6f0bd3089b7323aa7357efa1e03f97001642fb81f5a982ace9f6', 47343178,
     '0xde0d8153ab889863698021dba4ecbe739b879cc59fcc248acf2975c43464a13e', D('8000000')),
    ('ethereum', '0x6c58ddfc6d6fb6e855b179ded438c5331920bbbee124fcbb9de27380f5835afe', 25323396,
     'base', '0x54e1ed122d99be4dad233278c37e477ac395faa8a0e543ce5b49c510ac1335d3', 47371972,
     '0x97a65078f1b25735833072182a6634da39b324023a8f701c1ff57c673e245e40', D('10000000')),
    ('ethereum', '0x1be1f3ab52a3c7b06e1c5663df0dba1b0ba7fac1f3dab40d0f141c1606eb492d', 25323757,
     'base', '0x0b603a454636119c5bbdad4a0a3ee4febd166ac8c50837d1224135c8686ae7d0', 47374270,
     '0xd8008a1f0035ea4410dd3003d872bde59435c244cd97b2ed1d56c9a77683b8ce', D('10000000')),
    ('ethereum', '0x22aef9110d0ce49f505e8ea1e10f0d628bb56c3251f6ef9723e9b9e5708666a1', 25324364,
     'base', '0x9e3d9fcce1905e65f65959549dbc780f879949cfc0080dd23c82aa9c6b54e025', 47377981,
     '0xb79956c87d49b46bc09399b0df2119a4895dd15b11214efd8c607d77daf4e5d5', D('3000000')),
    ('ethereum', '0xec7ad211a649d09fff50242bafae22803856f854fe3c0892711c9b1604c00f0f', 25325773,
     'base', '0x1adaa972bcec1a0f9e4486be20b03f9eff9e79c135e241ff542b5d5600e4baf3', 47386381,
     '0x1c46c3cda7965ef6769c351987b7012c62a04c00c4dc31cc7e8ea801bd2d1265', D('5000000')),
    ('ethereum', '0x27f34ee0092286f5c87d4e7bd188f7028274ef6bf38e4cd6e9886da542645eae', 25326571,
     'base', '0xd92ca81fa8dbbf6205c97bd98d8be32137d7b6bc9b4030a3cfcf04693164da4d', 47391166,
     '0xf569aba654ac1eaaf7dfdd3c25b6e242c2beaeef79ecec6f11409e6ac10d192d', D('5000000')),
    ('ethereum', '0x0d4e10b8f0d567ff6d13327a26a858b59b13a82fdf3415bb89c81927be4e9c99', 25329918,
     'base', '0xa3985729ff42c8410dfd3bacd18807fa21f99af822e10d74982d2df10656d05d', 47411396,
     '0xb3bdae3b91e0b6432a3f7d7e9f7eef65d690d594f3ca71a5451a501ba039c26a', D('8000000')),
    ('ethereum', '0x4d3c436023bbf3be00947e1608785e12b2606364b9a5a6b361da55713c7fb099', 25497063,
     'base', '0xd283140b3405031f4189fa897d9c9065cde5d79588dec3df55ae6ba1d9912663', 48423335,
     '0xb713b84044d7fe09cc6fcd120ca6be4e7784b6554f7c338f83ec6f540b5ee5ab', D('3000000')),
    ('ethereum', '0x4a18ce1f83a3f2b65ecd789226b0d7c4bcc130ebfb7eb9aaf433aead7363554c', 25525165,
     'base', '0xa282f5b289ac04d9c029df60ea03f0d76bf9d142ae10956f967f74b193a8ab09', 48590542,
     '0x232e39d53d2f7267b756461ee567a45172f37d11e8098b15e31be5c9d8f95e74', D('3000000')),
    ('ethereum', '0x97dec4bcd09ea4c8e16dc59471e0ac4000ab3a1c5dcfc9f1a0651f88d5a75f20', 25526271,
     'base', '0x8782e381dfdd5da5fa020c29bbe868bdde9bed009b34733cd125472fea6302b4', 48594384,
     '0x38021fca6642cdd40620f4a9347bbb207fc99eace97ffb1f4cfeacd8b39893cd', D('3500000')),
    ('ethereum', '0x04325196f277a72703973698116b46453e8b0ed940013442385723545352df18', 25533811,
     'base', '0x6460b98bdc1907157b6d2805096c0764b5bba87174a6897f0a75509e880e977c', 48639575,
     '0x1a5afd52b44a1d5dd4bd09eacf26da27844f78611cc80ec9d747116b7071d65e', D('7000000')),
    ('ethereum', '0x54240fd54df8295444a492ed59200f277d936f95936969716bbb89955ab64f19', 25538641,
     'base', '0xd5bde5f551fc257f0b2157b5d015abad49ad1de2a6f8d8913a3d84409dc65d52', 48668554,
     '0x8017d0d6ffda2171a5b4f07f37b13053bbc1836fa5a4cd989bf85cac8ff403c8', D('6000000')),
    ('ethereum', '0xc638e76bc8ffd91f1d8afc426185783b0c88ebd42b169b48e6529fc88b6cd467', 25581189,
     'base', '0xb150e0a2c4045c4aea4ca3977ef76e7e4d1ab50839ffd3b62f21b9152a73d09b', 48924862,
     '0x37fbc5f0a8484de21a79bc61c535fdd3c76abe9d10a9e5e350b3e5274c0603dc', D('7500000')),
    ('ethereum', '0xaddc61b6bc61b9e5b36bcdbd6ba83bd5ba00b1531ecfc3e99a8a92d2fc55a158', 25582035,
     'base', '0x375a868a56bdba6f2505fc18387350fcedf39793a6a8502f3b350da945ea0fa6', 48929863,
     '0x39cef2d2a6e0af0d28e26744242a3d2375ebf72727d8ac676fced024a5938da3', D('4000000')),
    ('ethereum', '0x2bf361dbae347dd6da20b5172dab04c843e65efbe1e73c936cd080de0321ce83', 25595647,
     'base', '0x0166d92a7f2073839d63334280b1845ad1a7b3ab3fe4213eb7bb8aeba6f56f2a', 49012030,
     '0x3beb0e51fd1447564081385c336d0a8086630c7c23490fd4a786b32c9e2d9a57', D('8000000')),
    ('ethereum', '0xf0f08e9e7ae90f72c96cd52bd8141cd5444ca8650dbb1857a77ef71e4ea57a9d', 25611411,
     'base', '0x9dbcf16183d0958c4fb17cbdbb02a5bec26e29d33bedf571fce7d5ff28f56e46', 49106886,
     '0xe97fd3a030f3ce48d5e68f2553248a6a85d80ff976e59128d505d8d0df934005', D('4000000')),
    ('ethereum', '0xc49e162b2e769bed65ae37cefe367c911dad1a6da2fdacf5d5c775a6fd33ac2c', 25617765,
     'base', '0x1a018081264c2d04c335468459704daf81d7fcfb24fb235e09f295ac907d889f', 49145091,
     '0xae45874b118f8ef8e492fed3bfb24ee0c90e7604b270bf8f2729dbf3089f16eb', D('8000000')),
    ('ethereum', '0x15dd9bdabec38b138d0ebf8fc2a9e8dd2fa8dbaffd55dd2a50b6efbb4eaa1cdc', 25632592,
     'base', '0x374502c33aee6402051c036491d6fa0dd98b4a80db29cf2c127430e05598bb71', 49234377,
     '0xccaa16597c97036ba0082606feb40054c14cf8f53ab1c620b345f3a2f81585cf', D('9000000')),
    ('ethereum', '0x976107f8ae8b715932faaf6b864058cd0f8c5fb3909805c7f5257ab09c41aa05', 25632934,
     'base', '0xfca878920c2a016364fc0dab086df184f91c887d32d8e7c37c104e68abef8041', 49236537,
     '0x9d0c145b21fac8b1ea79311641a8b58416c7a3f6d8d7ed0bf46b25d00b040317', D('10000000')),
    ('ethereum', '0x8d35d1618ce8125eca0b1c60321962953dd9bb7d8900063da96b8daccaa618f6', 25638750,
     'base', '0xfcf9dcd1ecd311c1555274f6c91434a1fae97427a97d330669117599e335378f', 49271426,
     '0x6c943b2d15c96b3f2f00819d30cf2a537a6dd5eb6298b97d092bf4a1e0bf28b0', D('10000000')),
    ('ethereum', '0x8d35d1618ce8125eca0b1c60321962953dd9bb7d8900063da96b8daccaa618f6', 25638750,
     'base', '0x8a353d6ca7a1aee6df26e8dc8391b979f5bf517d35feda6af962f2717d1c4ce2', 49271437,
     '0x386d5a2a4f3c73eb963765a8193c27caf7fd649d10733b03388c2a18e7eadf1f', D('5000000')),
    ('ethereum', '0x1f18595bbd13d2cbdbbca250f965b43ef7d7d0ec1ea27b2c173484d9bce3ee93', 25639773,
     'base', '0x06bb4e0ba27d6b5c4f7d2436e58d254038711984655fddc56f0500ce7cb4a5f2', 49277576,
     '0x23838c1df6ed3a809e9d6ba14f53af9ef7a582c0c26cf7661b7717886f8e7bf4', D('4600000')),
    ('ethereum', '0x2b85506fa626a616be2715773d7312c878882f5695d2888b08d613e36bdae9db', 25640175,
     'base', '0xa8885f66e481b1637f7f863870b82f8c4ea1ddf1cd40db575ccc24d6a4efc6ef', 49280068,
     '0x2daf8df23d62d51389fadca222d6b7f22d2a3379f2623f34347329f09b1783c4', D('6300000')),
    ('ethereum', '0x6fef0eaa3ddc4bcc723c480e1d0fd3dc298198c4b9efe53bf0aaa0d13b8ec988', 25688778,
     'base', '0x74acc295e406b1317cb5b8771fe243b943fe8edee3fd3e1225e13ef0ccedeef7', 49572487,
     '0xed402e17498a8b8edbe57844632ebaf9b4580b51691c0c696def535f9c650358', D('7000000')),
    ('ethereum', '0x909c94f5d32d441dfbad2dca13869506e543edc14903d416c09b08ebce664adf', 25689112,
     'base', '0x8467ee34e3f6586c5dc486e5e3032602663f6df428f4abb1a4288d715e59b056', 49574593,
     '0xb19d8bf03fe3c0fbcaa196973c85dc61fd23768ef59365b49140bd4023c1040f', D('3000000')),
    ('ethereum', '0xd97b9ea1f5dc3ed791c784350460cd27b6ff0335d7d8f217507bb1b2f6703643', 25695567,
     'base', '0x93d96faba90fa6b7dd22037c38f2fc4134b2dc953fa16d3db53ce7855e91e6a6', 49613389,
     '0x7db974ed8e28828983a914b5c10bcec215dba3c5e5d44049df0f193c8a3c1c82', D('3000000')),
    ('ethereum', '0x598b886b037c036fe7259c7387b945e03419cadfa857beb444512d057115887e', 25703747,
     'base', '0x068db433714e56234c94ab0d335e985cabaff6b7acb504539de3562f84b5b88e', 49662534,
     '0xa36103a9a13de104199480ed8dfe8ee7830669b10d67ff3665d548a01a1517c8', D('5000000')),
    ('base', '0x7a638ef42fb94e897dc56e1ca5d4ff73cad01c99bf53734cc70266f17cb16f9f', 41031346,
     'ethereum', '0xb7992bab5ac13b3d2dda92b78ed68ad97db6096d124a907ccf048018d8dc2c02', 24271114,
     '0x7d48cf5b69e01f302c4d2d629af8dc9217abc0f73aa4e64672cb18f2c4b1fb7b', D('1000')),
    ('base', '0x3d6bd9d1901198728fcf4c679ae3582783ceeb40cf0d78a21da69e28aa437e8b', 42979359,
     'ethereum', '0x235d2ea3d41281b93e3c1d9a214a27dfef8d57c401d5f458233ed592b47c2761', 24594214,
     '0xcc40dc752ed61ae760e7d3073ee2c88743418d2d7c0234a0ad4b90d0e4beb2eb', D('9000000')),
    ('base', '0xc9e7e51499841a1ddd659c806808cf6ead4290e58629a2573a4c08f5d413a142', 43887119,
     'ethereum', '0x2b4ba72288447f0c7e14b5ca2892977efcdeebc31b5f30f9046e2399b8c0262f', 24744937,
     '0xd80515a813e0ee6ceb71fe283d7f32c819f91315235fd060356bac1bdeb105a4', D('1000000')),
    ('base', '0xb042a88d31208b28c06a3156d5c2273845f5907f8b6c945aafe9e09e3c6cb46e', 43887543,
     'ethereum', '0x03099296d49a58bf2d428e69c9c9be74464ff104cab660d6dc29b601297947d0', 24744936,
     '0xf26866e23cccb2d306fa7cd63a4afcc8fbd36fd639d898c0174cb5ae45fb8e56', D('1000000')),
    ('base', '0xc9ceb503098efa2fc74bf934bb774902874d16066576ee2e6e523b56c43ecd10', 43887584,
     'ethereum', '0x15006de390290ba2731db04a124cbdf690e41d7906c328d37b252f0f7b18be88', 24744935,
     '0x73e3ef1e2372f8584b230dab7b6d95a28449b74065a14e658aa0d846f1019454', D('1000000')),
    ('base', '0x65c143e970f027d1d2a5473f2d8ec95929a543ef0deda9850c22bee588b6cf56', 43887623,
     'ethereum', '0x262f5626688c2bad01650b22ff32ac2ad1437d6bb00fbf42f4fc407de693e942', 24744934,
     '0x33abb91b4da0eb040d41a034b5294b16faa3c505f05b9b9af7e6e8ee19b42053', D('1000000')),
    ('base', '0x7e9012e7f6c09d4c20962f8fa060273d31808a78082d37c38543ff24522ed949', 43887678,
     'ethereum', '0x24098ad94a48920ef36299157b9616372aa3544cd89de3e1ebc98b29caefc2cd', 24744933,
     '0xd5d0eb6762c1600f27d6b62ca7eccc4527a7482ff0740a0cb91f76da59c1493e', D('1000000')),
    ('base', '0x37885c272894b8ecb5b03087ac2c8eb40eb442c046887e9055dfbcaa7971e9df', 43887724,
     'ethereum', '0xdb4700bb904bdc751df78a1ca85712b68e896967e14e0aff713fa0bb4929d63f', 24744931,
     '0x214745dcfdfa84d8cd327b6e5b978ec4ca1896d7c84c0505b4118a607e52c254', D('1000000')),
    ('base', '0xe72f4b3018bc7483aa1ed5c981723cfbb8aa29b3521d6a01d65649d525837640', 43887763,
     'ethereum', '0xef823e1f55dc441988f424f69f49e9d09a53fe87aa37b6a60f0ba61badcf2449', 24744971,
     '0x4102a2b55f14010b8a5cb15fb6c53b7997f51869dfcaa859b2dda6110778df57', D('1000000')),
    ('base', '0xafd82112cd18ff4f84b5c9b234e71565a41f1cb4e3f39709370cba2727c22721', 43887799,
     'ethereum', '0xbf90fbb0b304bdd2badbbd83783db90f67fc5a385b8b01355d714aa535882438', 24744970,
     '0x251e47d6b7995e41fd7649058e698d2f514d56a5434dfe22873e1ae2a8ee59b8', D('1000000')),
    ('base', '0xb884c381c627746f1da62df82c34596082c4b7bce954e3ab91de6dd03acf006e', 43887836,
     'ethereum', '0x5306594b8b05d9caa379f2a71fc964c5a055b090420ec433922a01e03eee71c2', 24744968,
     '0x399fb63cc629901bf9792cfd551fdb44e382e4e14ee699b6270be7195311c6b4', D('1000000')),
    ('base', '0x486cb39b5a39524c30c85fca0e98cf0de9368a4824b89e593b5e640ef9ee7167', 43887909,
     'ethereum', '0x0dff774baf1f6a508c1a69703069daabdbf0da5b55f0cf0ad0ea8976198139e2', 24744966,
     '0x3042e9186e74c7398c58a8d3defd2531ca5d7ba3651d6965e53ae03bbd7be15c', D('1000000')),
    ('base', '0xf61510525d49d2afc84bafbba86d6ad11f2d1520b6d3337daccfd5b2c4c5c0ef', 43888087,
     'ethereum', '0xd8efb74b8fa89484c32ae117cfac24d81eb7b304c8ca9c1be23a8d5ab101a2c7', 24744963,
     '0x25d7d2f4c6555f053763a188ba16301b8dac0e8705d1ad34b7b537119fd9e3c1', D('7407')),
    ('base', '0x9616f84a18dd11e0ab3af403f5f2feb26f4a6bc70af286cd8831d6f2d36712df', 49681960,
     'ethereum', '0x09b9d9b4c219c646004427629518e09c13f2ba80ddb0b22d0caf6c4c3aedbb63', 25707147,
     '0x00f5c75b71e5761926b744a990970a29f86bdcb857b485f36f5c89442e83d6b8', D('5000000')),
    ('base', '0x4b6d320157f893ffc0397abe10c796e1e2f8d32abbfbf72d30bd490080a2169c', 50575209,
     'ethereum', '0x605c2d2a4baac9b4e19fd7756533d99b99aa196b9a59032741482d87278d9b28', 25856154,
     '0x9f40f5ad73816945d1bdcd3e320923f26b0fdc759dfd491d14dac0e5bd5def7c', D('10000000')),
)


def cash_account(chain):
    return f'{chain}:{HOLDERS[chain]}:{TOKENS[chain]}'


def link_grove_cctp_v2(history):
    from ..normalize.allocation_capital import AssetMovement

    if cash_account('ethereum') not in history.venue_accounts.values():
        return history
    index = {b.identity: b for b in history.batches}
    if len(index) != len(history.batches):
        raise ValueError('Duplicate capital transaction')
    custody = {v: list(a) for v, a in history.custody_accounts.items()}
    # A transaction may send multiple authenticated messages. Accumulate all
    # changes under its original key, then rename once after the whole pass.
    touched = set()
    seen = set()
    for source, sent, source_block, dest, received, dest_block, nonce, amount in ROUTES:
        send_id, receive_id = f'{source}:{sent}', f'{dest}:{received}'
        if nonce in seen:
            raise ValueError('Duplicate Grove CCTP v2 nonce')
        seen.add(nonce)
        if send_id + SUFFIX in index or receive_id + SUFFIX in index:
            if send_id in index or receive_id in index:
                raise ValueError('Cannot append raw events to linked Grove CCTP v2 route')
            continue
        s, r = index.get(send_id), index.get(receive_id)
        if s is None:
            if r is not None:
                raise ValueError('Grove CCTP v2 receipt lacks source funding')
            continue
        available = s.minted - sum((m.change - m.external_income for m in s.movements), D(0))
        if (s.chain != source or s.block != source_block or available < amount - D('.01')):
            raise ValueError('Grove CCTP v2 source funding mismatch')
        claim = f'cctpv2:{source}:{nonce}'
        if any(m.account == claim for m in s.movements):
            raise ValueError('Grove CCTP v2 route already linked')
        index[send_id] = replace(s, movements=(*s.movements,
            AssetMovement(claim, D(0), amount, preserve_basis=True)))
        touched.add(send_id)
        venue = next((v for v, a in history.venue_accounts.items() if a == cash_account(source)), None)
        if venue is None:
            raise ValueError('Grove CCTP v2 source cash venue missing')
        custody.setdefault(venue, []).append(claim)
        if r is None:
            continue  # Pinned before receipt: keep the existing basis in transit.
        cash = [m for m in r.movements if m.account == cash_account(dest)]
        if (r.chain != dest or r.block != dest_block or r.timestamp < s.timestamp
                or r.minted or len(r.movements) != 1 or len(cash) != 1
                or cash[0].change != amount or cash[0].external_income):
            raise ValueError('Grove CCTP v2 destination mint mismatch')
        index[receive_id] = replace(r, movements=(*r.movements,
            AssetMovement(claim, amount, -amount, preserve_basis=True)))
        touched.add(receive_id)
    return replace(history, batches=tuple(
        replace(b, identity=b.identity + SUFFIX) if key in touched else b
        for key, b in index.items()), custody_accounts=custody)
