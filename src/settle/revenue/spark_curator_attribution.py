"""Fee receipts on the prime's own capital are a supply rebate, not external fees."""
from copy import deepcopy

from .financial import metric
from .financial_spark import saved_component
from .statement_rollups import combine


def apply(statement, supplements):
    lookup = {row['id']: row for row in statement['lines']}
    venues = [row['id'].split('.')[-1] for row in statement['lines'] if row['id'].startswith('fees.curator.')]
    for venue in venues:
        native = lookup.get('investment.venue.' + venue)
        if native is None:
            raise ValueError('Curator vault has no native allocation row')
        raw = deepcopy(native)
        raw.update(id='audit.net_supply.' + venue, label='Net vault NAV yield: ' + venue, statement_hidden=True)
        rebate = dict(id='audit.self_curation.' + venue,label='Self-curation rebate: ' + venue,
                      aggregation='leaf',values={},statement_hidden=True)
        unresolved = dict(id='audit.unallocated_curation.' + venue,label='Known fees awaiting external/self attribution: ' + venue,
                          aggregation='leaf',values={},statement_hidden=True,unallocated_fee_income=True)
        statement['lines'].extend([raw,rebate,unresolved])
        native['components'] = [dict(id=raw['id'],sign=1),dict(id=rebate['id'],sign=1)]
        fee = lookup['fees.curator.' + venue]
        for period,saved in zip(statement['periods'],supplements,strict=True):
            month=period['key'];gross=deepcopy(fee['values'][month])
            source = saved['document'].get('curator_attribution',{}).get(venue,{}) if saved else {}
            attribution = source.get('attribution',{})
            if period['state']=='future':
                external={**metric(reason='future_period'),'status':'future'}
                own=deepcopy(external);unknown=deepcopy(external)
            elif attribution.get('status')=='available':
                def cell(field):
                    return saved_component({**attribution,'value':attribution[field]},'value',saved,
                                           expected=period['expected_cutoff'])
                external=cell('external_accrued_usd');own=cell('self_rebate_accrued_usd')
                unknown=saved_component({'status':'available','value':'0','quality':'supported',
                    'basis':'all_curator_receipts_attributed'},'value',saved,expected=period['expected_cutoff'])
                for row in statement.get('spark_fee_ledger',[]):
                    if row['venue']==venue and row['month']==month:
                        row['attribution']=deepcopy(attribution)
                        row['gross_fee_accrued_usd']=row['accrued_usd']
            else:
                external=metric(reason='external_vs_self_curation_attribution_not_published')
                own=metric(reason='self_curation_rebate_not_published')
                # Preserve known aggregate revenue without mislabelling the
                # gross receipts as known external fee income or inventing zero.
                unknown=gross
                if gross.get('value') is not None:
                    unknown.update(value=None,known_value=gross['value'],status='partial',quality='estimate',
                                   reason='gross_fee_income_known_but_economic_bucket_unallocated')
            fee['values'][month]=external;rebate['values'][month]=own;unresolved['values'][month]=unknown
            native['values'][month]=combine([(raw['id'],1,raw['values'][month]),(rebate['id'],1,own)])
            if period['state']=='future':native['values'][month].update(status='future',value=None,known_value=None)
    # Recompute aliases after the economic venue leaves change. These are
    # alternate views, not additional rows in the allocation revenue sum.
    lookup={row['id']:row for row in statement['lines']}
    affected = {'investment.venue.' + venue for venue in venues} | {'fees.curator.' + venue for venue in venues}
    while True:
        parents = {key for key,row in lookup.items() if any(c['id'] in affected for c in row.get('components', []))}
        if parents <= affected:
            break
        affected |= parents
    for period in statement['periods']:
        month=period['key'];visiting=set();done=set()
        def refresh(key):
            row=lookup[key]
            if key in done:return row['values'][month]
            if key in visiting:raise ValueError('Cyclic curator presentation dependency')
            visiting.add(key)
            if key in affected and row.get('components'):
                row['values'][month]=combine([(c['id'],c['sign'],refresh(c['id'])) for c in row['components']])
                if period['state']=='future':row['values'][month].update(status='future',value=None,known_value=None)
            visiting.remove(key);done.add(key);return row['values'][month]
        for key in lookup:
            if key in affected:refresh(key)
