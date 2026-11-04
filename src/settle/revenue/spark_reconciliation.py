"""Transaction-level supply bridges. Capital movements are never revenue.

Integer claim deltas reproduce contract rounding. WETH interest uses the same
explicit daily closing USD translation convention as depositor interest.
"""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext

from ..domain.primes import Chain
from ..extract.aave_reconstruct import BT_T0, BURN_T0, MINT_T0, _words, ray_div, ray_mul
from ..extract.cache import cached
from ..extract.hypersync import _DEFAULT_LOG_FIELDS, query_logs
from ..extract.hypersync_store import fetch_logs
from ..extract.transfer_logs import TRANSFER_TOPIC0
from .spark_onchain import topic
from .spark_savings import EVENTS
from .verification import digest

ETH_TRANSLATION = "daily_closing_ETH_USD_nonforward_quote_max_age_2h_confidence_gte_0.9"
DEPOSIT = next(k for k, v in EVENTS.items() if v == "deposit")
WITHDRAW = next(k for k, v in EVENTS.items() if v == "withdraw")


def unique(rows):
    result = {}
    for row in rows:
        key = row.block_number, row.log_index
        if key in result and result[key] != row:
            raise ValueError("Conflicting transaction evidence")
        result[key] = row
    return sorted(result.values(), key=lambda r: (r.block_number, r.log_index))


def owner_logs(source, chain, token, *, joined=False):
    holder = topic(source.prime.alm[Chain(chain)].hex)
    return unique(
        fetch_logs(
            chain,
            [
                {"address": [token], "topics": [[TRANSFER_TOPIC0], [holder]]},
                {"address": [token], "topics": [[TRANSFER_TOPIC0], [], [holder]]},
            ],
            source.opening_pins[chain] + 1,
            source.closing_pins[chain],
            log_fields=[*_DEFAULT_LOG_FIELDS, "transaction_hash"],
            join_mode="JoinAll" if joined else None,
            max_num_logs=100000,
        )
    )


def claim_bridge(opening, closing, capital, *, scale, rows, basis):
    with localcontext() as ctx:
        ctx.prec = 60
        value = Decimal(closing - opening - capital) / scale
        return {
            "status": "available",
            "quality": "supported",
            "recognition": "accrual",
            "value": str(value),
            "basis": basis,
            "reconciliation": {
                "opening_claim_units": str(Decimal(opening) / scale),
                "closing_claim_units": str(Decimal(closing) / scale),
                "net_capital_units": str(Decimal(capital) / scale),
                "interest_units": str(value),
                "event_count": len(rows),
                "event_digest": digest(
                    [
                        (
                            r.block_number,
                            r.log_index,
                            r.address,
                            r.topic0,
                            r.topic1,
                            r.topic2,
                            r.topic3,
                            r.data,
                        )
                        for r in rows
                    ]
                ),
            },
        }


