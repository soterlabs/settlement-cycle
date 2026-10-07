"""Receipt-basis recognition of individually verified protocol refunds.

DssBlow2.blow() joins its FULL token balance to Vow:
https://github.com/sky-ecosystem/dss-blow2/blob/master/src/DssBlow2.sol
A receipt creates income while held there. Its first subsequent Blow clears
that receivable and offsets the cash surplus return, including across months.
"""
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import yaml

from ...extract import hypersync, hypersync_store
from ._hypersync_common import _addr_topic, _evt, _word

CONFIG = Path(__file__).resolve().parents[4] / "config" / "non_msc.yaml"
WAD = Decimal(10**18)


def refund_adjustments(month, pin_block, *, entries=None, fetch=None):
    """Return in-month recognition/settlement entries, each bound to a tx/log.

    `expected_amount` is an assertion, never the source of booked revenue.
    Historical receipts are read even in later months to suppress double count
    when a previously accrued refund is finally joined into the surplus buffer.
    """
    entries = (yaml.safe_load(CONFIG.read_text()).get("accrued_refunds", [])
               if entries is None else entries)
    fetch = fetch or hypersync_store.fetch_logs
    identities = [(e['transaction'].lower(), e['log_index']) for e in entries]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate accrued refund receipt")
    out = []
    settlements = {}
    for entry in entries:
        if pin_block < entry['block']:
            continue
        token, holder = entry['token'].lower(), entry['holder'].lower()
        fields = [*hypersync._DEFAULT_LOG_FIELDS, 'transaction_hash']
        receipts = fetch('ethereum', [{
            'address': [token], 'topics': [[_evt('Transfer(address,address,uint256)')],
                [_addr_topic(entry['sender'])], [_addr_topic(holder)]],
        }], entry['block'], entry['block'], log_fields=fields)
        matches = [r for r in receipts if r.transaction_hash == entry['transaction'].lower()
                   and r.log_index == entry['log_index']]
        if len(matches) != 1:
            raise ValueError(f"Missing or ambiguous refund receipt {entry['id']}")
        receipt = matches[0]
        amount = Decimal(_word(receipt.data, 0)) / WAD
        if amount <= 0 or amount != Decimal(entry['expected_amount']):
            raise ValueError(f"Refund receipt amount mismatch {entry['id']}")
        received = datetime.fromtimestamp(receipt.block_time, UTC).date()
        base = {'id': entry['id'], 'label': entry['label'], 'receipt_transaction': receipt.transaction_hash,
                'receipt_log_index': receipt.log_index, 'token': token, 'custody_holder': holder}
        if month.first_day <= received <= month.last_day:
            out.append({**base, 'date': str(received), 'kind': 'recognition',
                        'transaction': receipt.transaction_hash, 'amount': amount})
        blows = fetch('ethereum', [{'address': [holder], 'topics': [
            [_evt('Blow(address,uint256)')], [_addr_topic(token)]]}],
            entry['block'], pin_block, log_fields=fields)
        later = sorted((r for r in blows if (r.block_number, r.log_index) >
                        (receipt.block_number, receipt.log_index)),
                       key=lambda r: (r.block_number, r.log_index))
        if not later:
            continue
        settlement = later[0]
        key = (settlement.transaction_hash, settlement.log_index)
        cumulative = settlements.get(key, Decimal(0)) + amount
        if cumulative > Decimal(_word(settlement.data, 0)) / WAD:
            raise ValueError("Blow amount does not cover accrued refunds")
        settlements[key] = cumulative
        settled = datetime.fromtimestamp(settlement.block_time, UTC).date()
        if month.first_day <= settled <= month.last_day:
            out.append({**base, 'date': str(settled), 'kind': 'settlement_offset',
                        'transaction': settlement.transaction_hash, 'amount': -amount})
    return out
