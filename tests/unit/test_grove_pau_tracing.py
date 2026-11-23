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


def test_a_skipped_pau_venue_does_not_suppress_tracing():
    grove = load_prime_by_id('grove')
    skipped = replace(grove, venues=[replace(v, skip=True) if v.id == 'E44' else v for v in grove.venues])
    prime, _ = with_grove_pau_tracing(skipped, ())
    assert any(v.id == 'E12_PAU' for v in prime.venues)


def test_production_paths_never_enable_allocation_financing():
    """The October source seal allows allocation_capital.py changes because no
    settlement, daily or API path computes allocation financing. Keep it so."""
    import re
    from pathlib import Path
    root = Path(__file__).parents[2]
    pattern = re.compile(r'include_allocation_financing\s*=\s*True')
    offenders = [f'{p.relative_to(root)}:{n}'
                 for p in [*root.glob('src/**/*.py'), *root.glob('scripts/*.py')]
                 for n, line in enumerate(p.read_text().splitlines(), 1)
                 if pattern.search(line.split('#', 1)[0])]
    assert offenders == []