def vault_supply(source, venue):
    """ERC4626 deposits/withdrawals at actual assets; ordinary sUSDS transfers at chi.

    Fee mints and unmatched share mutations fail closed, rather than being
    silently included in supply return. Only canonical sUSDS accepts ordinary
    share transfers: its chi conversion is independent of vault cash flows.
    """
    chain, token = str(venue.chain), venue.token.address.hex
    holder = topic(source.prime.alm[venue.chain].hex)
    start, end = source.opening_pins[chain], source.closing_pins[chain]
    rows = owner_logs(source, chain, token, joined=True)
    own = [r for r in rows if r.address.lower() == token.lower()]
    balances = [
        source.number(chain, token, "balanceOf(address)", b, int(holder, 16)) for b in (start, end)
    ]
    claims = [
        source.number(chain, token, "convertToAssets(uint256)", b, q)
        for b, q in zip((start, end), balances, strict=True)
    ]
    expected, actual = defaultdict(int), defaultdict(int)
    capital = quantity = 0
    zero = topic("0x" + "0" * 40)
    for r in own:
        if r.topic0 == DEPOSIT and r.topic2 == holder:
            assets, shares = _words(r.data)
            expected[r.transaction_hash] += shares
            capital += assets
        elif r.topic0 == WITHDRAW and r.topic3 == holder:
            assets, shares = _words(r.data)
            expected[r.transaction_hash] -= shares
            capital -= assets
        elif r.topic0 == TRANSFER_TOPIC0 and (r.topic1 == holder or r.topic2 == holder):
            (amount,) = _words(r.data)
            delta = amount * ((r.topic2 == holder) - (r.topic1 == holder))
            quantity += delta
            if r.topic1 == zero or r.topic2 == zero:
                actual[r.transaction_hash] += delta
            elif delta:
                if venue.id != "S32":
                    raise ValueError("Unclassified vault share transfer")
                # All transactions within a block share a timestamp; canonical
                # chi is unaffected by deposits. A rate reset within the same
                # block could change transfer NAV, so reject that ambiguity.
                from ..extract._keccak import keccak256

                file_topic = "0x" + keccak256(b"File(bytes32,uint256)").hex()
                changes = query_logs(
                    chain,
                    [{"address": [token], "topics": [[file_topic]]}],
                    r.block_number,
                    r.block_number,
                    max_num_logs=100000,
                ).rows
                if changes:
                    raise ValueError("sUSDS transfer shares a block with a rate reset")
                q_before = balances[0] + quantity - delta
                capital += source.number(
                    chain, token, "convertToAssets(uint256)", r.block_number, q_before + delta
                ) - source.number(
                    chain, token, "convertToAssets(uint256)", r.block_number, q_before
                )
    if dict(actual) != dict(expected) or balances[0] + quantity != balances[1]:
        raise ValueError("Vault capital/share events do not reproduce closing balance")
    result = claim_bridge(
        *claims,
        capital,
        scale=10**venue.underlying.decimals,
        rows=own,
        basis="closing_claim_minus_opening_claim_minus_actual_ERC4626_capital",
    )
    result.update(venue=venue.id, chain=chain, token_address=token)
    return result


def scaled_changes(rows, holder):
    """Decode signed scaled mutations and the authenticated event index."""
    changes = []
    for r in unique(rows):
        w = _words(r.data)
        if r.topic0 == MINT_T0 and r.topic2 == holder:
            amount = w[0] - w[1]
            delta = ray_div(abs(amount), w[2]) * (1 if amount >= 0 else -1)
            index = w[2]
        elif r.topic0 == BURN_T0 and r.topic1 == holder:
            delta, index = -ray_div(w[0] + w[1], w[2]), w[2]
        elif r.topic0 == BT_T0 and (r.topic1 == holder or r.topic2 == holder):
            delta, index = w[0] * ((r.topic2 == holder) - (r.topic1 == holder)), w[1]
        else:
            continue
        changes.append((r, delta, index))
    return changes


