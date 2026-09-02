# Scoping: migrating `spark` onto Envio HyperSync

Status: **scoping only** (no spark code changed yet). Companion to
`MIGRATION_GUIDE.md`. obex/keel/skybase are fully migrated (debt + balance +
`position_balance`, all HyperSync); spark is the next and largest prime, and the
first that would exercise the dormant Cat C aToken reconstruction
(`extract/aave_reconstruct.py`, `sources/hypersync_atoken.py`).

## 1. Why spark is different (the real blocker)

obex/keel/skybase flip two–three YAML lines because they already run through the
live `_sources_from_prime` merge (`compute/monthly_pnl.py:112-125`) with an
empty `Sources()` from their runners. **Spark does not.**

- `scripts/run_spark_2026.py` builds a fully-populated fixture `Sources` via
  `build_spark_sources` (`tests/fixtures/spark_fixture_loader.py:96-451`) and
  passes **hardcoded** pin blocks (`PIN_BLOCKS_BY_MONTH`, run_spark_2026.py:89).
- Because every field is caller-set (non-`None`), a YAML `sources:` block would
  be a **no-op** for spark (caller wins in the merge). **`config/spark.yaml` has
  no `sources:` block** (confirmed).

So migrating spark ≠ "add two YAML lines." It means reworking the fixture runner
toward a live-sources path like `run_obex_2026.py`, either wholesale or by
swapping one fixture-backed source at a time behind a parity gate.

## 2. Chains — no blocker

`HYPERSYNC_HOSTS` (`extract/hypersync.py:28-37`) serves: ethereum, base,
arbitrum, optimism, unichain, avalanche_c (+ plume, monad). Spark's venue chains
are exactly **ethereum (38), base (6), arbitrum (5), optimism (3), unichain (3),
avalanche_c (3)** — all covered. (The `chain: see` grep hit is prose —
`spark.yaml:899` "see comment above S34" — not a real chain.)

## 3. What can and cannot move

HyperSync serves **logs / blocks / txns only — never `eth_call`.** So:

**Can migrate (event/log-derived):**
- `debt` — single Ethereum ilk `ALLOCATOR-SPARK-A`; lowest risk.
- `block_resolver` (Tier 1) — all 6 chains supported; removes the hardcoded pins.
- `position_balance` (Tier 2) — Cat C aTokens (§4), EOA principal, subproxy,
  Cat B 4626 *share counts*.
- `balance` — Cat A/B/E flow timeseries across all 6 chains (widest surface).

**Must stay on RPC (`eth_call`) — same as obex/keel/skybase left prices/oracles:**
- NAV oracles S19–S22 (`const_one`, Centrifuge `price_per_share_feed`, Chronicle)
- `convert_to_assets` (Cat B share→USD)
- PSM3 `shares()`/`convertToAssets()`
- Curve `slot0`/reserves and Uniswap V4 (Cat F: S24 / S25 / S61 / S62 / S66)
- `lending_idle_usds` deductions on S1/S4

**Stays on Dune:** SSR — no HyperSync backend exists (`registry.py:145` only
offers `dune`); the runner already reads it live.

## 4. The Cat C aToken venues (12) — first users of the dormant path

5 SparkLend spTokens + 7 Aave V3 aTokens (identical event layout, both handled by
`aave_reconstruct`):

| id | symbol | chain | kind |
|---|---|---|---|
| S1 | spUSDS | ethereum | SparkLend |
| S2 | spUSDC | ethereum | SparkLend |
| S3 | spUSDT | ethereum | SparkLend |
| S4 | spDAI | ethereum | SparkLend |
| S5 | spPYUSD | ethereum | SparkLend |
| S6 | aEthLidoUSDS | ethereum | Aave V3 |
| S7 | aEthUSDS | ethereum | Aave V3 |
| S8 | aEthUSDC | ethereum | Aave V3 |
| S9 | aEthUSDT | ethereum | Aave V3 |
| S35 | aBasUSDC | base | Aave V3 |
| S41 | aArbUSDCn | arbitrum | Aave V3 |
| S54 | aAvaUSDC | avalanche_c | Aave V3 |

All on HyperSync-served chains. The Tier-2 hybrid self-verifies (probes RPC once
per token, gates "events" on `is_atoken`, falls back to RPC on any mismatch), so
enabling it **cannot silently ship a wrong aToken number** — worst case it
degrades to RPC, and `scripts/compare_position_balance.py` prints the per-token
verdict so a silent fallback is visible.

Sensitive parity cases: **S9/S2/S54** (prior mid-window entry/exit fix, git
`6b12698`); **S1/S4** carry `lending_idle_usds` (stays RPC regardless).

## 5. Sequenced plan (one commit per backend, parity-gated)

Mirror obex→keel→skybase: gate each step on an existing compare harness (exit 0)
before deleting the fixture path. Lowest risk first.

0. **Prep** — add a `sources:` block to `config/spark.yaml`; rework
   `run_spark_2026.py` toward live sources (or add a `--live` toggle so both
   paths coexist during validation).
1. **debt** → `hypersync`. Gate: `compare_debt_sources.py --prime spark --full`.
2. **block_resolver** → `hypersync` (Tier 1). Drops `PIN_BLOCKS_BY_MONTH` + the
   two block fixtures. Gate: block half of `compare_position_balance.py`.
3. **position_balance** → `hypersync` (Tier 2) — **activates the aToken path.**
   Gate: `compare_position_balance.py --prime spark` must match RPC exactly at
   SoM/EoM for the subproxy + every venue token, and show `aave`/`events`
   verdicts (not silent `rpc`) for the 12 Cat C tokens.
4. **balance** → `hypersync`, chain by chain (widest surface). Gate:
   `compare_balance_sources.py`; re-verify Cat A `inflow_by_counterparty` on S26
   (Anchorage) and S28 (PYUSD) given the self-transfer-netting fix (git `d565454`).
   Only then delete `cat_b_cum_balance.json`, `cat_e_cum_balance.json`,
   `subproxy_*_timeseries.json`, `inflow_by_counterparty.json` and the
   `SETTLE_SPARK_ALLOW_PRE_PERIOD_ANCHOR` bypass.

**End state:** `config/spark.yaml` carries
`sources: {debt, balance, block_resolver, position_balance: hypersync}`;
`run_spark_2026.py` uses an empty `Sources()` and orchestrator-resolved pins;
`spark_2026_q1/` fixtures retired; RPC remains for the eth_call-only price/oracle
paths and Dune for SSR.

## 6. Risks

- **Runner rework** is the bulk of the effort, not the sources themselves.
- **6-chain balance fan-out** — first prime to exercise HyperSync balances across
  all six chains; each chain needs its own parity pass.
- **Cat A counterparty tagging** (S26/S28) — verify against the self-transfer fix.
- **Fixtures encode a pre-period-anchor bypass**; a live source reconstructs from
  genesis and removes it — compare against on-chain truth, not the fixture.
