# settle-debt-envio

Envio HyperIndex that reconstructs **daily ilk debt** (frob + grab `dart`) for
Sky allocator primes from the MakerDAO Vat. It backs
[`EnvioDebtSource`](../src/settle/normalize/sources/envio_debt.py); the goal is
to match the Dune `debt_timeseries.sql` byte-for-byte, then retire Dune.

Design rationale, indexer contract, and free-plan analysis: [`../docs/envio/README.md`](../docs/envio/README.md).

Built on **HyperIndex v3** (Node 22+ required; v3 handlers register via
`indexer.onEvent(...)` from the `envio` package).

## Quickstart

```bash
cd envio-indexer
pnpm install            # or npm install
cp .env.example .env    # set ENVIO_API_TOKEN — v3 HyperSync wants a (free) token
pnpm codegen            # emits types to ./.envio/ (v3; v2 used ./generated)
pnpm dev                # indexer + Postgres + Hasura GraphQL on :8080
```

Let it sync to head, then from the repo root run the comparison gate:

```bash
export ENVIO_GRAPHQL_URL=http://localhost:8080/v1/graphql
export ETH_RPC=...      # pin-block resolution
export DUNE_API_KEY=... # the other side of the diff
python scripts/compare_debt_sources.py --prime spark --month 2026-06 --full
```

## What it indexes

| | |
|---|---|
| Contract | Vat `0x35D1b3F3D7966A1DFe207aa4514C12a259A0492B` (mainnet) |
| Event | anonymous `LogNote` (topic0 = fn selector, topic1 = ilk) |
| Kept | selector ∈ {frob `0x76088703`, grab `0x7bab3f40`} **and** ilk `ALLOCATOR-*` |
| Emits | `VatDebtEvent { ilk, sig, dart (raw WAD, signed), urn, block*, txHash }` |

`dart` is decoded at byte offset 164 of the note payload — the same word the SQL
pulls via `substr(input, 165, 32)`. It is stored **raw and un-rate-scaled**; the
`Vat.rate` index is applied later in Python (`normalize/debt.py`).

## Files

| File | Purpose |
|---|---|
| `config.yaml` | network, contract, event, start block, field selection |
| `schema.graphql` | the `VatDebtEvent` entity (the Python-side contract) |
| `abis/vat.json` | Vat ABI — carries `anonymous: true` for `LogNote` |
| `src/EventHandlers.ts` | filter + `dart` decode + entity write |

## Known knob to verify

`LogNote` is **anonymous**, so its `topic0` is the function selector rather than
an event-signature hash. We reference the event by name so Envio reads
`anonymous: true` from `abis/vat.json`. Confirm your Envio version matches the
selector as expected on first sync — the comparison harness will flag any
mismatch immediately (a wrong topic/offset shows up as a per-day `cum_debt`
diff). If needed, add an `eventFilters` topic1 allowlist of the exact allocator
ilk `bytes32` values to also filter server-side (and cut sync volume).
