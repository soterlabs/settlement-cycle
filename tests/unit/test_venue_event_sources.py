from dataclasses import replace

from settle.compute.monthly_pnl import Sources
from settle.domain.config import load_prime_by_id
from settle.normalize.sources.dune_balances import DuneBalanceSource
from settle.normalize.sources.hypersync_balances import HyperSyncBalanceSource
from settle.normalize.venue_sources import for_venue


def test_cutover_is_per_venue_and_does_not_mutate_shared_sources():
    venue = load_prime_by_id("grove").venues[0]
    shared = Sources()
    migrated = for_venue(shared, replace(venue, event_source="hypersync"))
    assert isinstance(migrated.balance, HyperSyncBalanceSource)
    assert shared.balance is None
    assert for_venue(shared, replace(venue, event_source="dune")) is shared


def test_explicit_fixture_sources_are_preserved():
    venue = replace(load_prime_by_id("grove").venues[0], event_source="hypersync")
    fixture = object()
    assert for_venue(Sources(balance=fixture), venue).balance is fixture
    oracle = DuneBalanceSource()
    assert for_venue(Sources(balance=oracle), venue).balance is oracle


def test_live_atoken_callback_is_installed_for_both_categories():
    from settle.domain.primes import PricingCategory
    venue = load_prime_by_id('osero').venues[0]
    for category in (PricingCategory.AAVE_ATOKEN, PricingCategory.SPARKLEND_SPTOKEN):
        routed = for_venue(Sources(), replace(venue, pricing_category=category, event_source='hypersync'))
        assert routed.atoken_event_blocks.__self__ is routed.balance
        supplied = HyperSyncBalanceSource(fetch_logs=lambda *args: [])
        routed = for_venue(Sources(balance=supplied), replace(venue, pricing_category=category, event_source='hypersync'))
        assert routed.atoken_event_blocks.__self__ is supplied


def test_atoken_fixture_callback_and_offline_balance_are_preserved():
    venue = replace(load_prime_by_id('osero').venues[0], event_source='hypersync')
    def callback(*args):
        return [123]
    assert for_venue(Sources(atoken_event_blocks=callback), venue).atoken_event_blocks is callback
    assert for_venue(Sources(balance=object()), venue).atoken_event_blocks is None


def test_other_categories_do_not_get_atoken_callback():
    from settle.domain.primes import PricingCategory
    venue = load_prime_by_id('osero').venues[0]
    for category in PricingCategory:
        if category in {PricingCategory.AAVE_ATOKEN, PricingCategory.SPARKLEND_SPTOKEN}:
            continue
        assert for_venue(Sources(), replace(venue, pricing_category=category, event_source='hypersync')).atoken_event_blocks is None