def aave_supply(source, venue):
    chain, token = str(venue.chain), venue.token.address.hex
    holder = topic(source.prime.alm[venue.chain].hex)
    rows = unique(
        fetch_logs(
            chain,
            [
                {"address": [token], "topics": [[MINT_T0], [], [holder]]},
                {"address": [token], "topics": [[BURN_T0, BT_T0], [holder]]},
                {"address": [token], "topics": [[BT_T0], [], [holder]]},
            ],
            source.opening_pins[chain] + 1,
            source.closing_pins[chain],
            log_fields=[*_DEFAULT_LOG_FIELDS, "transaction_hash"],
            max_num_logs=100000,
        )
    )
    start = source.period.start - timedelta(days=1)
    days = [start + timedelta(days=i) for i in range(source.period.n_days + 1)]
    pool = f"0x{source.number(chain, token, 'POOL()', source.closing_pins[chain]):040x}"

    def state(day):
        b = source.block(chain, day)
        q = source.number(chain, token, "scaledBalanceOf(address)", b, int(holder, 16))
        index = source.number(
            chain,
            pool,
            "getReserveNormalizedIncome(address)",
            b,
            int(venue.underlying.address.hex, 16),
        )
        claim = ray_mul(q, index)
        if claim != source.number(chain, token, "balanceOf(address)", b, int(holder, 16)):
            raise ValueError("SparkLend scaled claim differs from balanceOf")
        return q, index, claim

    opening = state(start)
    q, capital = opening[0], 0
    daily_capital = defaultdict(int)
    for r, delta, index in scaled_changes(rows, holder):
        if q + delta < 0:
            raise ValueError("SparkLend events miss opening inventory")
        flow = ray_mul(q + delta, index) - ray_mul(q, index)
        capital += flow
        daily_capital[datetime.fromtimestamp(r.block_time, UTC).date()] += flow
        q += delta
    closing = state(source.period.end)
    if q != closing[0]:
        raise ValueError("SparkLend scaled mutations do not bridge closing inventory")
    scale = 10**venue.underlying.decimals
    result = claim_bridge(
        opening[2],
        closing[2],
        capital,
        scale=scale,
        rows=rows,
        basis="exact_scaled_mutations_at_event_index_capital_adjusted_claim_growth",
    )
    if venue.underlying.symbol == "WETH":
        with localcontext() as ctx:
            ctx.prec = 60
            previous, usd, units, prices = opening[2], Decimal(0), 0, []
            for day in days[1:]:
                current = closing[2] if day == source.period.end else state(day)[2]
                earned = current - previous - daily_capital[day]
                price = source.price("coingecko:ethereum", day)
                usd += Decimal(earned) / scale * price
                units += earned
                prices.append(
                    {
                        "date": str(day),
                        "interest_units": str(Decimal(earned) / scale),
                        "usd_price": str(price),
                        "opening_claim_units": str(Decimal(previous) / scale),
                        "closing_claim_units": str(Decimal(current) / scale),
                        "net_capital_units": str(Decimal(daily_capital[day]) / scale),
                    }
                )
                previous = current
            if units != closing[2] - opening[2] - capital:
                raise ValueError("WETH daily accrual does not reconcile")
            result.update(
                value=str(usd), daily_translation=prices, translation_policy=ETH_TRANSLATION
            )
    result.update(venue=venue.id, chain=chain, token_address=token)
    return result


def bridged_susds(source, venue):
    """PSM3's local conversion rate, with every bridged share flow reconciled."""
    chain, token = str(venue.chain), venue.token.address.hex
    holder = topic(source.prime.alm[venue.chain].hex)
    psm = "0x2b05f8e1cacc6974fd79a673a341fe1f58d27266"
    start, end = source.opening_pins[chain], source.closing_pins[chain]

    def rate(block):
        if source.number(chain, psm, "susds()", block) != int(token, 16) or source.number(
            chain, psm, "usds()", block
        ) != int(venue.underlying.address.hex, 16):
            raise ValueError("PSM3 assets changed")
        provider = source.number(chain, psm, "rateProvider()", block)
        if not provider:
            raise ValueError("Missing PSM3 rate provider")
        return provider, source.number(chain, f"0x{provider:040x}", "getConversionRate()", block)

    balances = [
        source.number(chain, token, "balanceOf(address)", b, int(holder, 16)) for b in (start, end)
    ]
    rates = [rate(b) for b in (start, end)]
    rows = owner_logs(source, chain, token)
    q, capital = balances[0], 0
    for r in rows:
        # Unchanged adjacent-block conversion rates prove that a same-block
        # oracle update cannot introduce unknown transfer pricing here.
        previous, current = rate(r.block_number - 1), rate(r.block_number)
        if previous != current or current[0] != rates[0][0] or current[1] <= 0:
            raise ValueError("Bridged share transfer needs intrablock oracle state")
        oracle_rows = query_logs(
            chain,
            [{"address": [f"0x{current[0]:040x}"]}],
            r.block_number,
            r.block_number,
            max_num_logs=100000,
        ).rows
        if oracle_rows:
            raise ValueError("Bridged share transfer shares a block with an oracle update")
        delta = int(r.data, 16) * ((r.topic2 == holder) - (r.topic1 == holder))
        capital += (q + delta) * current[1] // 10**27 - q * current[1] // 10**27
        q += delta
    if q != balances[1] or q < 0 or rates[0][0] != rates[1][0] or min(r[1] for r in rates) <= 0:
        raise ValueError("Bridged sUSDS share/rate bridge does not reconcile")
    return claim_bridge(
        balances[0] * rates[0][1] // 10**27,
        balances[1] * rates[1][1] // 10**27,
        capital,
        scale=10**18,
        rows=rows,
        basis="PSM3_local_rate_capital_adjusted_bridged_share_claim_growth",
    )


