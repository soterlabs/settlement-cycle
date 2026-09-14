from dataclasses import replace

from settle.compute.monthly_pnl import Sources
from settle.domain.config import load_prime_by_id
from settle.normalize.sources.dune_balances import DuneBalanceSource
from settle.normalize.sources.hypersync_balances import HyperSyncBalanceSource
from settle.normalize.venue_sources import for_venue


def test_cutover_is_per_venue_and_does_not_mutate_shared_sources():
    venue = load_prime_by_id("grove").venues[0]
    shared = Sources(balance=DuneBalanceSource())
    migrated = for_venue(shared, replace(venue, event_source="hypersync"))
    assert isinstance(migrated.balance, HyperSyncBalanceSource)
    assert isinstance(shared.balance, DuneBalanceSource)
    assert for_venue(shared, replace(venue, event_source="dune")) is shared


def test_explicit_fixture_sources_are_preserved():
    venue = replace(load_prime_by_id("grove").venues[0], event_source="hypersync")
    fixture = object()
    assert for_venue(Sources(balance=fixture), venue).balance is fixture
