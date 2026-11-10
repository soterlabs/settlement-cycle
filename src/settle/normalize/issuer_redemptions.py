"""Spark issuer claims beneath #226's common exposure/capital interfaces.

Reviewed canonical links associate off-chain payments. A new source request is
retained immediately; an unexplained payment fails closed rather than selecting
an old request by date. Fixed executions and variable share claims stay distinct.
"""
import re
from datetime import UTC, date, datetime, time
from decimal import Decimal as D
from pathlib import Path

import yaml

from ..domain import Address
from ..extract import hypersync, hypersync_store, rpc
from ..extract.transfer_logs import TRANSFER_TOPIC0
from .sources.hypersync_balances import _addr_topic
from .sources.hypersync_venue_events import uint_words

CONFIG = Path(__file__).resolve().parents[3] / 'config/issuer_redemptions.yaml'


def policy_for(prime, venue):
    matches = [p for p in yaml.safe_load(CONFIG.read_text())['venues']
               if (p['prime'], p['venue_id']) == (prime.id, venue.id)]
    if len(matches) != 1:
        raise ValueError('Issuer async allocation requires exactly one reviewed policy')
    p = matches[0]
    if venue.chain.value != 'ethereum' or p['share_token'] != venue.token.address.hex:
        raise ValueError('Issuer policy asset/chain mismatch')
    if (p['cash_token'] != '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48' or p['cash_decimals'] != 6
            or any(not re.fullmatch(r'0x[0-9a-f]{40}', p[k]) for k in ['cash_payer', 'request_receiver'])):
        raise ValueError('Unsupported issuer underlying/payer configuration')
    if p['valuation'] not in {'fixed_one', 'fixed_execution', 'variable_shares'}:
        raise ValueError('Unsupported issuer claim valuation')
    ids = set()
    for g in p['groups']:
        if not g['requests'] or not g['payments'] or not g['payments'][-1]['final']:
            raise ValueError('Incomplete reviewed issuer association')
        if any(e['final'] for e in g['payments'][:-1]):
            raise ValueError('Payments after issuer finalization')
        for e in g['requests'] + g['payments']:
            if e['identity'] in ids or not re.fullmatch(r'0x[0-9a-f]{64}:\d+', e['identity']):
                raise ValueError('Duplicate/invalid reviewed issuer identity')
            ids.add(e['identity'])
    return p


def quote(venue, block):
    from .allocation_superstate import uscc_capital_price, ustb_capital_price
    value = ustb_capital_price(venue.token, block)
    if value is None:
        value = uscc_capital_price(venue.token, block)
    if value is None or not value.is_finite() or value <= 0:
        raise ValueError('Unavailable issuer execution NAV')
    return value


def source_events(prime, venue, period, policy):
    owner = venue.holder_override or prime.alm[venue.chain]
    owner_topic = _addr_topic(owner.value)
    receiver = _addr_topic(Address.from_str(policy['request_receiver']).value)
    payer = _addr_topic(Address.from_str(policy['cash_payer']).value)
    selections = [
        {'address': [venue.token.address.hex], 'topics': [[TRANSFER_TOPIC0], [owner_topic], [receiver]]},
        {'address': [policy['cash_token']], 'topics': [[TRANSFER_TOPIC0], [payer], [owner_topic]]},
    ]
    from .async_capital import unique_rows
    rows = unique_rows(hypersync_store.fetch_logs(
        venue.chain.value, selections, 0, period.pin_blocks[venue.chain],
        log_fields=[*hypersync._DEFAULT_LOG_FIELDS, 'transaction_hash']), period.pin_blocks[venue.chain])
    events = {}
    for r in rows:
        day = datetime.fromtimestamp(r.block_time, UTC).date()
        if day < date.fromisoformat(policy['history_start']) or day > period.end:
            continue
        request = r.address == venue.token.address.hex
        if (r.topic0 != TRANSFER_TOPIC0 or (r.topic1, r.topic2) !=
                ((owner_topic, receiver) if request else (payer, owner_topic))):
            raise ValueError('Unexpected issuer source event')
        e = dict(identity=f'{r.transaction_hash}:{r.log_index}', block=r.block_number,
                 timestamp=r.block_time, amount=str(D(uint_words(r.data, 1)[0]) /
                 10**(venue.token.decimals if request else policy['cash_decimals'])),
                 kind='request' if request else 'cash')
        if e['identity'] in events and events[e['identity']] != e:
            raise ValueError('Conflicting duplicate issuer event')
        events[e['identity']] = e
    return list(events.values())


