# Same-date revenue verification (PRD step 3)

Run `python -m settle.revenue.verification --as-of 2026-08-01 --output /tmp/revenue-check`.
All six configured primes run by default; `--prime obex` narrows diagnosis. Requires
provider credentials and `DATABASE_URL` with `db/schema.sql` applied. Each phase
starts a new interpreter with an empty local cache and required Postgres mode.
The first run may reuse existing durable inputs; the second must reuse them.

Every MonthlyPnL dataclass field is compared exactly, including nested daily and
venue breakdowns. Every HTTP attempt is counted below retry loops. A warm run
may only perform HyperSync head probes and fresh timestamp reads within explicit
boundary certification. Other requests fail acceptance, including a request
whose exception was swallowed by a compute fallback. Dune calls are forbidden
at the query entrypoint, even when a legacy cache could answer them.

Finalized EVM execution reverts are retained as typed negative responses and
re-raised to callers, never converted into cached zeros. This avoids repeating
unsupported capability probes on every worker restart. Transport errors and
ambiguous empty responses from deployed contracts remain retryable failures.

JSON reports contain calculation values, hashes, counts and timing, but no
provider URLs, credentials or raw upstream responses. Keep generated reports
outside Git. The Postgres CI matrix runs complete configured calculations for
all six primes with synthetic zero-position inputs, plus restart/interruption
and migration tests. It proves wiring/reuse under that scenario, not live parity
or coverage of every nonzero venue branch. Live reports are separate evidence.

Daily output backfills are limited to the last 90 completed UTC days. Extractors
may need earlier raw events to reconstruct opening state; those are input seeds,
not additional daily published results.

## One-day advancement (step 4)

Add `--advance` to measure the following completed UTC day after same-date reuse.
The JSON summary reports HTTP attempts by provider, uncompressed response bytes,
Postgres snapshot hits/misses, total wall time, extraction wall time and remaining
calculation/orchestration time. Extraction means calls in `settle.extract` and
`settle.normalize.sources`, including cache access and source decoding. Nested
calls and parallel intervals count once. Profiling adds overhead; these are
instrumented measurements, not an uninstrumented latency SLA. Postgres counters
do not include local-file or event-range hits. Response bytes exclude HTTP/TLS
framing and are not equivalent to provider billing units.

Use the per-prime `next_day` request counts and the provider's current billing
weights to size a plan. No thirtyfold assumption or unmeasured plan recommendation
is built in. The deterministic six-prime Postgres matrix checks that advancing
one day only requests event ranges beyond the previous cutoff. Real provider
measurements remain separate evidence; generated reports stay outside Git.
