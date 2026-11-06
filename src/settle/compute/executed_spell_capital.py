"""Execution-specific acquisition costs and links, applied before capital replay.

Raw normalized snapshots stay immutable. These adjustments identify exact
executed transactions/accounts, validate their shapes, and are idempotent.
They never turn a generic receipt into a loan or change total debt draws.
"""
from dataclasses import replace
from decimal import Decimal

D = Decimal
SPARK = '0x1601843c5e9bc251a3272907010afa41fa18347e'
GROVE = '0x491edfb0b8b608044e227225c715981a30f3a44e'
USDS = '0xdc035d45d973e3ec169d2276ddab16f1e407384f'
BUIDL = '0x6a9da2d710bb9b700acde7cb81f10f1ff8c89041'
JTRSY = '0x8c213ee79581ff4984583c6a801e5263418c4b86'
SYRUP = '0x80ac24aa929eaf5013f6436cda2a7ba190f5cc0b'
INITIAL = 'ethereum:0xdd5bf338720c06dd098216e53a276a8bac00fddd80e94757f4e12c9e09f853ab'
PAYMENT = 'ethereum:0xafa23f703044c5296f42ff5202429b0dd16558ddbf677042d2cc9ea036b87667'
DELIVERY = 'ethereum:0xfafb7edda92afb685a8ea0cfb8b26648220cc533219d3f4a7059e7171046f464'
BUIDL_INTEREST = 'ethereum:0x0032e26b8e4b284e3c61ea8aeb0870e3f0dbb7d3173945faf0449ca6ec5138e8'
JTRSY_COST = D('404016484')
BUIDL_COST = D('608367166.98')
SYRUP_COST = D('100928938.340794')
SUFFIX = ':executed-spell'


def account(holder, token):
    return f'ethereum:{holder}:{token}'


def _require(condition, message):
    if not condition:
        raise ValueError(f'Executed spell capital mismatch: {message}')


def _movement(batch, key):
    found = [m for m in batch.movements if m.account == key]
    _require(len(found) == 1, f'{batch.identity}: missing/duplicate {key}')
    return found[0]


