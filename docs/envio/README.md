# Envio spike — replacing the Dune `IDebtSource`

This is the "indexer alternative spike" from
[`SETTLEMENT_ARCHITECTURE.md §11`](../SETTLEMENT_ARCHITECTURE.md). Goal: stand up
an [Envio HyperIndex](https://docs.envio.dev) that produces the **daily ilk debt
series** and prove it matches Dune byte-for-byte before we retire the Dune query.

We deliberately keep **both** sources live under the same `IDebtSource`
protocol; the comparison harness runs them together and only a clean match
unlocks Dune removal.

> **⚠️ Update (2026-07-11) — the Envio path is HyperSync-direct, not HyperIndex.**
> HyperIndex **cannot index** the Vat's frob/grab: they're emitted via the
> `note` modifier as an **anonymous `LogNote` with 4 indexed topics**, and
> HyperIndex's decoder reserves topic0 for the event-signature hash → 5 topics →
> `topic_count must be 1..=4` at build time. This is an open, unimplemented
> feature request: [enviodev/hyperindex#990](https://github.com/enviodev/hyperindex/issues/990)
> (maintainer: "quite big… not on the roadmap until after v3"). Confirmed at
> runtime — the deployed HyperIndex crash-loops on decoder build.
>
> **Working source: `HyperSyncDebtSource`** (`sources/hypersync_debt.py`, registry
> name `hypersync`) — queries HyperSync's raw-log API filtered by the frob/grab
> `topic0` selector (which HyperIndex can't express) and decodes `dart` directly.
> No indexer to host; needs a free `ENVIO_API_TOKEN`. The `envio` (HyperIndex)
> source is kept only for the non-anonymous event surface / if #990 ever lands.
>
> ```bash
> export ENVIO_API_TOKEN=...   # free: https://app.envio.dev/api-tokens
> python scripts/compare_debt_sources.py --prime spark --month 2026-06 --full
> #   (--envio-source hypersync is the default)
> ```

## What's already wired in this repo

| Piece | Location |
|---|---|
| Envio source (`IDebtSource`) | `src/settle/normalize/sources/envio_debt.py` |
| Registry entry (`"envio"`) | `src/settle/normalize/registry.py` |
| Comparison harness | `scripts/compare_debt_sources.py` |
| Unit tests (mocked transport) | `tests/unit/test_envio_debt.py` |

Run the comparison once the indexer is up:

```bash
export ENVIO_GRAPHQL_URL=http://localhost:8080/v1/graphql   # + token/secret if used
export ETH_RPC=...            # pin-block resolution (and --full rate reads)
export DUNE_API_KEY=...       # the other side of the comparison

python scripts/compare_debt_sources.py --prime spark --month 2026-06
python scripts/compare_debt_sources.py --prime spark --month 2026-06 --full
```

Exit 0 = every day matches within `--tol` (default: exact). Wire it into CI as
the gate that must pass before deleting `debt_timeseries.sql`.

---

## The contract the Python side depends on

`EnvioDebtSource` queries one GraphQL entity (default name `VatDebtEvent`,
override with `ENVIO_DEBT_ENTITY`). **One row per `frob`/`grab` contribution** to
`urns[ilk][u].art` on the Vat:

```graphql
type VatDebtEvent {
  id: ID!                # <txHash>-<logIndex>  (any unique key)
  ilk: String!           # 0x-prefixed 32-byte hex, lower-case
  dart: BigInt!          # raw signed int256 in WAD (1e18). NOT divided by 1e18.
  blockNumber: Int!
  blockTimestamp: Int!   # unix seconds, UTC
}
```

Two rules that make or break the match:

1. **Store raw `dart`, do not rate-scale.** The Dune query returns *normalised*
   `Art` (Σ dart, wad). The Vat `rate` index is applied later in
   `normalize/debt.py` via an RPC read. If the indexer pre-applies rate, the
   numbers drift by ~4.5% for `ALLOCATOR-SPARK-A`.
2. **Include both `frob` and `grab`.** `grab` is repurposed as the
   stability-fee capitalisation path for allocator ilks — omitting it
   under-counts by ~$48M by Apr 2026. See `debt_timeseries.sql` header.

The Python side aggregates per-day and cumsums in pandas, so the entity stays a
flat event log — no server-side date views needed.

---

## What to build on the Envio side

> **Already drafted** in [`../../envio-indexer/`](../../envio-indexer/) —
> `config.yaml`, `schema.graphql`, `abis/vat.json`, `src/EventHandlers.ts`,
> ready to `pnpm install && pnpm codegen && pnpm dev`. The steps below explain
> what those files do.

### 1. Install & scaffold

```bash
pnpm dlx envio init          # or: npx envio init  →  choose "Contract Import" / blank
```

Produces `config.yaml`, `schema.graphql`, `src/EventHandlers.ts`. Put
`schema.graphql` = the `VatDebtEvent` type above.

### 2. Index the Vat's `frob`/`grab`

The Vat (`0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B`) uses the `note` modifier,
which emits an **anonymous `LogNote`** on every state-changing call:

```
LogNote(bytes4 indexed sig, bytes32 indexed arg1, bytes32 indexed arg2,
        bytes32 indexed arg3, bytes data) anonymous
```

so each `frob`/`grab` is available as a **log** — no trace indexing required:

- `topic1 (sig)` = selector — `0x76088703` (frob) or `0x7bab3f40` (grab)
- `topic2 (arg1)` = `ilk` (bytes32) → your `ilk` filter
- `data` = the full calldata → decode `dart` = `int256(data[164:196])`
  (0-indexed byte 164 = Dune's 1-indexed `substr(...,165,32)`)

Because `LogNote` is anonymous, index it with a **wildcard / topic-filtered log
handler** (filter `topic1 ∈ {frob, grab}`), decode `dart` from `data`, and write
one `VatDebtEvent`. This mirrors `debt_timeseries.sql` exactly.

> Alternative: enable HyperSync **transaction-input / trace** indexing and decode
> `dart` from the call input at the same offset. Slightly heavier; the LogNote
> path is simpler and sufficient. Pick one and verify with the harness.

### 3. Point HyperSync at Ethereum mainnet

`config.yaml` — network `1`, start block = the Vat deployment (or, cheaper for
the spike, `prime.start_date`'s block; the harness passes `start = prime.start_date`
so earlier history isn't queried anyway). HyperSync is the default data source
and needs no archive-node RPC.

### 4. Run

```bash
pnpm dev            # indexer + Postgres + Hasura GraphQL on :8080
```

Let it sync to head, then run the comparison harness.

---

## Is the free plan enough?

**For this spike: yes.** Two ways to run it, both free:

- **Self-hosted (recommended for the spike).** HyperIndex is open-source; run the
  indexer + Postgres + Hasura locally or on the Railway box we already use for
  the raw-data Postgres (`docker compose up`). Zero platform cost, and it matches
  the data-ownership goal that motivated moving off Dune. HyperSync query access
  is free and generous — a single-contract (Vat) index is tiny.
- **Envio hosted platform** has a free tier that comfortably covers **one small
  indexer** like this (single chain, single contract, low entity count). It's
  enough to validate the spike without a card.

What would push you off free: many chains × many contracts (the full
multi-chain balance/transfer migration in later phases), or needing the hosted
platform's SLA/uptime for production settlement runs. Confirm current limits on
[envio.dev/pricing](https://envio.dev/pricing) before committing production to
the hosted tier — but for "prove debt matches Dune for one month," free is fine.

**Recommendation:** self-host for the debt spike (free, owned, deterministic).
Only evaluate the hosted tier when/if you migrate the broader event surface
(transfers, inflows, PSM3, DR) and want managed uptime.
