"""Replay inputs for the settlement pipeline.

These are **production inputs**, not test artifacts: the Spark and Grove
runners are "replay" primes that read pre-captured Dune/RPC snapshots
(committed JSON under ``spark_2026_q1/`` and ``grove_2026_0X/``) instead of
hitting live sources on every run. The ``*_fixture_loader`` modules turn
those snapshots into ``settle.compute.Sources`` bundles, and
``mock_sources`` provides the in-memory ``Source`` implementations they use.

Capture/refresh scripts live in ``scripts/`` and in each snapshot dir's
``_capture_dune_fixtures.py``; pin blocks come from
``config/pin_blocks.yaml``.
"""