def constant_inventory_gain(source, kind, old):
    """Certify MORPHO only if the complete token transfer ledger is empty."""
    from .spark_onchain import MORPHO

    if kind != "morpho" or old.get("status") != "available":
        return old
    start = source.period.start - timedelta(days=1)
    holdings, evidence = [], []
    for chain, token in MORPHO.items():
        rows = owner_logs(source, chain, token)
        opening, closing = (
            source.balance(chain, token, start),
            source.balance(chain, token, source.period.end),
        )
        if any(int(r.data, 16) for r in rows) or opening != closing:
            return old
        holdings.append(opening)
        evidence.append(
            {
                "chain": chain,
                "token": token,
                "opening_units": str(opening),
                "closing_units": str(closing),
                "transfer_count": len(rows),
            }
        )
    coin = "ethereum:" + MORPHO["ethereum"]
    value = sum(holdings, Decimal(0)) * (
        source.price(coin, source.period.end) - source.price(coin, start)
    )
    return {
        **old,
        "value": str(value),
        "asset_price_gain_usd": str(value),
        "quality": "supported",
        "basis": "constant_inventory_verified_full_transfer_ledger_times_closing_minus_opening_USD_quote",
        "inventory_evidence": evidence,
    }


def fluid_base(source, venue):
    """Constant fsUSDS inventory valued via the local Base PSM3 sUSDS rate."""
    chain, token, psm = (
        "base",
        venue.token.address.hex,
        "0x1601843c5e9bc251a3272907010afa41fa18347e",
    )
    holder = source.prime.alm[venue.chain].hex
    rows = owner_logs(source, chain, token)
    if any(int(r.data, 16) for r in rows):
        raise ValueError("Fluid share flows need event-level wrapper valuation")
    claims, quantities = [], []
    for block in (source.opening_pins[chain], source.closing_pins[chain]):
        underlying = int(venue.underlying.address.hex, 16)
        if (
            source.number(chain, token, "asset()", block) != underlying
            or source.number(chain, psm, "susds()", block) != underlying
        ):
            raise ValueError("Fluid or Base PSM3 underlying changed")
        quantity = source.number(chain, token, "balanceOf(address)", block, int(holder, 16))
        shares = source.number(chain, token, "convertToAssets(uint256)", block, quantity)
        provider = source.number(chain, psm, "rateProvider()", block)
        rate = source.number(chain, f"0x{provider:040x}", "getConversionRate()", block)
        if rate <= 0:
            raise ValueError("Base sUSDS rate is zero")
        claims.append(shares * rate // 10**27)
        quantities.append(quantity)
    if quantities[0] != quantities[1]:
        raise ValueError("Fluid constant-inventory bridge failed")
    return {
        **claim_bridge(
            *claims,
            0,
            scale=10**18,
            rows=rows,
            basis="constant_Fluid_shares_times_full_wrapper_claim_at_local_Base_PSM3_rate",
        ),
        "venue": venue.id,
    }


def net_sparklend_gain(source, old, supply, savings):
    """Net FX revaluation using the explicit daily closing recognition policy.

    Daily claim bridges independently reconcile token interest and capital.
    Capital and interest translate at that day's close, so FX gain belongs to
    the opening claim only. Depositor FX is offset using the identical rule.
    """
    if (
        old.get("status") != "available"
        or supply.get("status") != "available"
        or savings.get("status") != "available"
    ):
        return old
    asset_days = supply.get("daily_translation", [])
    liability_days = savings.get("daily_liability", [])
    if len(asset_days) != source.period.n_days or len(liability_days) != len(asset_days):
        return old
    if [p["date"] for p in savings.get("daily_translation", [])] != [
        p["date"] for p in asset_days
    ] or any(
        p["usd_price"] != q["usd_price"]
        for p, q in zip(savings["daily_translation"], asset_days, strict=True)
    ):
        raise ValueError("Asset and depositor USD translation conventions differ")
    previous_price = source.price("coingecko:ethereum", source.period.start - timedelta(days=1))
    asset_gain = liability_gain = Decimal(0)
    ledger = []
    for asset, liability in zip(asset_days, liability_days, strict=True):
        if asset["date"] != liability["date"]:
            raise ValueError("Net ETH revaluation dates differ")
        for row in (asset, liability):
            if Decimal(row["closing_claim_units"]) - Decimal(row["opening_claim_units"]) - Decimal(
                row["net_capital_units"]
            ) != Decimal(row["interest_units"]):
                raise ValueError("Net ETH revaluation claim bridge failed")
        price = Decimal(asset["usd_price"])
        gross = Decimal(asset["opening_claim_units"]) * (price - previous_price)
        offset = Decimal(liability["opening_claim_units"]) * (price - previous_price)
        asset_gain += gross
        liability_gain += offset
        ledger.append(
            {
                "date": asset["date"],
                "opening_usd_price": str(previous_price),
                "closing_usd_price": str(price),
                "asset_price_gain_usd": str(gross),
                "depositor_offset_usd": str(offset),
                "asset_bridge": asset,
                "liability_bridge": liability,
            }
        )
        previous_price = price
    result = {
        **old,
        "value": str(asset_gain - liability_gain),
        "asset_price_gain_usd": str(asset_gain),
        "depositor_offset_usd": str(liability_gain),
        "quality": "supported",
        "basis": "daily_close_FX_revaluation_with_reconciled_capital_and_interest_less_Savings_depositor_FX",
        "translation_policy": ETH_TRANSLATION,
        "daily_revaluation": ledger,
    }
    result.pop("confidence_reason", None)
    return result


@cached(source_id="spark-reporting.avalanche-native-zero-history.v1")
def avalanche_native_history(holder, opening_pin, closing_pin):
    """Accept empty native activity only from an index covering the cutoff."""
    import requests

    root = "https://api.routescan.io/v2/network/mainnet/evm/43114"
    response = requests.get(root + "/blocks", params={"limit": 1}, timeout=30)
    response.raise_for_status()
    head = response.json()["items"][0]
    if head["number"] < closing_pin + 128:
        raise ValueError("Native activity index has not finalized the cutoff")
    histories = {}
    for action in ("txlist", "txlistinternal"):
        response = requests.get(
            root + "/etherscan/api",
            params={
                "module": "account",
                "action": action,
                "address": holder,
                "startblock": opening_pin + 1,
                "endblock": closing_pin,
                "page": 1,
                "offset": 10000,
                "sort": "asc",
            },
            timeout=30,
        )
        response.raise_for_status()
        document = response.json()
        rows = document.get("result")
        if (
            not isinstance(rows, list)
            or len(rows) >= 10000
            or document.get("status") not in ("0", "1")
        ):
            raise ValueError("Incomplete native activity history")
        if document["status"] == "0" and (
            rows or document.get("message") != "No transactions found"
        ):
            raise ValueError("Native activity provider did not confirm an empty result")
        for row in rows:
            if not opening_pin < int(row["blockNumber"]) <= closing_pin:
                raise ValueError("Native activity outside requested boundaries")
            if row["from"].lower() == holder or (row.get("isError") != "1" and int(row["value"])):
                raise ValueError("Native inventory has nonzero activity")
        histories[action] = rows
    return {
        "indexed_head": head["number"],
        "indexed_head_timestamp": head["timestamp"],
        "history_counts": {k: len(v) for k, v in histories.items()},
        "history_digest": digest(histories),
        "provider": "routescan.avalanche.account.normal_and_internal_transactions",
    }


def empty_avax_gain(source, old):
    from .spark_onchain import WAVAX

    if old.get("status") != "available" or Decimal(old["value"]) != 0:
        return old
    chain = "avalanche_c"
    before, end = source.period.start - timedelta(days=1), source.period.end
    if any(source.native(chain, day) or source.balance(chain, WAVAX, day) for day in (before, end)):
        return old
    rows = owner_logs(source, chain, WAVAX)
    if any(int(r.data, 16) for r in rows):
        return old
    proof = avalanche_native_history(
        source.prime.alm[Chain(chain)].hex, source.opening_pins[chain], source.closing_pins[chain]
    )
    result = {
        **old,
        "quality": "supported",
        "basis": "empty_AVAX_WAVAX_inventory_verified_pinned_balances_and_full_month_native_and_token_history",
        "inventory_evidence": {**proof, "wrapped_transfer_count": len(rows)},
    }
    result.pop("confidence_reason", None)
    return result
