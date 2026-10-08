"""Read-only inventory of Spark's USCC issuer boundary; no inferred cash links."""
from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

HOLDER = '1601843c5e9bc251a3272907010afa41fa18347e'
ENTRY = 'db48ac0802f9a79145821a5430349caff6d676f7'
USCC = '0x14d60e7fdc0d71d8611742720e4c50e7a974020c'
USDC = '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48'
TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
# Reviewed closed subscription groups, not a runtime nearest-date matcher.
PAIRS = (
    (('0xfc298a3643627678a14071b5b5e7a9ddd2c8853a1030bd364a200fd59bcf1981',
      '0x5ac3e1a191b96b810beb7e5b5ddf52e6c4eeab1e0bd2c7b20526be4be970660e'),
     '0x9701dd940f4b4b211d9328cf6161c24078487f193ec56d9d8f055cb4bc325200'),
    (('0xd9218cf442a60d710849f35fb9403fd66da840faaf41f0d1f0703d55156a0397',),
     '0x0a286c4057964fa5b275451fad781e584ac76ed3ad6dbd7e7aae73ff041725b5'),
    (('0x76a6c50cc9909525a67fc286f4c84477f50976fe9bac7be78b37c6c51143ffa6',),
     '0x4f5c87581ef8b9a636aa07dde269910e3cb4389a9f1dd9ec04de3bdf6cfb53a6'),
)


def inventory(fixture):
    rows = fixture['issuer_transactions']
    identities = [(r['transaction_hash'], r['log_index']) for r in rows]
    if len(set(identities)) != len(identities):
        raise ValueError('Duplicate USCC evidence')
    transfers = [r for r in rows if r['topic0'] == TRANSFER]
    payments = {r['transaction_hash']: r for r in transfers if r['address'] == USDC
                and r['topic1'].endswith(HOLDER) and r['topic2'].endswith(ENTRY)}
    issues = {r['transaction_hash']: r for r in transfers if r['address'] == USCC
              and int(r['topic1'], 16) == 0 and r['topic2'].endswith(HOLDER)}
    burns = [r for r in transfers if r['address'] == USCC
             and r['topic1'].endswith(HOLDER) and int(r['topic2'], 16) == 0]
    def units(r):
        return D(int(r['data'], 16)) / 10**6
    previous = 0
    subscriptions = []
    if set(payments) != {tx for txs, _ in PAIRS for tx in txs} or set(issues) != {tx for _, tx in PAIRS}:
        raise ValueError('USCC subscription inventory differs from reviewed groups')
    for txs, mint in PAIRS:
        cash = [payments[tx] for tx in txs]
        issue = issues[mint]
        if not all(previous < r['block_number'] < issue['block_number'] for r in cash):
            raise ValueError('USCC subscriptions overlap or have invalid chronology')
        paid, shares = sum((units(r) for r in cash), D(0)), units(issue)
        subscriptions.append({'payments': list(txs), 'issuance': mint, 'cash_paid': paid,
                              'shares_received': shares, 'implied_cash_per_share': paid / shares})
        previous = issue['block_number']
    received = sum((units(r) for r in issues.values()), D(0))
    burned = sum((units(r) for r in burns), D(0))
    if len(burns) != 3 or received != burned:
        raise ValueError('USCC token exit is not fully observed')
    candidates = fixture['unassigned_cash_candidates']
    if any(r['address'] != USDC or r['topic0'] != TRANSFER or not r['topic2'].endswith(HOLDER)
           for r in candidates):
        raise ValueError('Candidate is not a USDC receipt by Spark ALM')
    return {'status': 'evidence inventory; no cash attribution or ledger mutation',
            'subscriptions': subscriptions, 'total_paid': sum((s['cash_paid'] for s in subscriptions), D(0)),
            'total_shares_received': received, 'total_shares_burned': burned,
            'ending_token_units': received - burned,
            'burns': [{'tx': r['transaction_hash'], 'block': r['block_number'], 'shares': units(r)} for r in burns],
            'unassigned_cash_candidates': [{'tx': r['transaction_hash'], 'block': r['block_number'],
                 'payer': '0x' + r['topic1'][-40:], 'amount': units(r), 'attributed_to_uscc': False} for r in candidates],
            'candidate_cash_total': sum((units(r) for r in candidates), D(0)),
            'scope': 'No pricing, revenue, global debt, capital basis, or settlement changes'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.fixture.read_bytes()
    result = inventory(json.loads(raw))
    result['fixture_sha256'] = hashlib.sha256(raw).hexdigest()
    args.output.write_text(json.dumps(result, default=str, indent=2) + '\n')


if __name__ == '__main__':
    main()
