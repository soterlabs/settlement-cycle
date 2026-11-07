#!/usr/bin/env python3
"""Audit a pinned Grove ALM transfer capture without changing settlement numbers.

Unknown contracts and counterparties stay unresolved. Route identification is
distinct from verification that a financial amount was booked correctly.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from settle.domain.config import load_prime_by_id  # noqa: E402
from settle.domain.sky_tokens import PAR_STABLES_BY_CHAIN, USDS_BY_CHAIN  # noqa: E402
from settle.extract._keccak import keccak256  # noqa: E402
from settle.extract.transfer_logs import TRANSFER_TOPIC0  # noqa: E402

NEW_BASE_VAULT = '0xbeef0786756810478b88982de00f3cd7fdb8e7c7'
POOL = '0xbafead7c60ea473758ed6c6021505e8bbd7e8e5d'
USDC_ETH = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
USDC_BASE = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
USDC_MONAD = '0x754704bc059f8c67012fed69bc8a327a5aafb603'
COINBASE_PAYER = '0xcd531ae9efcce479654c4926dec5f6209531ca7b'
# Reviewed unsolicited transfers only; unknown is never a synonym for spam.
SPAM = {
    ('ethereum', '0x113f5c181237ecea75574bf4d6f06f8dd3d81ce1'),
    ('ethereum', '0x4a4a792eaca707308324b32f987fe1d6ad00e5b2'),
    ('ethereum', '0x68725d45a960c100e082dd14ec82c9d83c2240a6'),
    ('ethereum', '0x91d6cf4af9697ec95d9e1754e84d2b31c67a5ab7'),
    ('monad', '0x29f9c424803ac7b09d97ee218adbbb9329703946'),
}


def verify_bridges(directory, captures, proofs):
    """Bind every claimed bridge to actual funded and received message bytes."""
    transmitter = '0x81d40f21f12a8f0e3252bccb954d722d4c464b64'
    messenger = '0x28b5a0e9c621a5badaa536219b3a228c8168cf5d'
    minter = '0xfd78ee919681417d192449715b2594ab58f5d002'
    tokens = {'ethereum': USDC_ETH, 'base': USDC_BASE}
    domains = {'ethereum': 0, 'base': 6}
    sent_topic = '0x' + keccak256(b'MessageSent(bytes)').hex()
    received_topic = '0x' + keccak256(b'MessageReceived(address,uint32,bytes32,bytes32,uint32,bytes)').hex()
    burn_topic = '0x' + keccak256(
        b'DepositForBurn(address,uint256,address,bytes32,uint32,bytes32,bytes32,uint256,uint32,bytes)'
    ).hex()
    with gzip.open(Path(directory) / 'iris-responses.json.gz', 'rt') as source:
        iris = json.load(source)
    indexes = {c: {(r['transaction_hash'], r['log_index']): r for r in capture['logs']}
               for c, capture in captures.items()}

    def dynamic(row, word):
        data = bytes.fromhex(row['data'][2:])
        offset = int.from_bytes(data[word * 32:(word + 1) * 32])
        size = int.from_bytes(data[offset:offset + 32])
        body = data[offset + 32:offset + 32 + size]
        if len(body) != size:
            raise ValueError('Truncated bridge message')
        return body

    consumed, nonces, burns = set(), set(), set()
    for p in proofs:
        source, dest = p['source_chain'], p['destination_chain']
        tx, received = p['source_tx'], p['destination_tx']
        sent = indexes[source][(tx, p['source_log'])]
        payment = indexes[source][(tx, p['payment_log'])]
        burn = indexes[source][(tx, p['source_log'] + 1)]
        receipt = indexes[dest][(received, p['destination_log'])]
        mint = indexes[dest][(received, p['mint_log'])]
        items = [r for r in iris[source + ':' + tx]['messages']
                 if r.get('eventNonce') == p['nonce'] and r.get('status') == 'complete']
        if len(items) != 1 or p['nonce'] in nonces:
            raise ValueError('Ambiguous or reused CCTP nonce')
        nonces.add(p['nonce'])
        message = bytes.fromhex(items[0]['message'][2:])
        if len(message) != 376:
            raise ValueError('Unsupported CCTP message shape')
        template = bytearray(message)
        template[12:44], template[144:148] = bytes(32), bytes(4)
        body = message[148:]
        owner, recipient = '0x' + body[112:132].hex(), '0x' + body[48:68].hex()
        amount = int.from_bytes(body[68:100])
        payment_id = (source, tx, payment['log_index'])
        valid = (
            sent['address'] == transmitter and sent['topic0'] == sent_topic
            and dynamic(sent, 0) == template
            and int.from_bytes(message[4:8]) == domains[source]
            and int.from_bytes(message[8:12]) == domains[dest]
            and '0x' + message[12:44].hex() == p['nonce']
            and '0x' + message[56:76].hex() == messenger
            and '0x' + message[88:108].hex() == messenger
            and '0x' + body[16:36].hex() == tokens[source]
            and owner in captures[source]['scope']['holders']
            and recipient in captures[dest]['scope']['holders']
            and not any(body[132:])
            and Decimal(p['amount_usdc']) == Decimal(amount) / 10**6
            and burn['address'] == messenger and burn['topic0'] == burn_topic
            and burn['topic1'][-40:] == tokens[source][2:]
            and burn['topic2'][-40:] == owner[2:]
            and payment['address'] == tokens[source] and payment['topic0'] == TRANSFER_TOPIC0
            and payment['topic1'][-40:] == owner[2:] and payment['topic2'][-40:] == minter[2:]
            and int(payment['data'], 16) == amount
            and payment_id not in consumed
            and receipt['address'] == transmitter and receipt['topic0'] == received_topic
            and receipt['topic2'] == p['nonce'] and dynamic(receipt, 2) == body
            and mint['address'] == tokens[dest] and mint['topic0'] == TRANSFER_TOPIC0
            and int(mint['topic1'], 16) == 0 and mint['topic2'][-40:] == recipient[2:]
            and int(mint['data'], 16) == amount
        )
        if not valid:
            raise ValueError('Bridge proof differs from canonical captured funding or mint')
        consumed.add(payment_id)
        burns.add((source, tx, burn['log_index']))
    observed = {(c, r['transaction_hash'], r['log_index'])
                for c, capture in captures.items() for r in capture['logs']
                if r['address'] == messenger and r.get('topic0') == burn_topic
                and '0x' + r['topic2'][-40:] in capture['scope']['holders']}
    if observed != burns:
        raise ValueError('Not every captured Grove CCTP burn has a proof')


def verify_native(directory, captures, controls):
    flows = []
    for suffix in ('', '-eth-base'):
        status = json.loads((directory / f'dune-native{suffix}-status.json').read_text())
        response = json.loads((directory / f'dune-native{suffix}-results.json').read_text())
        if status.get('state') != 'QUERY_STATE_COMPLETED':
            raise ValueError('Native trace query did not complete')
        result = response['result']
        if result['metadata']['total_row_count'] != len(result['rows']):
            raise ValueError('Native trace result is truncated')
        for row in result['rows']:
            c = row['chain']
            scope = captures[c]['scope']
            if not scope['from_block'] <= row['block_number'] < scope['to_block']:
                raise ValueError('Native trace outside requested range')
            sender, receiver = row['sender'].lower(), row['receiver'].lower()
            if sender not in scope['holders'] and receiver not in scope['holders']:
                raise ValueError('Unrelated native trace')
            flows.append({**row, 'sender': sender, 'receiver': receiver})
    gas = json.loads((directory / 'monad-gas-receipts.json').read_text())
    signed = {(c, t['hash']) for c, capture in captures.items()
              for t in capture['transactions'] if t.get('from') in capture['scope']['holders']}
    if signed != {('monad', r['tx']) for r in gas}:
        raise ValueError('Missing gas accounting for an ALM-signed transaction')
    if any(int(r['gas_used']) * int(r['effective_gas_price']) != int(r['fee_wei']) for r in gas):
        raise ValueError('Invalid gas receipt arithmetic')
    for control in controls:
        if control['token'] != 'native':
            continue
        c, holder = control['chain'], control['holder']
        net = sum((int(r['value_wei']) if r['receiver'] == holder else 0)
                  - (int(r['value_wei']) if r['sender'] == holder else 0)
                  for r in flows if r['chain'] == c)
        fees = sum(int(r['fee_wei']) for r in gas) if c == 'monad' else 0
        if int(control['closing_raw']) - int(control['opening_raw']) != net - fees:
            raise ValueError('Native trace and gas totals do not reconcile to balances')
    return flows, gas


def validate_pages(scope, pages):
    cursor = scope['from_block']
    for page in pages:
        if page['from_block'] != cursor or page['next_block'] <= cursor:
            raise ValueError('Capture has a gap or non-advancing page')
        height = page['archive_height']
        height = int(height, 16) if isinstance(height, str) and height.startswith('0x') else int(height)
        if height < scope['to_block'] - 1:
            raise ValueError('Archive does not cover the requested closing block')
        cursor = page['next_block']
    if cursor != scope['to_block']:
        raise ValueError('Incomplete capture')


def transfers(chain, capture, metadata):
    validate_pages(capture['scope'], capture['pages'])
    holders = set(capture['scope']['holders'])
    unique = {}
    for log in capture['logs']:
        if log.get('topic0') != TRANSFER_TOPIC0:
            continue
        if not log.get('topic1') or not log.get('topic2'):
            raise ValueError('Malformed Transfer event')
        sender, receiver = ('0x' + log[f'topic{i}'][-40:] for i in (1, 2))
        if sender not in holders and receiver not in holders:
            continue
        identity = (log['block_number'], log['log_index'])
        if identity in unique:
            if unique[identity]['raw_log'] != log:
                raise ValueError('Conflicting Transfer event identity')
            continue
        if not capture['scope']['from_block'] <= log['block_number'] < capture['scope']['to_block']:
            raise ValueError('Transfer outside requested range')
        if not log.get('transaction_hash'):
            raise ValueError('Transfer lacks transaction identity')
        token = log['address']
        symbol, decimals = metadata.get((chain, token), ('unknown', None))
        nft = log.get('topic3') is not None
        if not nft and len(log.get('data', '')) != 66:
            raise ValueError('Malformed fungible Transfer amount')
        amount = (str(Decimal(int(log['data'], 16)) / 10**decimals)
                  if not nft and decimals is not None else None)
        direction = ('internal' if sender in holders and receiver in holders else
                     'out' if sender in holders else 'in')
        unique[identity] = dict(chain=chain, block=log['block_number'],
            transaction=log['transaction_hash'], log_index=log['log_index'],
            token=token, symbol=symbol, decimals=decimals, amount=amount,
            sender=sender, receiver=receiver, direction=direction,
            kind='nft' if nft else 'erc20', raw_log=log)
    return list(unique.values())


def classify(row, *, income_routes, venue_tokens, bridge_events, swap_events, rebate_receipts=None):
    c, t, sender, receiver = (row[k] for k in ('chain', 'token', 'sender', 'receiver'))
    event = (c, row['transaction'], row['log_index'])
    if (c, t) in SPAM:
        return 'excluded_unsolicited', 'Reviewed airdrop or counterfeit token; no price inferred'
    if c == 'base' and (t == NEW_BASE_VAULT or
                       (t == USDC_BASE and NEW_BASE_VAULT in (sender, receiver))):
        return 'missing_venue', 'Unconfigured Grove x Steakhouse Base vault'
    if c == 'ethereum' and t == USDC_ETH and sender == COINBASE_PAYER:
        receipt = (rebate_receipts or {}).get(row['transaction'])
        if receipt is not None:
            if (receiver != '0x491edfb0b8b608044e227225c715981a30f3a44e'
                    or Decimal(row['amount']) != Decimal(receipt['amount'])):
                raise ValueError('Approved partner rebate differs from actual receipt')
            return 'approved_partner_rebate', (
                f"Historical {receipt['asset']} partner rebate; approved October MSC allocation, not Apollo principal"
            )
        return 'unresolved_receipt', 'Shared Coinbase Prime payer; no transaction-specific economic association'
    if c == 'monad' and t == USDC_MONAD and row['direction'] == 'out':
        return 'unresolved_outflow', 'Real USDC, signed by configured Monad holder; destination unclassified'
    route = income_routes.get((c, t, sender, receiver))
    if route:
        return 'booked_distribution', route
    if event in bridge_events:
        return 'verified_bridge', 'CCTP v2 source funding, Iris nonce, destination mint verified; not income'
    if event in swap_events:
        return 'swap_execution_review', 'Verified AUSD/USDC trade; reconcile execution PnL separately from LP income'
    if c == 'base' and (t in {
            '0xbeef0e0834849acc03f0089f01f4f1eeb06873c9',
            '0xbeef2d50b428675a1921bc6bbf4bfb9d8cf1461a'} or
            (t == USDC_BASE and any(x in venue_tokens for x in ((c, sender), (c, receiver))))):
        return 'vault_cash_reconciliation', 'E19/E23: actual exit cash differs from booked NAV-derived return'
    if c == 'ethereum' and t == USDC_ETH:
        if sender == '0xcfc0f98f30742b6d880f90155d4ebb885e55ab33':
            return 'existing_route_identified', 'E10 BUIDL cash linked to two requests in booking snapshot'
        if sender == '0x0665fde254598e307b63f3aae3ccd881a62d4be3':
            return 'existing_route_identified', 'E9 JTRSY redemption; cash included in capital-flow series'
        if '0x37305b1cd40574e4c5ce33f8e8306be057fd7341' in (sender, receiver):
            return 'existing_route_identified', 'LitePSM USDC/DAI capital conversion'
    if c == 'ethereum' and row['symbol'] in {'DAI', 'USDS'}:
        return 'existing_route_identified', 'Debt buffer and USDS/DAI conversion; capital, not venue income'
    if c == 'ethereum' and POOL in (sender, receiver):
        return 'existing_route_identified', 'E12 LP liquidity/fee leg; separate authenticated swaps above'
    if c == 'ethereum' and any(a in (sender, receiver) for a in (
            '0xab8306d9fefbe8183c3c59ca897a2e0eb5befe67',
            '0x748b66a6b3666311f370218bc2819c0bee13677e',
            '0xbe009e220f0c8fc5749ac4632a3e51706f1dca4f')):
        return 'existing_route_identified', 'Agora subscription/redemption boundary; principal route'
    if (c, t) in venue_tokens:
        return 'existing_route_identified', venue_tokens[(c, t)]
    if any((c, a) in venue_tokens for a in (sender, receiver)):
        return 'existing_route_identified', 'Cash leg with a configured venue'
    return 'unresolved', 'No reviewed booking route; retained for review'


def audit(directory):
    directory = Path(directory)
    scope = json.loads((directory / 'scope.json').read_text())
    prime = load_prime_by_id('grove', config_dir=ROOT / 'config')
    expected = {c.value: {a.hex} for c, a in prime.alm.items()}
    expected['ethereum'].add(prime.basin_idle_usds.holder.hex)
    if (scope['period'] != '2026-10' or set(scope['chains']) != set(expected)
            or any(set(scope['chains'][c]['holders']) != holders for c, holders in expected.items())):
        raise ValueError('Audit must include every configured Grove ALM and the Diamond PAU')
    metadata, venue_tokens, income_routes = {}, {}, {}
    for venue in prime.venues:
        # Cash and skip-only reporting rows reuse token addresses. They must
        # not turn every USDC leg into a fictitious Warehouse/EOA position.
        if not venue.skip and venue.pricing_category.value not in {'A', 'EOA'}:
            venue_tokens[(venue.chain.value, venue.token.address.hex)] = f'{venue.id}: {venue.label}'
        for token in (venue.token, venue.underlying):
            if token:
                metadata[(token.chain.value, token.address.hex)] = (token.symbol, token.decimals)
        for source in venue.cash_distributions:
            chain = source.chain or venue.chain
            income_routes[(chain.value, source.token.hex, source.payer.hex,
                           prime.alm[chain].hex)] = venue.id
    for chain, tokens in PAR_STABLES_BY_CHAIN.items():
        metadata.update({(chain.value, '0x' + a.hex()): info for a, info in tokens.items()})
    for chain, token in USDS_BY_CHAIN.items():
        metadata[(chain.value, token.address.hex)] = (token.symbol, token.decimals)
    for entry in json.loads((directory / 'token-metadata.json').read_text()):
        calls = entry['calls']
        if isinstance(calls.get('decimals()'), int):
            metadata[(entry['chain'], entry['token'])] = (calls['symbol()'], calls['decimals()'])
    bridges = json.loads((directory / 'cctp-v2-proofs.json').read_text())
    rebates = json.loads((directory / 'approved-partner-rebates.json').read_text())
    rebate_receipts = {r['tx']: r for r in rebates['receipts']}
    total = sum((Decimal(r['amount']) for r in rebates['receipts']), Decimal(0))
    if (rebates['status'] != 'operator_approved' or rebates['settlement_month'] != '2026-10'
            or len(rebate_receipts) != len(rebates['receipts'])
            or total != Decimal(rebates['total_received'])
            or total != Decimal(rebates['sky']['total']) + Decimal(rebates['grove']['total'])):
        raise ValueError('Invalid approved partner rebate allocation')
    bridge_events = set()
    for proof in bridges['proofs']:
        bridge_events.add((proof['source_chain'], proof['source_tx'], proof['payment_log']))
        bridge_events.add((proof['destination_chain'], proof['destination_tx'], proof['mint_log']))
    swaps = json.loads((directory / 'swap-executions.json').read_text())
    swap_events = {('ethereum', proof['tx'], index) for proof in swaps['events']
                   for index in proof['verified_transfer_events']}
    rows, captures = [], {}
    for chain, chain_scope in scope['chains'].items():
        with gzip.open(directory / f'{chain}-raw.json.gz', 'rt') as source:
            capture = json.load(source)
        if capture['scope'] != chain_scope:
            raise ValueError('Capture scope changed')
        captures[chain] = capture
        rows.extend(transfers(chain, capture, metadata))
    verify_bridges(directory, captures, bridges['proofs'])
    for row in rows:
        row['status'], row['booking_route'] = classify(row, income_routes=income_routes,
            venue_tokens=venue_tokens, bridge_events=bridge_events, swap_events=swap_events,
            rebate_receipts=rebate_receipts)
    rebate_rows = [r for r in rows if r['status'] == 'approved_partner_rebate']
    matched_rebates = {r['transaction'] for r in rebate_rows}
    if matched_rebates != set(rebate_receipts) or len(rebate_rows) != len(rebate_receipts):
        raise ValueError('Approved rebate receipt missing from captured transfers')
    booking = json.loads((directory / 'booking-snapshot.json').read_text())
    by_venue = {v['venue_id']: Decimal(v['revenue']) for v in booking['venue_breakdown']}
    for venue in {r['booking_route'] for r in rows if r['status'] == 'booked_distribution'}:
        receipts = sum((Decimal(r['amount']) for r in rows
                        if r['status'] == 'booked_distribution' and r['booking_route'] == venue), Decimal(0))
        if receipts != by_venue[venue]:
            raise ValueError('Configured cash distributions differ from booked venue revenue')
    rows.sort(key=lambda r: (r['chain'], r['block'], r['log_index']))
    controls = json.loads((directory / 'balance-controls.json').read_text())
    token_controls = [r for r in controls if r['token'] != 'native']
    if any(r.get('residual_raw') != '0' for r in token_controls):
        raise ValueError('Token balance control failed')
    native, gas = verify_native(directory, captures, controls)
    result = {'period': scope['period'], 'scope': scope['scope'],
        'transfer_events': len(rows), 'status_counts': dict(Counter(r['status'] for r in rows)),
        'chain_counts': {c: sum(r['chain'] == c for r in rows) for c in scope['chains']},
        'token_balance_controls': len(token_controls), 'token_balance_residuals': 0,
        'approved_partner_rebates': rebates,
        'native_flows': native, 'gas_receipts': gas,
        'native_balance_controls': sum(r['token'] == 'native' for r in controls),
        'note': 'Identified routes are not a certification of complete financial booking. '
                'Native movements and gaps are reported separately in the audit note.',
        'transfers': rows}
    with gzip.open(directory / 'transfer-audit.json.gz', 'wt') as output:
        json.dump(result, output, indent=2)
    fields = [k for k in rows[0] if k != 'raw_log'] if rows else []
    with (directory / 'transfer-audit.csv').open('w') as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.input_dir)
    print(json.dumps({k: v for k, v in result.items() if k != 'transfers'}, indent=2))
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(args.input_dir.iterdir()) if p.is_file() and p.name != 'sha256.json'}
    (args.input_dir / 'sha256.json').write_text(json.dumps(hashes, indent=2) + '\n')


if __name__ == '__main__':
    main()
