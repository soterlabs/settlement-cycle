"""Spark's two August 12 Binance OTC tests, stopping at the approved boundary.

June 18 spell sets the exchange, buffer, USDT/USDC whitelist and limits:
https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20260618/SparkEthereum_20260618.sol#L43
OTCSwapSent / OTCClaimed and actual token transfers identify the three legs.
The buffer returned 2,249.428190 USDT between two 2,000 USDT deposits. Release
at most the funded 2,000 claim; excess cash stays unclassified, not earned.
The second 2,000 remains at the boundary at the August pin. Independently
observed buffer balances are not extra borrowed capital or an idle exemption.
"""

from dataclasses import replace
from decimal import Decimal as D

from ..normalize.allocation_capital import AssetMovement

PREFIX = "ethereum:0x1601843c5e9bc251a3272907010afa41fa18347e:"
CASH = PREFIX + "0xdac17f958d2ee523a2206206994597c13d831ec7"
SAVER = "ethereum:0xe2e7a17dff93280dec073c995595155283e3c372"
VENUE = "S_BINANCE_OTC"
ACCOUNT = "eoa-allocation:ethereum:" + VENUE
MARKER = ":binance-otc"
SKY_MARKER = ":binance-independent-sky"
POLICY = "Binance OTC boundary: principal-first release capped at observed funding; excess returned cash remains unclassified."
RULES = (
    ("ethereum:0x2d4f499b6eadce1aab46192756e020b419ddecb56e7980672f7c0848d7763dcf", 25739145, 1786540439, D(0), D(2000)),
    ("ethereum:0x7e5bee5a406bd57995ea23849f961defca52051c168a6bc9e9052331687d1a93", 25739399, 1786543499, D(2000), D(-2000)),
    ("ethereum:0x1927ade1558dd5fbd33abe0e784d207648372593811bb7c469a88574ed46b6a2", 25739427, 1786543835, D(0), D(2000)),
)


def link_spark_binance(history):
    if not any(a.startswith(PREFIX) for a in history.venue_accounts.values()):
        return history
    by_id = {b.identity: b for b in history.batches}
    if len(by_id) != len(history.batches):
        raise ValueError("Duplicate Binance boundary batch")
    if not any(tx in by_id or tx + MARKER in by_id for tx, *_ in RULES):
        return history
    horizon = max((b.block for b in history.batches if b.chain == "ethereum"), default=0)
    required = [r for r in RULES if r[1] <= horizon]
    repaired = [tx + MARKER in by_id for tx, *_ in required]
    if any(repaired):
        if not all(repaired) or any(tx in by_id for tx, *_ in required):
            raise ValueError("Partially repaired Binance boundary")
        if RULES[0][0] + SKY_MARKER not in by_id or history.venue_accounts.get(VENUE) != ACCOUNT:
            raise ValueError("Missing Binance funding branch or venue")
        for tx, block, stamp, before, delta in required:
            b = by_id[tx + MARKER]
            claims = [m for m in b.movements if m.account == ACCOUNT]
            if ((b.chain, b.block, b.timestamp) != ("ethereum", block, stamp)
                    or len(claims) != 1 or claims[0] != AssetMovement(
                        ACCOUNT, before, delta, preserve_basis=True)):
                raise ValueError("Repaired Binance claim changed")
        return history
    changes = {}
    for tx, block, stamp, before, delta in required:
        b = by_id.get(tx)
        if b is None or (b.chain, b.block, b.timestamp) != ("ethereum", block, stamp):
            raise ValueError("Binance boundary metadata changed or historical leg missing")
        cash = [m for m in b.movements if m.account == CASH]
        if len(cash) != 1 or cash[0].value_before or cash[0].change or cash[0].external_income:
            raise ValueError("Binance boundary net cash differs from witnessed transaction")
        if any(m.account == ACCOUNT for m in b.movements):
            raise ValueError("Binance boundary claim already exists")
        claim = AssetMovement(ACCOUNT, before, delta, preserve_basis=True)
        assumption = (b.funding_assumption + " " if b.funding_assumption else "") + POLICY
        if tx == RULES[0][0]:
            # The only USDT ingress is the 2,000 saver Take and its only egress
            # is the OTC send. Sky's USDS funds only spUSDS/spDAI in this tx.
            # Do not commingle these independently witnessed token routes.
            if b.external_funding and not (
                len(b.external_funding) == 1
                and b.external_funding[0].kind == "draw"
                and b.external_funding[0].source == SAVER
                and b.external_funding[0].amount == D(2000)
            ):
                raise ValueError("Binance test's independent saver funding changed")
            allowed = {
                CASH, PREFIX + "0xdc035d45d973e3ec169d2276ddab16f1e407384f",
                PREFIX + "0x6b175474e89094c44da98b954eedeac495271d0f",
                PREFIX + "0xc02ab1a5eaa8d1b114ef786d9bde108cd4364359",
                PREFIX + "0x4dedf26112b3ec8ec46e7e31ea5e123490b05b8b",
            }
            if len(b.movements) != len(allowed) or {m.account for m in b.movements} != allowed or abs(
                b.minted - D("3556.808411972098839995")
            ) > D("1e-15"):
                raise ValueError("Binance test's independent Sky route changed")
            expected = {
                PREFIX + "0xc02ab1a5eaa8d1b114ef786d9bde108cd4364359": D("1874.265030291630258023"),
                PREFIX + "0x4dedf26112b3ec8ec46e7e31ea5e123490b05b8b": D("1682.543381680468581972"),
            }
            if any(m.external_income or abs(m.change - expected.get(m.account, D(0))) > D("1e-15")
                   for m in b.movements):
                raise ValueError("Binance test's Sky deposits changed")
            sky = replace(b, identity=tx + SKY_MARKER,
                          movements=tuple(m for m in b.movements if m.account != CASH),
                          external_funding=(), funding_assumption=None)
            boundary = replace(b, identity=tx + MARKER, minted=D(0), minted_by_ilk={},
                               log_index=b.log_index + 1, movements=(cash[0], claim),
                               funding_assumption=assumption)
            changes[tx] = (sky, boundary)
        else:
            changes[tx] = (replace(b, identity=tx + MARKER,
                                   movements=(*b.movements, claim), funding_assumption=assumption),)
    return replace(history,
                   batches=tuple(new for b in history.batches for new in changes.get(b.identity, (b,))),
                   venue_accounts={**history.venue_accounts, VENUE: ACCOUNT},
                   analytics_only_venues=tuple(sorted({*history.analytics_only_venues, VENUE})))