def replay(venue, policy, events, *, pin, price_at=quote):
    """Pure ownership ledger; prices are requested only at visible executions."""
    requests = {e['identity']: e for g in policy['groups'] for e in g['requests']}
    payments = {e['identity']: (e, g) for g in policy['groups'] for e in g['payments']}
    excluded = {e['identity']: e for e in policy['excluded_cash']}
    pending, actions, ignored = {}, [], []
    positions = {}
    unique = {}
    for e in events:
        if e['block'] > pin:
            continue
        if (not re.fullmatch(r'0x[0-9a-f]{64}:\d+', e['identity']) or e['block'] <= 0
                or e['timestamp'] <= 0 or not D(e['amount']).is_finite() or D(e['amount']) < 0):
            raise ValueError('Invalid issuer event identity/amount')
        key = e['block'], int(e['identity'].rsplit(':', 1)[1])
        if ((e['identity'] in unique and unique[e['identity']] != e)
                or (key in positions and positions[key] != e['identity'])):
            raise ValueError('Conflicting duplicate issuer event')
        unique[e['identity']], positions[key] = e, e['identity']
    for e in sorted(unique.values(), key=lambda e: (e['block'], int(e['identity'].rsplit(':', 1)[1]))):
        identity, amount = e['identity'], D(e['amount'])
        reviewed = requests.get(identity) or payments.get(identity, (None,))[0] or excluded.get(identity)
        if reviewed and any(e[k] != reviewed[k] for k in ('block', 'timestamp', 'amount')):
            raise ValueError('Issuer event differs from reviewed canonical evidence')
        if not amount:
            continue
        if e['kind'] == 'request':
            fixed = (amount if policy['valuation'] == 'fixed_one' else
                     amount * price_at(venue, e['block']) if policy['valuation'] == 'fixed_execution' else None)
            pending[identity] = dict(**e, original_shares=str(amount), shares=str(amount),
                                     fixed_value=None if fixed is None else str(fixed), paid='0')
            actions.append(dict(**e, share_exit=str(amount), cash_paid='0', final=False))
        elif e['kind'] == 'cash':
            if identity in excluded:
                ignored.append(dict(**e, reason=excluded[identity]['reason']))
                continue
            if identity not in payments:
                raise ValueError(f'Unattributed issuer payment {identity}; add reviewed event links')
            payment, group = payments[identity]
            ids = [r['identity'] for r in group['requests']]
            if any(r not in pending for r in ids):
                raise ValueError('Issuer payment lacks its outstanding source request')
            prior = [p['identity'] for p in group['payments'][:group['payments'].index(payment)]]
            if any(p not in {a['identity'] for a in actions} for p in prior):
                raise ValueError('Issuer payment lacks an earlier installment')
            # Reviewed variable claims are priced only when this receipt is
            # visible. No future settlement NAV leaks into earlier cutoffs.
            if policy['valuation'] == 'variable_shares':
                if not payment['final'] or prior:
                    raise ValueError('Variable issuer partial pricing requires explicit share allocation')
                expected = sum((D(pending[r]['shares']) for r in ids), D(0)) * price_at(venue, e['block'])
                if abs(expected - amount) > D('.01'):
                    raise ValueError('Issuer settlement differs from its execution NAV')
            if payment['final']:
                for r in ids:
                    del pending[r]
            else:
                if len(ids) != 1 or pending[ids[0]]['fixed_value'] is None:
                    raise ValueError('Partial issuer payment requires one fixed claim')
                r = pending[ids[0]]
                remaining = D(r['fixed_value']) - amount
                if remaining < 0:
                    raise ValueError('Issuer advance exceeds outstanding fixed claim')
                r['fixed_value'], r['paid'] = str(remaining), str(D(r['paid']) + amount)
            actions.append(dict(**e, share_exit='0', cash_paid=str(amount), final=payment['final']))
        else:
            raise ValueError('Unknown issuer event kind')
    return pending, actions, ignored


def ledger_at(prime, venue, period):
    p = policy_for(prime, venue)
    return replay(venue, p, source_events(prime, venue, period, p), pin=period.pin_blocks[venue.chain])


def exposure(prime, venue, block):
    from ..domain.period import Period
    from .async_vaults import AsyncVaultExposure
    day = datetime.fromtimestamp(rpc.block_timestamp(venue.chain, block), UTC).date()
    pending, _, _ = ledger_at(prime, venue, Period(day, day, {venue.chain: block}))
    shares = sum((D(r['shares']) for r in pending.values() if r['fixed_value'] is None), D(0))
    cash = sum((D(r['fixed_value']) for r in pending.values() if r['fixed_value'] is not None), D(0))
    return AsyncVaultExposure(redemption_shares=shares, redemption_cash=cash, claims=tuple(
        dict(**r, owner=(venue.holder_override or prime.alm[venue.chain]).hex,
             token=venue.token.address.hex, valuation='share_NAV' if r['fixed_value'] is None else 'fixed_USDC')
        for r in pending.values()))


def capital_events(prime, venue, period, *, opening_block, block_resolver, nav_at_block):
    _, actions, _ = ledger_at(prime, venue, period)
    out = []
    for a in actions:
        day = datetime.fromtimestamp(a['timestamp'], UTC).date()
        if not period.start <= day <= period.end:
            continue
        block = (period.pin_blocks[venue.chain] if day == period.end else
                 min(block_resolver.block_at_or_before(venue.chain.value, datetime.combine(day, time.max, UTC)),
                     period.pin_blocks[venue.chain]))
        value = D(a['share_exit']) * nav_at_block(block) - D(a['cash_paid'])
        out.append(dict(block_date=day, capital_adjustment_usd=value, **a))
    return out


def settlement_ledger(prime, venue, period):
    from .redemption_settlements import RedemptionLedger
    pending, actions, ignored = ledger_at(prime, venue, period)
    restored = tuple(dict(a, date=datetime.fromtimestamp(a['timestamp'], UTC).date().isoformat(),
                          carrying_value_usd=a['share_exit']) for a in actions
                     if a['kind'] == 'request' and venue.min_transfer_amount_usd
                     and D(a['share_exit']) < venue.min_transfer_amount_usd
                     and period.start <= datetime.fromtimestamp(a['timestamp'], UTC).date() <= period.end)
    return RedemptionLedger(capital_outflows=restored, outstanding=tuple(pending.values()),
                            settlements=tuple(a for a in actions if a['kind'] == 'cash'),
                            unmatched_cash=tuple(ignored))
