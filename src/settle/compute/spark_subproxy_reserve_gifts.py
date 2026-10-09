"""Recognize earned reserve balances forwarded through Spark's SubProxy.

March 12 spell, section 4 (executed March 14):
https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20260312/SparkEthereum_20260312.sol#L111
The complete scaled-balance history proves the reserve-treasury origin of the
large balances. The separate 1 USDS / 1.1 DAI seed mints remain unclassified.
This is tracing income only, never retrospective settlement recognition.
"""

from dataclasses import replace
from decimal import Decimal as D

TX = "ethereum:0xd157dbc535da15f78cfb94eacbfbfe20c0b728f9f561350484919dfe499d239d"
MARKER = ":subproxy-reserve-earnings"
PREFIX = "ethereum:0x1601843c5e9bc251a3272907010afa41fa18347e:"
# Token, pre-existing direct treasury gift, whole movement, additional earned part.
LEGS = (
    (
        "0x4dedf26112b3ec8ec46e7e31ea5e123490b05b8b",
        D("28028.56056513842457866781298"),
        D("612534.6113116682894760880842"),
        D("584504.9306710878410230186230845329022957881819772"),
    ),
    (
        "0xc02ab1a5eaa8d1b114ef786d9bde108cd4364359",
        D("12978.02767056296048381952472"),
        D("646078.3499939505008446992328"),
        D("633099.293108098237639493007806498156236256565264666"),
    ),
)


def recognize_spark_subproxy_reserves(history):
    if not any(PREFIX + token in history.venue_accounts.values() for token, *_ in LEGS):
        return history
    by_id = {b.identity: b for b in history.batches}
    if TX + MARKER in by_id:
        if TX in by_id:
            raise ValueError("Mixed SubProxy reserve transformation")
        return history
    batch = by_id.get(TX)
    if batch is None:
        return history
    if (batch.chain, batch.block, batch.timestamp) != (
        "ethereum",
        24656425,
        1773499751,
    ) or batch.minted:
        raise ValueError("SubProxy reserve execution metadata changed")
    movements = list(batch.movements)
    for token, direct, whole, earned in LEGS:
        matches = [i for i, m in enumerate(movements) if m.account == PREFIX + token]
        if len(matches) != 1:
            raise ValueError("Missing SubProxy reserve movement")
        index = matches[0]
        old = movements[index]
        if abs(old.change - whole) > D("1e-12") or abs(old.external_income - direct) > D("1e-12"):
            raise ValueError(
                "SubProxy reserve shape changed or direct treasury gifts not yet applied"
            )
        movements[index] = replace(old, external_income=old.external_income + earned)
    fixed = replace(batch, identity=TX + MARKER, movements=tuple(movements))
    return replace(
        history, batches=tuple(fixed if b.identity == TX else b for b in history.batches)
    )
