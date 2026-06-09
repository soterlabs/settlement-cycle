# Spark

Spark is a prime agent in the Sky ecosystem.

## Key contracts

| Contract | Address | Role |
|----------|---------|------|
| ALM Proxy | `0x1601843c5e9bc251a3272907010afa41fa18347e` | Cross-chain hub for Spark allocations |
| Urn | `0x691a6c29e9e96dd897718305427ad5d534db16ba` | Borrower position in the Vat (ALLOCATOR-SPARK-A); records ilk debt. Holds $0. |
| SubProxy | `0x3300f198988e4C9C63F75dF86De36421f06af8c4` | Holds idle USDS (~$30–37M throughout 2026) earning SSR+agent_rate. Distinct from the urn — confirmed on-chain. |

## Ilk

- **Name:** `ALLOCATOR-SPARK-A`
- **bytes32:** `0x414c4c4f4341544f522d535041524b2d41000000000000000000000000000000`

## Dune query parameters

All queries are shared parameterized templates in [`queries/`](../../queries/) (in this repo). Fill in Spark-specific parameters:

| Parameter | Value |
|-----------|-------|
| `ilk_bytes32` | `0x414c4c4f4341544f522d535041524b2d41000000000000000000000000000000` |
| `subproxy_address` | `0x3300f198988e4C9C63F75dF86De36421f06af8c4` |
| `alm_proxy_address` | `0x1601843c5e9bc251a3272907010afa41fa18347e` |
| `venue_token_address` | per-venue (see `config/spark.yaml`) |
| `start_date` | 2024-11-18 (first frob) |
| `calendar_start_date` | per settlement period |

## Fixture refresh

Spark's offline replay fixtures live in [`tests/fixtures/spark_2026_q1/`](../../tests/fixtures/spark_2026_q1/) and feed `scripts/run_spark_2026.py` via `tests/fixtures/spark_fixture_loader.py`. Refresh them for a new settlement period via:

```bash
DUNE_API_KEY=... python scripts/extend_spark_fixtures.py
```

That script re-runs the published Dune queries listed in its module docstring (debt timeseries, EoD blocks for Eth/Avax/L2s, SubProxy USDS+sUSDS timeseries) and writes the JSON back into `tests/fixtures/spark_2026_q1/`. Bump `MAY_31_PIN_BLOCK_EXACT["ethereum"]` and `MAY_31_PIN_BLOCK` in that script for new EoM boundaries.

The SubProxy USDS/sUSDS capture closes a fixture-loader gap surfaced in PR #124 (2026-06-09) — without it, mid-period Sky governance allocations to the SubProxy are masked, understating `agent_rate` by ~3% (~$18.6K Jan–May 2026). See PRD §17.13 for the full investigation.

## PnL summary

Pending.
