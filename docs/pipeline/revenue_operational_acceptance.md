# Daily revenue operational acceptance

Feature implementation and production acceptance are separate. PRs #207–#212
provide the pipeline, persistence, daily schedule, read API and required-input
failure gate. This follow-up closes the remaining measurement and monitoring work.

## Live measurements

Run the verifier against a separate Postgres database, with provider credentials
and `db/schema.sql` applied. Use a pinned checkout and an empty local cache for
each subprocess. Baseline all six primes through one completed date, repeat
that date, then advance the entire fleet by one day. Required-Postgres mode
never promotes local-only files; tests have isolated local caches.

A completely empty baseline is valid but expensive on a throttled provider.
For the September 14→15 acceptance run, seed only immutable production snapshots
whose pinned block is at or below that chain's September 14 closing pin. Date
resolutions must also target no later than September 14 UTC close; cached block
timestamps must be no later than that close. Exclude all legacy/unpinned entries.
The snapshot manifest records cutoff, per-chain ceilings, row count and SHA-256.

The baseline setup can additionally seed exactly the event intervals requested
by that calculation. Require complete production coverage of each interval and
an upper block at or below that chain's September 14 closing pin; reject unknown
chains or post-cutoff timestamps. Copy rows before claiming coverage, atomically
in the isolated database, and record each interval's bounds, count and hash.
This copies requested ranges only, keeping temporary storage bounded. Intervals
not covered by production are fetched normally from HyperSync. Inputs from
interrupted baseline attempts remain reusable. The measurement-only seeding
hook is enabled only for the baseline. The fresh-process same-date and next-day
runs use the unchanged implementation against the isolated database, with the
seed hook disabled and its production database credential removed.

The first complete run is a seeded baseline, not a cold-start cost benchmark
or an independent validation of the source cache. No cutoff advances until the
whole baseline fleet and reuse checks complete. September 15 remains uncached;
warm reuse and next-day demand are the measured workloads. Never measure
incremental demand against an unrestricted production cache that already
contains future inputs. Keep generated reports and seeded databases outside Git.

The verifier uses the same official reference-rate preparation as publication.
Reference refreshes are counted separately from historical extraction; changed
SOFR snapshots invalidate numerical comparison. Exact equality covers every
calculation field. On a same-date rerun, only finalized-head/boundary checks and
the explicit official reference refresh are permitted upstream reads.

`verification.json` records requests by provider and RPC method, response bytes,
cache hits, extraction time and remaining calculation time. Response bytes are
decoded HTTP response bodies, not wire/TLS traffic. The extraction timer includes
the instrumented input/normalization paths; its remainder is not a CPU profile
of all arithmetic. These are HTTP attempts (including retries), not provider
billing credits. The baseline may
need older raw events for opening state; published cutoffs remain within 90 days.
Initial cache population is not representative of recurring daily cost.

