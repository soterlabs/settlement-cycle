"""Extract — pull raw data from Dune, RPC, off-chain APIs.

No business logic lives here. Each extractor is wrapped by an on-disk cache keyed
by `(source, args, pin)` so repeated runs at the same pin are free.
"""