def apply_executed_spells(history):
    from ..normalize.allocation_capital import AssetMovement
    from .grove_agora_incentives import recognize_grove_agora_incentives
    from .grove_agora_redemptions import (
        link_grove_agora_redemptions,
        link_grove_agora_subscriptions,
    )
    from .grove_apollo_cash_settlements import link_grove_apollo_cash_settlements
    from .grove_basin_capital import link_grove_basin_shares
    from .grove_buidl_partial_payments import link_grove_buidl_partial_payments
    from .grove_buidl_subscriptions import link_grove_buidl_subscriptions
    from .grove_cash_distributions import recognize_grove_cash_distributions
    from .grove_cctp_v2_capital import link_grove_cctp_v2
    from .grove_curve_swap_gains import recognize_grove_curve_swap_gains
    from .grove_falconx_test_refund import link_falconx_test_refund
    from .grove_galaxy_arch_capital import link_grove_galaxy_arch
    from .grove_historical_capital import link_grove_initial_jaaa, link_grove_jaaa_avalanche
    from .grove_merkl_rewards import recognize_grove_merkl_rewards
    from .grove_paxos_capital import link_grove_paxos_boundary
    from .grove_plume_capital import link_grove_plume_jtrsy
    from .grove_rlusd_conversions import link_grove_rlusd_conversions
    from .grove_secondary_cash import include_grove_secondary_cash
    from .grove_stac_capital import link_grove_stac_subscriptions
    from .spark_arbitrum_spells import link_spark_arbitrum_spells
    from .spark_b2c2_capital import link_spark_b2c2_boundary
    from .spark_base_withdrawals import link_spark_base_withdrawals
    from .spark_buidl_redemptions import link_spark_buidl_redemptions
    from .spark_buidl_subscriptions import link_spark_buidl_subscriptions
    from .spark_early_base_seed import link_spark_early_base_seed
    from .spark_native_seed import link_spark_native_seed
    from .spark_op_uni_withdrawals import link_spark_june_op_uni_withdrawals, link_spark_op_uni_withdrawals
    from .spark_reserve_gifts import recognize_spark_reserve_gifts
    from .spark_anchorage_correction import correct_spark_anchorage_round_trip
    from .spark_binance_capital import link_spark_binance
    from .spark_par_swap_gains import recognize_spark_par_swap_gains
    from .spark_paxos_capital import link_spark_paxos
    from .spark_subproxy_reserve_gifts import recognize_spark_subproxy_reserves
    from .spark_separate_savings_routes import separate_spark_savings_routes
    from .spark_uscc_capital import link_spark_uscc
    from .spark_ustb_capital import link_spark_ustb

    history = correct_spark_anchorage_round_trip(history)
    history = link_spark_paxos(history)
    history = separate_spark_savings_routes(history)
    history = recognize_spark_reserve_gifts(history)
    history = recognize_spark_subproxy_reserves(history)
    history = link_spark_ustb(history)
    history = link_spark_uscc(history)
    history = link_spark_arbitrum_spells(history)
    history = link_spark_buidl_redemptions(history)
    history = link_spark_op_uni_withdrawals(link_spark_b2c2_boundary(history))
    history = link_spark_june_op_uni_withdrawals(history)
    history = link_spark_early_base_seed(link_spark_buidl_subscriptions(history))
    history = link_spark_native_seed(link_spark_base_withdrawals(history))
    history = recognize_spark_par_swap_gains(history)
    history = link_spark_binance(history)
    history = link_grove_plume_jtrsy(link_grove_jaaa_avalanche(link_grove_initial_jaaa(history)))
    history = link_grove_buidl_subscriptions(link_grove_stac_subscriptions(history))
    history = link_grove_rlusd_conversions(history)
    history = link_grove_agora_redemptions(history)
    history = link_grove_agora_subscriptions(history)
    history = link_grove_cctp_v2(history)
    history = link_grove_galaxy_arch(history)
    history = recognize_grove_cash_distributions(history)
    history = recognize_grove_agora_incentives(history)
    history = recognize_grove_merkl_rewards(history)
    history = link_grove_apollo_cash_settlements(history)
    history = include_grove_secondary_cash(history)
    history = link_grove_basin_shares(history)
    history = link_grove_paxos_boundary(history)
    history = link_grove_buidl_partial_payments(history)
    history = link_falconx_test_refund(history)
    history = recognize_grove_curve_swap_gains(history)
    batches = list(history.batches)
    indexes = {b.identity: i for i, b in enumerate(batches)}
    if len(indexes) != len(batches):
        raise ValueError("Duplicate capital transaction")
    custody = {k: list(v) for k, v in history.custody_accounts.items()}
    holders = {a.split(':')[1] for a in history.venue_accounts.values()
               if a.startswith('ethereum:')}
    # September 4 payload, executed September 8, 2025: issuer interest was
    # mistakenly paid to Spark after the portfolio sale and forwarded to Grove.
    # It carries no new borrowed principal. Limit this to the exact execution;
    # a general Spark-sender allowlist would also misclassify capital purchases.
    # https://forum.skyeco.com/t/september-4-2025-proposed-changes-to-spark-for-upcoming-spell/27102/1
    # https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20250904/SparkEthereum_20250904.sol
    # The adjacent spell test asserts the exact 900,612.89 BUIDL balance.
    if BUIDL_INTEREST in indexes and GROVE in holders:
        _require(SPARK not in holders, 'mixed prime history')
        i = indexes[BUIDL_INTEREST]
        b = batches[i]
        _require(b.block == 23319630 and b.chain == 'ethereum', 'BUIDL interest execution block')
        m = _movement(b, account(GROVE, BUIDL))
        _require(b.minted == 0 and m.change == D('900612.89') and m.external_income == 0,
                 'BUIDL forwarded interest')
        batches[i] = replace(b, identity=b.identity + SUFFIX,
            movements=tuple(replace(x, external_income=x.change) if x.account == m.account else x
                            for x in b.movements))
    # July 24 Sky spell executed July 28, 2025 at block 23018751:
    # https://github.com/sky-ecosystem/spells-mainnet/blob/50ea24e8605a31e283100b9f59f873404d939b87/archive/2025-07-24-DssSpell/DssSpell.sol
    # Grove _sendUSDSToSpark fixes JTRSY's cost at 404,016,484 USDS; BUIDL at par:
    # https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20250724/GroveEthereum_20250724.sol
    # Spark delivers both positions in the same transaction:
    # https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20250724/SparkEthereum_20250724.sol
    if INITIAL in indexes and holders & {GROVE, SPARK}:
        _require(not {GROVE, SPARK} <= holders, 'mixed prime history')
        holder = GROVE if GROVE in holders else SPARK
        i = indexes[INITIAL]
        b = batches[i]
        _require(b.block == 23018751 and b.chain == 'ethereum', 'initial execution block')
        j = _movement(b, account(holder, JTRSY))
        buidl = _movement(b, account(holder, BUIDL))
        _require(j.external_income == 0 and buidl.external_income == 0, 'initial gifts')
        if holder == GROVE:
            _require(b.minted == JTRSY_COST + BUIDL_COST, 'Grove acquisition draw')
            _require(j.value_before == 0 and j.change > 0, 'Grove initial JTRSY position')
            _require(buidl.value_before == 0 and buidl.change == BUIDL_COST, 'Grove BUIDL cost')
            corrected = replace(j, change=JTRSY_COST)
        else:
            cash = _movement(b, account(holder, USDS))
            _require(b.minted == 0 and cash.change == JTRSY_COST + BUIDL_COST, 'Spark sale cash')
            _require(j.value_before == -j.change > 0, 'Spark full JTRSY sale')
            _require(buidl.value_before == -buidl.change == BUIDL_COST, 'Spark full BUIDL sale')
            corrected = replace(j, value_before=JTRSY_COST, change=-JTRSY_COST)
        batches[i] = replace(b, identity=b.identity + SUFFIX,
                             movements=tuple(corrected if m.account == j.account else m for m in b.movements))

    # Sky July 16, 2026 authorizes (plots) the two independently cast payloads:
    # https://github.com/sky-ecosystem/spells-mainnet/blob/50ea24e8605a31e283100b9f59f873404d939b87/archive/2026-07-16-DssSpell/DssSpell.sol
    # PAYMENT occurred FIRST (block 25574512); DELIVERY followed at 25574524.
    # Spark mints USDS equal to convertToAssets(85,943,747.637271 syrupUSDC):
    # https://github.com/sparkdotfi/spark-spells/blob/dc2a653f4b2f5491641276e913cae06e221ce8ea/archive/20260716/SparkEthereum_20260716.sol
    # Grove delivers its entire holding to Spark:
    # https://github.com/grove-labs/grove-spells/blob/97bbdf8d89e824e93a28ecaf9cf0628ebc640d12/archive/20260716/GroveEthereum_20260716.sol
    if PAYMENT not in indexes or not holders & {GROVE, SPARK}:
        return replace(history, batches=tuple(batches), custody_accounts=custody)
    _require(not {GROVE, SPARK} <= holders, 'mixed prime history')
    pi = indexes[PAYMENT]
    p = batches[pi]
    _require(p.block == 25574512 and p.chain == 'ethereum', 'payment execution block')
    di = indexes.get(DELIVERY)
    delivery = batches[di] if di is not None else None
    holder = GROVE if GROVE in holders else SPARK
    asset = account(holder, SYRUP)
    cash = account(holder, USDS)
    pm = _movement(p, cash)
    if delivery is not None:
        _require(delivery.block == 25574524 and delivery.chain == 'ethereum', 'delivery execution block')
        _require(p.day == delivery.day and p.timestamp < delivery.timestamp, 'exchange chronology')
        # Moving seller cash recognition by 144 seconds is safe only with no
        # intervening use/mark of either asset and the same daily boundary.
        for other in batches:
            if other.chain == 'ethereum' and p.block < other.block < delivery.block:
                guarded = (asset, cash) if holder == GROVE else (asset,)
                _require(not any(m.account in guarded for m in other.movements),
                         'intervening exchange-account activity')
        dm = _movement(delivery, asset)
        _require(dm.external_income == 0 and delivery.minted == 0, 'delivery income or draw')
    if holder == SPARK:
        _require(pm.change == 0 and abs(p.minted - SYRUP_COST) < D('1e-18'), 'Spark purchase funding')
        pending = 'spell-prepayment:' + PAYMENT
        # Isolate the explicit purchase draw from unrelated reserve sweeps in
        # this spell: those receipts must not absorb a pro-rata share of it.
        purchase = replace(p, identity=p.identity + ':purchase' + SUFFIX,
                           movements=(AssetMovement(pending, D(0), SYRUP_COST),))
        batches[pi] = replace(p, identity=p.identity + SUFFIX, minted=D(0), minted_by_ilk={})
        batches.append(purchase)
        vid = next((v for v, a in history.venue_accounts.items() if a == asset), None)
        _require(vid is not None, 'Spark syrupUSDC venue')
        custody.setdefault(vid, []).append(pending)
        if delivery is not None:
            _require(dm.value_before == 0 and dm.change > 0, 'Spark initial syrupUSDC receipt')
            batches[di] = replace(delivery, identity=delivery.identity + SUFFIX,
                movements=(*tuple(replace(m, change=SYRUP_COST) if m.account == asset else m for m in delivery.movements), AssetMovement(pending, SYRUP_COST, -SYRUP_COST, preserve_basis=True)))
    elif delivery is not None:
        _require(pm.change == SYRUP_COST and pm.external_income == 0 and p.minted == 0,
                 'Grove sale proceeds')
        _require(dm.value_before == -dm.change > 0, 'Grove full syrupUSDC sale')
        # A same-day advance payment precedes delivery. Keep the seller's
        # basis with its asset until delivery, then release it to actual cash.
        # Never fetch a future delivery to close an incomplete pinned history.
        batches[pi] = replace(p, identity=p.identity + SUFFIX,
                              movements=tuple(m for m in p.movements if m.account != cash))
        batches[di] = replace(delivery, identity=delivery.identity + SUFFIX,
            movements=(*tuple(replace(m, value_before=SYRUP_COST, change=-SYRUP_COST) if m.account == asset else m for m in delivery.movements), pm))
    return replace(history, batches=tuple(batches), custody_accounts=custody)
