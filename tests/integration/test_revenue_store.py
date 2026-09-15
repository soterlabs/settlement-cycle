from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import psycopg
import pytest

from settle.domain.period import Month, Period
from settle.domain.primes import Chain
from settle.revenue import store
from tests.integration.test_input_cache_postgres import database  # noqa: F401


@dataclass
class Pnl:
    prime_id: str
    period: Period
    pin_blocks_som: dict
    revenue: Decimal = Decimal('1.000000000000000001')

    @property
    def as_of(self):
        return self.period.end


def example(days=1):
    cutoff = datetime.now(UTC).date() - timedelta(days=days)
    return Pnl('obex', Period.from_month(Month(cutoff.year, cutoff.month),
               {Chain.ETHEREUM: 200}, as_of=cutoff), {Chain.ETHEREUM: 100})


def test_immutable_versions_idempotency_and_transaction_rollback(database):  # noqa: F811
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        pnl, versions = example(), store.Versions('code-a', 'config-a', '0')
        with conn.transaction():
            first = store.publish(conn, pnl, versions)
        with conn.transaction():
            assert store.publish(conn, pnl, versions) == first
        with pytest.raises(ValueError, match='different results'), conn.transaction():
            store.publish(conn, replace(pnl, revenue=Decimal('2')), versions)
        assert store.read(conn, 'obex')['result']['revenue'] == str(pnl.revenue)
        with conn.transaction():
            second = store.publish(conn, replace(pnl, revenue=Decimal('2')),
                                   replace(versions, inputs='corrected'))
        assert second != first
        assert store.read(conn, 'obex', revision=first)['result']['revenue'] == str(pnl.revenue)
        assert store.read(conn, 'obex')['revision_id'] == second
        assert len(store.revisions(conn, 'obex', pnl.as_of)) == 2
        assert store.read(conn, 'obex')['provisional'] is True
        with pytest.raises(RuntimeError), conn.transaction():
            store.publish(conn, pnl, replace(versions, code='rolled-back'))
            raise RuntimeError('failed before commit')
        assert len(store.revisions(conn, 'obex', pnl.as_of)) == 2
        assert store.read(conn, 'grove', revision=first) is None


def test_late_backfill_cannot_replace_a_newer_cutoff(database):  # noqa: F811
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        versions = store.Versions('code', 'config', '0')
        latest = store.publish(conn, example(), versions)
        store.publish(conn, example(2), versions)
        assert store.read(conn, 'obex')['revision_id'] == latest
        rows = store.history(conn, 'obex', start=example(3).as_of, end=example().as_of)
        assert len(rows) == 2


def test_corrections_in_one_transaction_have_unambiguous_order(database):  # noqa: F811
    with psycopg.connect(database, autocommit=True) as conn:
        store.apply_schema(conn)
        pnl = example()
        with conn.transaction():
            ids = [store.publish(conn, replace(pnl, revenue=Decimal(i)),
                                 store.Versions('code', 'config', str(i))) for i in range(8)]
        assert store.read(conn, 'obex')['revision_id'] == ids[-1]
        assert [r['revision_id'] for r in store.revisions(conn, 'obex', pnl.as_of)] == ids[::-1]
