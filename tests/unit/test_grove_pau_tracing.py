"""Grove's tracing-only E12_PAU must not duplicate a configured PAU LP venue."""
from dataclasses import replace

from settle.domain.config import load_prime_by_id
from settle.normalize.allocation_capital import with_grove_pau_tracing


def test_configured_pau_venue_owns_the_nft_capital():
    grove = load_prime_by_id('grove')
    assert any(v.id == 'E44' for v in grove.venues)
    prime, analytics = with_grove_pau_tracing(grove, ())
    # E44 tracks the PAU-held NFT; a second synthetic account would charge it twice.
    assert [v.id for v in prime.venues].count('E12_PAU') == 0
    assert analytics == ()


def test_tracing_row_is_still_added_without_a_configured_pau_venue():
    grove = load_prime_by_id('grove')
    legacy = replace(grove, venues=[v for v in grove.venues if v.id != 'E44'])
    prime, analytics = with_grove_pau_tracing(legacy, ('X',))
    pau = next(v for v in prime.venues if v.id == 'E12_PAU')
    assert str(pau.holder_override).lower() == '0x0dcd9298e163dfd3c0b5b00f0d9093c36e40a153'
    assert pau.notional_principal_usd is None
    assert analytics == ('X', 'E12_PAU')
    # Other primes are untouched.
    osero = load_prime_by_id('osero')
    assert with_grove_pau_tracing(osero, ()) == (osero, ())
