"""Live point-in-time snapshot — reproduces BA labs `stars-api` numbers from
raw on-chain primitives. Distinct from the monthly settlement pipeline (which
computes period revenue): a snapshot is the prime's balance sheet at one block.

Use:
    from settle.snapshot import compute_snapshot
    snap = compute_snapshot(prime, sources=Sources())
    print(snap.assets, snap.debt, snap.nav)

Or via CLI:
    python -m settle snapshot --prime grove
"""

from .types import Snapshot, VenueSnapshot, IdleHolding
from .compute import compute_snapshot

__all__ = ["Snapshot", "VenueSnapshot", "IdleHolding", "compute_snapshot"]