For plan sizing, report RPC methods and HTTP attempts separately. Also retain
successful HyperSync response `x-ratelimit-cost` totals and HTTP 429 counts, but
do not equate those rate-limit units with billed credits. The observed endpoint
limit was 30,000 units per 60 seconds; a minimal request reported 1,000 units.
[Envio's usage documentation](https://docs.envio.dev/docs/HyperSync/api-tokens)
describes billed credits as depending on bandwidth, disk reads and other
resources, and directs users to the account dashboard for monthly usage.
Confirm that mapping before selecting a paid plan. Exclude rejected-request
cost headers from successful-request totals. Multiplying a measured daily
workload by 31 is a planning scenario, not a measured monthly bill: month
boundaries, new venues, retries and input corrections can change demand.

## Independent completion monitor

The GitHub Actions `Daily revenue completion` workflow checks the read API daily
at 03:00 UTC, separately from Railway's scheduler. The worker starts at 20:17 UTC,
has a six-hour hard deadline, and the monitor allows 30 minutes of grace. A check
at 03:00 on September 17 therefore requires the September 15 cutoff produced by
the September 16 job. The API's calendar-day freshness flag alone would signal a
false alarm after midnight, before the next scheduled run was due.

Missing/stale cutoffs, failed or abandoned current attempts, overdue running
attempts, malformed responses and an unavailable API fail the workflow. A newer
run can still be in progress if the previously due result was published. Neither
the workflow nor the checker starts calculations or calls chain providers.

Keep the monitor's schedule/deadline constants aligned with the Railway worker.
Workflow failure is an alert signal, not proof of notification receipt. Verify
notification routing and an actual delivery separately; do not infer it from a
successful HTTP check or a Railway deployment status. Railway project crash
notifications cover failures; the independent monitor also detects missed runs.

## Acceptance record

### Live same-date reuse — September 14, 2026

All six primes passed exact equality across every calculation field and the
reference snapshot. Every warm run used a new interpreter and empty local file
cache, required the isolated Postgres store, and forbade Dune calls. Historical
provider reads and RPC calls were zero for every prime. The seeded baselines also
matched all fields of the corresponding published September 14 results.

The pinned calculation checkout was `1b30ee5`; its `src/` tree is identical to
merged `03fa6c6`. Baseline setup started with 4,812 immutable raw snapshots and
later copied 899,307 event rows across 73 requested, past-only intervals. It also
retained completed live fetches from interrupted baseline attempts. None of this
setup is included in the recurring-cost totals below.

| Prime | HyperSync attempts | Official reference reads | Historical reads | RPC calls | Elapsed seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| grove | 36 | 1 | 0 | 0 | 22.48 |
| spark | 43 | 1 | 0 | 0 | 144.76 |
| obex | 7 | 0 | 0 | 0 | 1.75 |
| keel | 7 | 0 | 0 | 0 | 1.51 |
| skybase | 7 | 0 | 0 | 0 | 1.18 |
| osero | 7 | 0 | 0 | 0 | 2.46 |

Fleet totals: 109 HTTP attempts (107 HyperSync, two official reference reads),
18,614 response bytes, and 174.13 seconds summed measured wall time.
Two HyperSync attempts were retried after HTTP 429; successful responses reported
105,000 rate-limit cost units. These are local measurements under a shared
provider quota, not a Railway runtime guarantee or billing-credit total.

### One-day advancement — September 14 → 15, 2026

All six complete calculations matched every field of the corresponding published
September 15 result. No Dune calls occurred. All 219 event-query attempts started
after their chain's September 14 closing pin: none re-fetched the baseline event
range. New closing-block resolution, snapshots and event extensions are included
in the request totals. The verifier's generic `historical` request category also
includes these newly needed reads; it does not mean they repeated cached history.

Runs used the production order: Grove, Spark, OBEX, Keel, Skybase, Osero. Shared
inputs fetched by an earlier prime are reusable by later primes. Per-prime cost
allocation therefore depends on order; use the fleet total for sizing.

| Prime | HyperSync attempts | RPC attempts | Reference reads | Response MB | Elapsed seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| grove | 284 | 133 | 1 | 0.1574 | 508.00 |
| spark | 281 | 205 | 1 | 4.3121 | 568.98 |
| obex | 14 | 4 | 0 | 0.0023 | 13.32 |
| keel | 9 | 2 | 0 | 0.0014 | 1.78 |
| skybase | 9 | 2 | 0 | 0.0014 | 1.85 |
| osero | 20 | 6 | 0 | 0.0534 | 57.44 |
| **Fleet** | **617** | **352** | **2** | **4.5279** | **1151.37** |

There were 971 HTTP attempts and 4,527,893 decoded response bytes. HyperSync
returned 44 HTTP 429 responses, all recovered by retry; its successful responses
reported 573,000 rate-limit units. Those units are not a verified billing total.

| Prime | Raw snapshot hits / misses | Instrumented extraction seconds | Remaining calculation seconds |
| --- | ---: | ---: | ---: |
| grove | 578 / 255 | 507.73 | 0.269 |
| spark | 3779 / 520 | 568.75 | 0.235 |
| obex | 53 / 12 | 13.18 | 0.140 |
| keel | 10 / 6 | 1.62 | 0.166 |
| skybase | 10 / 6 | 1.70 | 0.153 |
| osero | 159 / 16 | 57.31 | 0.122 |

The measured full fleet takes about 19 minutes, comfortably inside the six-hour
worker deadline for this workload. This supports retaining full MTD arithmetic
for now. It does not establish a worst-case bound for month rollover, new venues,
large activity spikes or provider outages.

### Initial provider sizing

Use the measured daily advance, not 30 copies of a cold monthly extraction.
A 31-day planning scenario with **2× headroom** gives:

| Resource | One measured day | 31 comparable days | With 2× headroom |
| --- | ---: | ---: | ---: |
| RPC attempts | 352 | 10,912 | 21,824 |
| HyperSync attempts | 617 | 19,127 | 38,254 |
| Official reference reads | 2 | 62 | 124 |
| Decoded response MB | 4.53 | 140.36 | 280.73 |

The RPC mix was 349 `eth_call`, two `eth_getCode`, and one
`eth_getBlockByNumber`. At Alchemy's published nominal weights of 26 CU for
`eth_call` and 20 CU for the other two methods, routing **all** these RPCs through
Alchemy would represent 9,134 CU/day, 283,154 CU per comparable 31-day month,
or 566,308 CU with 2× headroom. This is a method-based planning equivalent,
not the observed account bill: some configured chains use other endpoints,
error charging can differ, and other applications also consume account capacity.
[Alchemy compute-unit costs](https://www.alchemy.com/docs/reference/compute-unit-costs).

Monthly headroom does not remove a per-minute throttle. Match the HyperSync
plan's throughput as well as its usage allowance; check the account's actual
credit consumption against the measured requests and bytes before choosing a
paid tier. No provider subscription was changed. Review actual daily usage and
month rollover before tightening these initial allowances.

Detailed reports, request logs and the measurement database remain outside Git.
The requested-interval seed manifest SHA-256 is
`32fdd4ac896afeaaa62f5632a90ea6ed0ea0827bb705bae9fd482ecba7ba3c4c`.
The full verification report SHA-256 is
`7c03c67aef3c8d5ebf8dda49905fcf7608c184f53bd403d0d2f599daee3362bc`.

### Production publication and unattended scheduling — September 16, 2026

PR #213 merged as `03fa6c64e61adbd835102297e6a174e93973b6e0` after three review
rounds and passing unit/Postgres CI. All six primes have published results
through September 15. Grove caught up after the required official SOFR
observation became available.

The unmodified Railway schedule (`17 20 * * *`) triggered without a manual
restart. The first durable attempt started at 20:21:52 UTC; all six finished
successfully by 20:24:05 UTC, approximately 132 seconds later. Scheduling was
about five minutes later than the configured minute. The completed deployment
was `52f02c95-76e3-4db2-836e-d51ef1279881`, running the merged commit above.
The API returned HTTP 200 with all six primes current and successful.

Every field of each newly published September 15 calculation exactly matched
its previous `8071f5b` calculation. This verifies reuse across the deployed code
change; it is not a measurement of a newly advanced cutoff.

The independent completion checker passed in
[GitHub Actions run 35143823615](https://github.com/soterlabs/settlement-cycle/actions/runs/35143823615).
That monitor invocation was manually dispatched; its own 03:00 UTC schedule
has not yet been observed. The Railway worker evidence above is automatic.

### Notification delivery

Receipt is still unverified. Railway returned no notification rules for this
project/workspace, and the current credentials cannot read delivery history.
No deliberate test notification has been sent. A destination and authorization
to send a test there are pending; a workflow failure alone does not establish
that someone receives it.

Dashboard integration uses [the existing handoff prompt](msc_dashboard_daily_revenue_prompt.md).
