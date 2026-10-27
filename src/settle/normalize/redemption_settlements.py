"""Realize cash-versus-carrying-value differences when redemption cash arrives.

BUIDL Transfer events have no shared request identifier across the two legs.
Match verified event links first, otherwise a UNIQUE group of outstanding
requests at the contractual expected payout (within a configured tolerance).
Never choose between ambiguous groups or limit eligibility by elapsed days.
Outstanding requests alone do not prevent a period from closing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import yaml

from ..domain.pricing import PricingCategory
from ..extract.transfer_logs import TRANSFER_TOPIC0
from .sources.hypersync_balances import _addr_topic
from .sources.hypersync_venue_events import uint_words, window_logs

CONFIG = Path(__file__).resolve().parents[3] / 'config/redemption_settlements.yaml'
D = Decimal


@dataclass(frozen=True)
class RedemptionLedger:
    revenue_adjustment: Decimal = Decimal("0")
    settlements: tuple[dict, ...] = ()
    outstanding: tuple[dict, ...] = ()
    unmatched_cash: tuple[dict, ...] = ()
    capital_outflows: tuple[dict, ...] = ()


@dataclass(frozen=True)
class PendingRedemptionLedger(RedemptionLedger):
    """CoF diagnostics without changing the frozen cash-ledger wire format."""

    pending_daily: tuple[dict, ...] = ()


def policy_for(prime_id, venue_id):
    rows = yaml.safe_load(CONFIG.read_text())['venues']
    matches = [r for r in rows if r['prime'] == prime_id and r['venue_id'] == venue_id]
    if len(matches) > 1:
        raise ValueError('Duplicate redemption settlement policy')
    if not matches:
        return None
    p = dict(matches[0])
    for key in ['recognition_start', 'history_start']:
        p[key] = date.fromisoformat(p[key])
    p['cof_history_start'] = date.fromisoformat(p.get('cof_history_start', str(p['history_start'])))
    if p['cof_history_start'] > p['history_start']:
        raise ValueError('CoF history must include the cash-recognition opening ledger')
    if p['history_start'] > p['recognition_start']:
        raise ValueError('Redemption history must include the opening outstanding requests')
    for key in ['expected_payout_factor', 'match_tolerance_usd']:
        p[key] = D(str(p[key]))
        if not p[key].is_finite():
            raise ValueError('Non-finite redemption matching parameter')
    if not 0 < p['expected_payout_factor'] <= 1 or not 0 <= p['match_tolerance_usd'] <= 5:
        raise ValueError('Invalid redemption matching parameter')
    for key in ['cash_payer', 'cash_token', 'request_receiver', 'share_token']:
        if not re.fullmatch(r'0x[0-9a-f]{40}', p[key]):
            raise ValueError('Invalid redemption policy address')
    if not isinstance(p['cash_decimals'], int) or not 0 <= p['cash_decimals'] <= 18:
        raise ValueError('Invalid cash decimals')
    links = {}
    linked_requests = set()
    for link in p.get('links', []):
        cash, requests = link['cash'], tuple(link['requests'])
        if not requests or cash in links or len(set(requests)) != len(requests):
            raise ValueError('Duplicate or empty redemption link')
        if linked_requests.intersection(requests):
            raise ValueError('Redemption request linked to multiple payments')
        for identity in (cash, *requests):
            if not re.fullmatch(r'0x[0-9a-f]{64}:\d+', identity):
                raise ValueError('Invalid redemption event identity')
        links[cash] = requests
        linked_requests.update(requests)
    p['links'] = links
    return p


def _unique_group(pending, amount, policy):
    """Find at most two positive-sum candidates; ambiguity is never FIFO-guessed."""
    target = amount
    # Avoid matching unrelated sub-dollar issuer dust to small test requests.
    tolerance = min(policy['match_tolerance_usd'], amount * D('0.001'))
    factor = policy['expected_payout_factor']
    items = sorted(pending.items(), key=lambda item: D(item[1]['shares']), reverse=True)
    candidates = []

    # Bound computation, not request age or group size. An issuer can create
    # an ambiguous/complex batch; require explicit identities instead of
    # hanging the daily worker in an exponential subset search.
    stack = [(0, D(0), ())]
    examined = 0
    while stack and len(candidates) < 2:
        index, total, group = stack.pop()
        if total > target + tolerance:
            continue
        if group and abs(total - target) <= tolerance:
            candidates.append(group)
            if len(candidates) == 2:
                break
        for i in range(index, len(items)):
            examined += 1
            if examined > 10000:
                raise ValueError('Complex redemption cash match; add an exact event link')
            identity, request = items[i]
            subtotal = total + D(request['shares']) * factor
            if subtotal <= target + tolerance:
                stack.append((i + 1, subtotal, (*group, identity)))

    if len(candidates) > 1:
        raise ValueError('Ambiguous redemption cash match; add an exact event link')
    return candidates[0] if candidates else ()


def match_redemptions(venue, period, policy, events, *, track_pending=False):
    """Pure, deterministic ledger from canonical filtered Transfer events.

    Earlier payments consume earlier requests but only cash in the recognition
    period affects revenue. No future cash is visible to a historical cutoff.
    The $1 / $0.9995 carrying price is dated at SHARE EXIT, not cash receipt.
    """
    if period.end < policy['recognition_start'] and not track_pending:
        return RedemptionLedger()
    if (venue.chain.value != policy['chain'] or venue.token.address.hex != policy['share_token']
            or venue.pricing_category != PricingCategory.RWA_TRANCHE
            or venue.nav_oracle is None or venue.nav_oracle.kind != 'const_one'):
        raise ValueError('Cash redemption matching currently supports only constant-$1 RWA NAVs')
    unique = {}
    positions = {}
    for event in events:
        e = dict(event)
        day = date.fromisoformat(e['date'])
        if day < policy['history_start'] or day > period.end or e['block'] > period.pin_blocks[venue.chain]:
            continue
        if e['kind'] not in {'request', 'cash'} or not D(e['amount']).is_finite() or D(e['amount']) < 0:
            raise ValueError('Invalid redemption event')
        if D(e['amount']) == 0:
            continue
        identity = f"{e['tx']}:{e['log_index']}"
        if not re.fullmatch(r'0x[0-9a-f]{64}:\d+', identity):
            raise ValueError('Missing or invalid redemption transaction/log identity')
        if identity in unique and unique[identity] != e:
            raise ValueError('Conflicting duplicate redemption event')
        position = e['block'], e['log_index']
        if position in positions and positions[position] != identity:
            raise ValueError('Conflicting redemption block/log identity')
        positions[position] = identity
        unique[identity] = e
    pending, settlements, unmatched = {}, [], []
    revenue = D(0)
    capital_outflows = []
    pending_deltas = {}
    reserved = {r: cash for cash, requests in policy['links'].items() for r in requests}
    for identity, e in sorted(unique.items(), key=lambda item: (item[1]['block'], item[1]['log_index'])):
        day, amount = date.fromisoformat(e['date']), D(e['amount'])
        if e['kind'] == 'request':
            pending_deltas[day] = pending_deltas.get(day, D(0)) + amount * policy['expected_payout_factor']
            haircut = venue.nav_haircut_bps or D(0)
            if venue.nav_haircut_effective_date and day < venue.nav_haircut_effective_date:
                haircut = D(0)
            carrying = amount * (D(1) - haircut / D(10000))
            pending[identity] = {**e, 'event_id': identity, 'shares': str(amount),
                                 'carrying_value_usd': str(carrying)}
            # BUIDL's amount filter distinguishes incoming yield mints, but
            # also drops small outgoing test redemptions. Restore only
            # verified share exits that the existing threshold excluded.
            if (max(period.start, policy['recognition_start']) <= day
                    and venue.min_transfer_amount_usd
                    and amount < venue.min_transfer_amount_usd):
                capital_outflows.append(pending[identity])
            continue
        explicit = policy['links'].get(identity)
        eligible = {k: v for k, v in pending.items() if k not in reserved or reserved[k] == identity}
        group = explicit or _unique_group(eligible, amount, policy)
        if group and any(k not in eligible for k in group):
            raise ValueError('Cash link references a missing, already settled, or later redemption')
        if not group:
            unmatched.append({**e, 'event_id': identity})
            # Tiny unlinked issuer payments are recorded, not guessed to be
            # redemptions. A material receipt needs attribution before close.
            if (track_pending or day >= policy['recognition_start']) and amount > policy['match_tolerance_usd']:
                raise ValueError(f'Unmatched redemption cash {identity}; add an exact event link')
            continue
        requests = [pending.pop(k) for k in group]
        pending_deltas[day] = pending_deltas.get(day, D(0)) - sum(
            (D(r['shares']) * policy['expected_payout_factor'] for r in requests), D(0),
        )
        carrying = sum((D(r['carrying_value_usd']) for r in requests), D(0))
        variance = amount - carrying
        if max(period.start, policy['recognition_start']) <= day <= period.end:
            revenue += variance
            settlements.append({'cash': {**e, 'event_id': identity}, 'requests': requests,
                                'carrying_value_usd': str(carrying), 'cash_usd': str(amount),
                                'revenue_adjustment_usd': str(variance),
                                'matching': 'explicit_event_link' if explicit else 'unique_expected_payout'})
    daily = []
    if track_pending:
        value = sum((v for day, v in pending_deltas.items() if day < period.start), D(0))
        current = period.start
        while current <= period.end:
            value += pending_deltas.get(current, D(0))
            if value < 0:
                raise ValueError('Negative pending redemption value')
            daily.append({'block_date': current, 'pending_value_usd': value})
            current += timedelta(days=1)
    fields = (revenue, tuple(settlements), tuple(pending.values()), tuple(unmatched), tuple(capital_outflows))
    return PendingRedemptionLedger(*fields, tuple(daily)) if track_pending else RedemptionLedger(*fields)


def restore_redemption_capital(inflows, ledger):
    """Reinsert verified sub-threshold share exits into Cat E capital flows.

    Incoming yield mints remain excluded. This operates only on requests
    already fetched for the redemption ledger; it performs no balance reads.
    """
    if not ledger.capital_outflows:
        return inflows
    corrections = pd.DataFrame([
        {'block_date': date.fromisoformat(r['date']),
         'daily_inflow': -D(r['carrying_value_usd'])}
        for r in ledger.capital_outflows
    ])
    out = pd.concat([inflows[['block_date', 'daily_inflow']], corrections], ignore_index=True)
    out = out.groupby('block_date', as_index=False)['daily_inflow'].sum().sort_values('block_date')
    out['cum_inflow'] = out['daily_inflow'].cumsum()
    return out


def redemption_settlements(prime, venue, period, *, track_pending=False):
    policy = policy_for(prime.id, venue.id)
    if policy is None or (period.end < policy['recognition_start'] and not track_pending):
        return RedemptionLedger()
    if track_pending:
        policy = {**policy, 'history_start': policy['cof_history_start']}
    holder = venue.holder_override or prime.alm[venue.chain]
    who = _addr_topic(holder.value)
    request_to = _addr_topic(bytes.fromhex(policy['request_receiver'][2:]))
    cash_from = _addr_topic(bytes.fromhex(policy['cash_payer'][2:]))
    rows = window_logs(venue.chain.value, [
        {'address': [venue.token.address.hex], 'topics': [[TRANSFER_TOPIC0], [who], [request_to]]},
        {'address': [policy['cash_token']], 'topics': [[TRANSFER_TOPIC0], [cash_from], [who]]},
    ], policy['history_start'], period.pin_blocks[venue.chain], period.end, transactions=True)
    events = []
    for row in rows:
        is_request = row.address == venue.token.address.hex
        expected = (venue.token.address.hex, who, request_to) if is_request else (policy['cash_token'], cash_from, who)
        if (row.address, row.topic1, row.topic2) != expected or row.topic0 != TRANSFER_TOPIC0:
            raise ValueError('Unexpected transfer in redemption source')
        decimals = venue.token.decimals if is_request else policy['cash_decimals']
        events.append({'kind': 'request' if is_request else 'cash',
                       'date': datetime.fromtimestamp(row.block_time, UTC).date().isoformat(),
                       'amount': str(D(uint_words(row.data, 1)[0]) / D(10 ** decimals)),
                       'tx': row.transaction_hash, 'block': row.block_number, 'log_index': row.log_index,
                       'token': row.address, 'sender': '0x' + row.topic1[-40:],
                       'recipient': '0x' + row.topic2[-40:]})
    return match_redemptions(venue, period, policy, events, track_pending=track_pending)


def pending_redemption_values(prime, venue, period):
    """EoD receivables from the SAME matched request/cash ledger as realization.

    CoF history starts at a fixed audited empty ledger, independently of the
    revenue-recognition date. Never use future cash or a rolling T+N lookback.
    Pending BUIDL is valued at the contractual expected exit mark (99.95%),
    preserving the basis of the already-recorded August 31 CoF correction.
    Transport errors, ambiguous matches and unexplained material cash propagate.
    """
    ledger = redemption_settlements(prime, venue, period, track_pending=True)
    return pd.DataFrame(getattr(ledger, 'pending_daily', ()), columns=['block_date', 'pending_value_usd'])
