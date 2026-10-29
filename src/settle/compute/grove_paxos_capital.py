"""Reviewed Paxos boundary receipts for pinned Grove capital histories.

See normalize/allocation_paxos.py for the precise authorizing spell and policy.
Actual USDC transfers are retained in tests/fixtures/grove_paxos_boundary_events.json.
"""
from decimal import Decimal

from ..normalize.allocation_paxos import link_paxos_boundary

EVENTS = (
    ('0xb520cdadba8b42d6daf5c3dbfe16b3c4b1eb58e49e2b10d127302e7da867b72b', 25582337, 1784651807, 862, Decimal('100')),
    ('0x284b48080c469e0e9b2a0943429ebd4f01005cd739ee7b34de364c4e2375f940', 25639579, 1785341207, 156, Decimal('1000')),
    ('0x59610c6694d893c41b7e5b74a98090df25a7ab8c3dcfb733e931a6b2bfa4ae5b', 25639859, 1785344567, 131, Decimal('2499000')),
    ('0xc7ca8935e6006a2da68f2c3b12067519ef876adf42a6fcb8fdb3e6ca85390d72', 25640204, 1785348707, 460, Decimal('5000000')),
    ('0x25feba48b385f3cd7189a7add24cb1a7d629e20a0b26b006f04f9d50c6d0ee27', 25641176, 1785360431, 853, Decimal('7500000')),
)


def link_grove_paxos_boundary(history):
    return link_paxos_boundary(history, EVENTS)
