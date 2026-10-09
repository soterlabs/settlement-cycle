"""June 22 partial native withdrawals: two returned, two still pending.

Complete message/portal/cash proof: spark_june_op_uni_withdrawals.json.
At Ethereum block 25878704 both 10k USDS withdrawals are unfinalized;
the sUSDS withdrawals were finalized July 2. No amount/date matching.
"""

from decimal import Decimal as D

JUNE_ROUTES = (
    (
        "unichain",
        "0x345e368fccd62266b3f5f37c9a131fd1c39f5869",
        "0x6ba2c8158c8b1bc1e9daeafc5903f0d46978ccbb37d92eb80fea931582236625",
        51388983,
        1782137342,
        (
            (
                "0x7e10036acc4b56d4dfca3b77810356ce52313f9c",
                "0xdc035d45d973e3ec169d2276ddab16f1e407384f",
                10000000000000000000000,
                None,
                D("10000.00"),
                None,
                None,
                None,
            ),
            (
                "0xa06b10db9f390990364a3984c04fadf1c13691b5",
                "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd",
                10000000000000000000000,
                "0xd476db3eb2c7e4e9fb08a1f9581e75bd265533be57166a238a44cef7df101c60",
                D("11006.22290923617434"),
                D("11016.94725981763585"),
                25445694,
                1783005755,
            ),
        ),
    ),
    (
        "optimism",
        "0x876664f0c9ff24d1aa355ce9f1680ae1a5bf36fb",
        "0x8f10a77f52f7c583a8606120736ddd5766f6cb24027808d8f0238d856ca21d77",
        153269287,
        1782137351,
        (
            (
                "0x4f13a96ec5c4cf34e442b46bbd98a0791f20edc3",
                "0xdc035d45d973e3ec169d2276ddab16f1e407384f",
                10000000000000000000000,
                None,
                D("10000.00"),
                None,
                None,
                None,
            ),
            (
                "0xb5b2dc7fd34c249f4be7fb1fcea07950784229e0",
                "0xa3931d71877c0e7a3148cb7eb4463524fec27fbd",
                10000000000000000000000,
                "0x9419f1d7242dfb1eb23fce5c4e1180c0de5dcad27580047edd8441658c1ac0ef",
                D("11006.2230573559123"),
                D("11016.94800113797736"),
                25445699,
                1783005815,
            ),
        ),
    ),
)
