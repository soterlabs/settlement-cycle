"""November review: net pass-through ETH principal FX, retain interest and own gains.

Retained v3.10 selections keep their original presentation and recognition.
Raw price revaluation metrics remain auditable; this policy removes paired
Savings principal translation from gross income/funding, not genuine yield.
"""
from copy import deepcopy

from .statement_rollups import combine


def prepare(statement, supplements=()):
    if 'review_policy' in statement:
        raise ValueError('Spark review policy has already been applied')
    lookup = {row['id']: row for row in statement['lines']}
    for cell in lookup['expenses.compensation.spk']['values'].values():
        if cell.get('value') is not None:
            cell['source_refs'] = [statement['expense_input_revision']]
    old = lookup['gains.eth_weth']
    free = deepcopy(old)
    free.update(id='revaluation.free_eth', label='Spark-owned ETH / WETH gains')
    statement['lines'].append(free)
    retained = dict(id='revaluation.retained_reserves', label='Net Spark-owned reserve gains',
                    aggregation='subtotal', components=[
                        dict(id='revaluation.alm_assets', sign=1),
                        dict(id='revaluation.savings_liabilities', sign=1)], values={})
    statement['lines'].append(retained)
    other = dict(id='gains.other_reserves', label='Other treasury reserve gains', aggregation='subtotal',
                 components=[dict(id='revaluation.ops_reserves', sign=1)],
                 values={})
    statement['lines'].append(other)
    old.update(aggregation='subtotal', components=[dict(id=free['id'], sign=1), dict(id=retained['id'], sign=1)])
    lookup['gains.sparklend_nonstable']['include_in_income'] = False
    lookup['gains.sparklend_nonstable']['statement_hidden'] = True
    lookup['funding.savings.revaluation']['statement_hidden'] = True
    lookup['funding.savings.revaluation']['include_in_income'] = False
    lookup['revaluation.savings_liabilities']['funding_component'] = False
    lookup['rewards.unattributed_sparklend_receipts']['statement_hidden'] = True
    funding = lookup['funding.savings']
    funding['components'] = [c for c in funding['components'] if c['id'] != 'funding.savings.revaluation']
    for period in statement['periods']:
        month = period['key']
        retained['values'][month] = combine([(c['id'], c['sign'], lookup[c['id']]['values'][month]) for c in retained['components']])
        other['values'][month] = combine([('revaluation.ops_reserves', 1, lookup['revaluation.ops_reserves']['values'][month])])
        old['values'][month] = combine([(free['id'], 1, free['values'][month]), (retained['id'], 1, retained['values'][month])])
        funding['values'][month] = combine([(c['id'], c['sign'], lookup[c['id']]['values'][month]) for c in funding['components']])
        if period['state'] == 'future':
            for row in (retained, old, funding, other):
                row['values'][month].update(status='future', value=None, known_value=None)
    statement['pending_expense_inputs'] = [r for r in statement.get('pending_expense_inputs', [])
        if any(p['state'] != 'future' and lookup[r['id']]['values'][p['key']].get('value') is None
               for p in statement['periods'])]
    statement['spark_sde_cash'] = [dict(month=p['key'], through=saved['cutoff'], source_ref=saved['revision_id'],
        **deepcopy(saved['document'].get('sde_cash', {'status': 'unavailable', 'entries': [], 'errors': {'source': 'SDE liquidity principal not published'}})))
        for p, saved in zip(statement['periods'], supplements, strict=True) if saved]
    for proof in statement['spark_sde_cash']:
        expected = next(p['expected_cutoff'] for p in statement['periods'] if p['key'] == proof['month'])
        if proof['through'] != expected:
            proof['status'] = 'partial'
            proof.setdefault('errors', {})['cutoff'] = 'Retained principal source stops before the selected cutoff'
    if any(row['id'].startswith('fees.curator.') for row in statement['lines']):
        from .spark_curator_attribution import apply as attribute_fees
        attribute_fees(statement, supplements)
    # Only newly retained sources opt into this component; older pinned
    # selections keep their prior shape and amounts.
    if any(saved and "sky_staking" in saved["document"] for saved in supplements):
        from .financial import metric
        from .financial_spark import saved_component
        row = dict(id="investment.sky_staking", label="SKY staking rewards",
                   family="SKY staking", aggregation="leaf", values={})
        statement["sky_staking_cash_receipts"] = []
        for period, saved in zip(statement["periods"], supplements, strict=True):
            month = period["key"]
            raw = saved["document"].get("sky_staking", {}) if saved else {}
            row["values"][month] = (
                {**metric(reason="future_period"), "status": "future"}
                if period["state"] == "future" else
                saved_component(raw, "value", saved, recognition="receipt", expected=period["expected_cutoff"])
                if saved else metric(reason="staking_receipts_not_published"))
            if saved and raw.get("status") in ("available", "partial") and saved["cutoff"] == period["expected_cutoff"]:
                statement["sky_staking_cash_receipts"].extend(
                    dict(receipt, month=month, source_ref=saved["revision_id"])
                    for receipt in raw.get("receipts", []))
        statement["lines"].append(row)
    statement['review_policy'] = {
        'eth': 'Exclude matched Savings-funded principal FX; retain daily translated interest and residual Spark-owned reserve FX',
        'hidden_dust': 'Retained in API allocation revenue and totals; excluded only from statement rows',
    }


def presentation(statement):
    hidden = {row['id'] for row in statement['lines'] if row.get('statement_hidden')}
    rows = statement.get('statement_rows', [])
    group = 'statement.family.other_reviewed_vaults'
    remove = {row['id'] for row in rows if row.get('metric_id') in hidden or row['id'] == group or row.get('parent') == group}
    while True:
        children = {row['id'] for row in rows if row.get('parent') in remove}
        if children <= remove:
            break
        remove.update(children)
    rows = [row for row in rows if row['id'] not in remove]
    if not any(row.get('metric_id') == 'gains.other_reserves' for row in rows):
        host = next(row for row in rows if row.get('metric_id') == 'statement.gains')
        rows.insert(rows.index(host) + 1, dict(id='gains.other_reserves', label='Other treasury reserve gains',
            parent=host['id'], depth=host['depth'] + 1, kind=None, metric_id='gains.other_reserves'))
    if any(row["id"] == "investment.sky_staking" for row in statement["lines"]):
        host = next(row for row in rows if row.get("metric_id") == "statement.interest")
        rows.insert(rows.index(host) + 1, dict(id="investment.sky_staking", label="SKY staking rewards",
            parent=host["id"], depth=host["depth"] + 1, kind=None, metric_id="investment.sky_staking"))
    statement['statement_rows'] = rows
