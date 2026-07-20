# HyperSync migration — final parity review checklist

Run this before sending PR #154 for review. Goal: **prove every HyperSync
source produces identical results to the Dune/RPC it replaces, with no silent
fallback masking a gap.** Every check is exact-match unless a tolerance is
stated (and the only non-zero tolerance is Dune's own Trino decimal truncation,
`--tol 0.001`).

Env for all checks: `ENVIO_API_TOKEN`, `DUNE_API_KEY`, `ETH_RPC` (+ per-chain
`*_RPC`), and `DATABASE_URL` (store) — or `HYPERSYNC_NO_STORE=1` for live-only.

---

## Phase A — Source-level parity harnesses (fast, isolated)

Each compares a HyperSync source against the incumbent at sampled points; each
exits non-zero on drift (CI-ready).

- [ ] **Debt** — `scripts/compare_debt_sources.py --prime <p> --month <m> --full --tol 0.001`
      for every debt-bearing prime (spark, grove, obex). ✅ already validated: spark 544 days.
- [ ] **Balance** — `scripts/compare_balance_sources.py --prime <p> --month <m>`
      (subproxy USDS/sUSDS + every venue token; Dune vs HyperSync).
- [ ] **Block resolver + position balance** — `scripts/compare_position_balance.py --prime <p> --month <m>`
      (RPC vs HyperSync per chain + per venue token; prints the events/aave/rpc verdict).
- [ ] Cat C spot-check across ≥2 blocks and ≥2 Aave instances (SparkLend + Aave
      Horizon) — reconstruction == RPC `balanceOf` to the wei.

## Phase B — End-to-end settlement parity (the real gate)

The ground truth. A/C/D/E are diagnostics for when this fails.

- [ ] For each migrated prime, with **all** HyperSync sources enabled in config
      (`debt`, `balance`, `block_resolver`, `position_balance`), regenerate every
      month 2026-01..06 and confirm **`summary.md` is byte-identical** to the
      committed Dune/RPC artifact:
      `git diff --stat -- 'settlements/<p>/**/summary.md'` → empty.
- [ ] `.xlsx` differ only in the generated-at timestamp cell (spot-check one).
- [ ] obex ✅ · keel ✅ · skybase ✅ (done). grove / spark: pending a fast archive RPC.

## Phase C — Coverage matrix (no blind spots)

- [ ] Build the grid `prime × chain × pricing category × source` and confirm each
      HyperSync source is exercised by ≥1 Phase-A/B case. In particular:
  - non-rebasing balance via events: Cat A / B / EOA + subproxy USDS/sUSDS
  - Cat C aTokens via reconstruction: SparkLend spTokens **and** Aave (core + Horizon)
  - block resolver: every chain a prime touches, **including monad**
  - multi-chain balance (spark's 6 chains) once spark is runnable
- [ ] Note any combo with **no** coverage — that's an untested path.

## Phase D — Anti-masking audit (a "pass" must be real)

- [ ] `position_balance` verdicts are the **expected** kind per token: par-stables/
      4626 shares → `events`; aTokens/spTokens → `aave`; nothing unexpectedly `rpc`
      (an `rpc` verdict on a token we intended to reconstruct = a silent miss).
- [ ] The store is actually serving (rows present in `hypersync_logs`; coverage
      advancing) and not silently live-passing-through when a DB was expected.
- [ ] Grep the HyperSync sources for `except … : fallback/return rpc` and confirm
      each fallback is intended, not hiding a decode error. A run should surface
      HyperSync/decoders errors loudly, never degrade silently to a wrong number.

## Phase E — Edge cases & determinism

- [ ] Reorg window: a query with upper bound within `HYPERSYNC_REORG_MARGIN` of head
      is served live and **not persisted** (store test covers this; confirm the
      guard fires in a near-head run).
- [ ] Zero/empty balances (agent-rate-only primes, pre-funding blocks) → no
      premature `events` classification; decimals + `min_transfer_amount` filter honored.
- [ ] Self-transfers deduped; genesis/first-block; naive-vs-aware datetimes.
- [ ] Determinism: re-run same `(prime, month, pin)` → byte-identical output;
      **cold vs warm store** → identical; block resolver result stable across runs.

## Phase F — Scope confirmation (what is NOT migrated)

State explicitly in the PR so reviewers aren't surprised:

- [ ] Still on Dune/RPC by design: **SSR**, **PSM3**, **LP** (v3/v4/curve), Cat E
      NAV oracles, 4626 `convertToAssets`.
- [ ] **Spark Savings V2** (`savings_v2_deployed`) **cannot** move — it reads a
      Dune-hosted *derived* table, not raw chain data.
- [ ] grove / spark are code-ready but **environment-blocked** (need a fast
      multi-chain archive RPC incl. a healthy monad node) — not yet parity-verified end-to-end.
- [ ] Residual RPC floor = irreducible daily contract-state reads (4626/AMM/NAV)
      the daily supply-side methodology requires.

---

### Sign-off
PR is review-ready when **Phase B is green for every runnable prime** and Phases
A/C/D/E have no open items for those primes. grove/spark carry an explicit
"pending archive-RPC" caveat rather than blocking the rest.
