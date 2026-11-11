"""Retained gateway evidence using the existing native-USDS claim mechanism."""
from dataclasses import asdict
from datetime import timedelta
from decimal import Decimal

from settle.controls.assets import canonical_hash, control_precision, require
from settle.domain.period import Period
from settle.extract import hypersync_store
from settle.extract.hypersync import LogRow
from settle.normalize.usds_bridge_transit import collect_transfers, daily_exposure


def capture_bridges(prime, period, *, fetch=None):
    fetch = fetch or hypersync_store.fetch_logs
    queries = []
    def record(chain, selections, start, end, **kwargs):
        rows = fetch(chain, selections, start, end, **kwargs)
        queries.append({'request': [chain, selections, start, end, kwargs],
                        'rows': [asdict(row) for row in rows]})
        return rows
    collect_transfers(prime, period, fetch=record)
    return {'schema': 1, 'source': 'native-usds-gateway-history', 'prime': prime.id,
            'queries': queries}


@control_precision
def check_bridge_states(prime, period, observations, control, source):
    require(source['schema'] == 1 and source['source'] == 'native-usds-gateway-history'
            and source['prime'] == prime.id, 'Wrong gateway evidence identity')
    queries = {}
    for query in source['queries']:
        key = canonical_hash(query['request'])
        require(key not in queries, 'Duplicate bridge query evidence')
        queries[key] = query['rows']
    consumed = set()
    def fetch(chain, selections, start, end, **kwargs):
        key = canonical_hash([chain, selections, start, end, kwargs])
        require(key in queries, 'Missing complete gateway query evidence')
        consumed.add(key)
        return [LogRow(**row) for row in queries[key]]
    transfers = collect_transfers(prime, period, fetch=fetch)
    require(consumed == set(queries), 'Gateway evidence contains an unscoped query')
    opening_period = Period(period.start - timedelta(days=1), period.end, period.pin_blocks)
    claims = daily_exposure(opening_period, transfers).to_dict('records')
    accounts = control['inventory']['accounts']
    for observation, claim in zip(observations, claims, strict=True):
        actual = {}
        for key, account in accounts.items():
            if 'bridge_usds' not in account['deduction_fields']:
                continue
            state = observation['assets'][key]
            value = Decimal(state['value_usd'])
            require(Decimal(state['deductions']['bridge_usds']) == value
                    and account['deduction_fields'] == ['bridge_usds'],
                    'A native-USDS bridge claim must be counted and exempted exactly once')
            chain = account['chain']
            actual[chain] = actual.get(chain, Decimal(0)) + value
        expected = {k: Decimal(v) for k, v in claim['source_chain_claims'].items()}
        require({k: v for k, v in actual.items() if v} == expected,
                f'Gateway/owned bridge claims disagree on {observation["day"]}')
