"""Historical payment corrections with explicit current Sky expense recognition."""
from decimal import Decimal
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]


def apply_settlement_adjustments(provenance: dict) -> None:
    """Replace the payment bridge deterministically; never increment revenue.

    September 2026's four Skybase true-ups were explicitly authorized by the
    operator. Their earned periods precede September, so they do not enter
    September-earned DR. Previously unbooked items are marked explicitly to
    reduce Sky Net Revenue (and TMF inputs) in the settlement month.
    """
    if provenance.get('provisional'):
        return
    path = _REPO_ROOT / 'config/settlement_adjustments.yaml'
    config = yaml.safe_load(path.read_text()) if path.exists() else {}
    entries = (config or {}).get(provenance['month'], {}).get(provenance['prime_id'], [])
    ids = set()
    adjustments = []
    for entry in entries:
        amount = Decimal(entry['amount'])
        if type(entry.get('recognize_sky_expense', False)) is not bool:
            raise ValueError('recognize_sky_expense must be a boolean')
        if entry['id'] in ids or not amount.is_finite():
            raise ValueError('Duplicate or non-finite settlement adjustment')
        ids.add(entry['id'])
        adjustments.append({**entry, 'amount': str(amount)})
    provenance['settlement_adjustments'] = adjustments
    r = provenance['results']
    # Same economic settlement convention as summary.md: supply revenue
    # already excludes SDE, so deduct borrowing costs (Sky claim minus SDE).
    period_net = sum((Decimal(r.get(k, '0')) for k in (
        'prime_agent_revenue', 'agent_rate', 'distribution_rewards', 'chronicle_points', 'gar',
        'sde_revenue',
    )), Decimal(0)) - Decimal(r['sky_revenue'])
    total = sum((Decimal(e['amount']) for e in adjustments), Decimal(0))
    provenance['settlement_payment'] = {
        'period_net_revenue': str(period_net),
        'prior_period_adjustments': str(total),
        'total': str(period_net + total),
        'note': 'Historical true-ups are separate from period-earned revenue; flagged unbooked amounts are recognized as Sky expense in this settlement.',
    }
