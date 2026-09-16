# Daily revenue delivery review log

## Step 3 — PR #207

- Round 1: checked process isolation, exact comparison and transport accounting.
  Fixed comparison accepting mismatched prime/cutoff identities. The initial live
  OBEX check matched every field but found two repeated capability requests;
  finalized execution reverts now persist as typed responses and remain errors
  to callers. Transport failures are not persisted.
- Round 2: checked negative-cache safety and source compatibility. Tightened
  persistence to a dedicated EVMRevert exception produced only from structured
  execution-revert RPC responses; an arbitrary provider error mentioning a revert
  must not become durable. Added regression coverage for that distinction.
- Round 3: rechecked Dune alias interception, swallowed-error accounting, exact
  nested result comparison, required persistence, fresh-process isolation and
  the transport boundary beneath retries. No further P1/P2 findings in the
  reviewed implementation. Forty targeted checks pass, including the six-prime
  real-Postgres matrix. Live acceptance is recorded separately, not inferred
  from the deterministic zero-position transport.

## Step 4

- Round 1: reviewed cutoff and month-boundary behavior. Added preflight validation
  so future/incomplete days and dates outside the last 90 completed UTC days
  fail before any worker or provider request starts.
- Round 2: reviewed measurement validity and failure paths. Fixed advancement
  running despite a failed same-date gate; the command now records the failure
  and does not present next-day measurements built on a failed reuse check.
- Round 3: checked nested/parallel timing, profile restoration, retry counting,
  cache-counter scope and response-byte semantics. No additional P1/P2 findings.
  Metrics explicitly distinguish instrumented wall time from provider billing
  units and do not claim synthetic fixtures are live budget measurements.

## Step 5

- Round 1: checked immutable identity, concurrent inserts and publication order.
  Fixed ambiguous correction ordering within one transaction (Postgres NOW is
  transaction-stable); a database sequence now orders publications explicitly.
- Round 2: checked provenance integrity. Local code-version capture now refuses
  untracked source/config files as well as tracked edits, rather than assigning
  a clean commit identity to uncommitted implementation changes.
- Round 3: checked transaction ownership, identical-identity conflicts, revision
  lookup isolation by prime, late-backfill ordering, Decimal serialization and
  the 90-day publication boundary. No further P1/P2 findings. Five targeted
  Postgres/provenance checks pass; failed transactions preserve prior results.

## Step 6

- Round 1: checked publication boundaries and lock/session behavior. Added guards
  against a calculation returning another prime/cutoff and against transactional
  lock sessions that would hide attempt records until completion.
- Round 2: checked first-run outages and missed schedules. Added a fixed deployment
  start cutoff so missed initial ticks can be recovered even when no attempt was
  recorded (for example a DB outage); it is clamped to the rolling 90-day limit.
  Reviewed the Railway plan and removed unrelated existing-service drift so the
  deployment only adds the daily revenue service.
- Round 3: checked long-running extraction and schedule starvation. Added a hard
  six-hour process deadline (configurable) that cannot be swallowed by fallback
  handlers. Expiry closes DB sessions, releases locks and leaves an auditable
  abandoned attempt for the next tick. No unresolved P1/P2 findings after this
  fix; deadline termination, recovery, locking and publication tests pass.

## Step 7

- Round 1: reviewed dependency initialization and degraded database behavior.
  Fixed reader construction failures escaping the endpoint's error handling;
  connection setup now returns a generic 503 without exposing credentials.
  Existing HTTP validation and missing-result responses remain intact.
- Round 2: exercised the real Postgres reader through HTTP for revision selection,
  exact decimals, prime isolation, late backfills and failed-refresh retention.
  Fixed earliest-valid-date underflow in the default history window and disabled
  caching of absent/unavailable results so an early miss does not hide a later
  publication. Readiness now checks the exact configured prime set.
- Round 3: rechecked SQL parameter binding, cutoff/revision isolation, coherent
  result/attempt snapshots, bounded history, unavailable-result semantics and
  read-only provider behavior. ETags change on UTC freshness rollover and
  corrections; unchanged responses validate with 304. The final deployment
  readiness check found an unresolved P1: input freshness is not a publication
  gate. Grove and Spark use the SOFR configuration whose last row is 2026-08-31;
  `rates.at(date(2026, 9, 15))` returns that rate (0.0368), while the legacy
  fatal carry-forward threshold is 45 days. A newly calculated September
  result can therefore be published and reported current despite missing
  reference-rate updates. Configuration hashing records which inputs were
  used but does not establish their freshness. Stopped before merging step 7
  under the requested three-round rule. The new daily revenue deployment is
  paused pending a freshness gate and explicit carry-forward provenance.
  Production overrides its start command with a no-op; the configured daily
  schedule remains in place. Reapplying the main IaC worker command would
  resume calculation and must wait for this issue to be resolved.
  Existing hourly SBE and read API services are unaffected.

Final offline + isolated-Postgres regression: 1145 passed, 1 skipped, 5 live
tests deselected. Live same-date reuse passed for Grove and OBEX; Spark and the
remaining live matrix are incomplete (provider throttling and stop-rule exit).

## Step 7 resumed — official SOFR input gate

The user supplied/authorized the NY Fed source and requested continuation. The
prior P1 is addressed by fetching complete official business-day observations,
persisting exact snapshots, passing them into the calculation, and checking
coverage before either calculation or cached-result reuse. The schedule moves
to 20:17 UTC after the same-day revision window. No YAML SOFR fallback is allowed
in daily publication. The base-rate cap remains in effect.

- Round 1: reviewed snapshot identity, correction/retry behavior and calendar
  coverage. Found a correction reverting to an earlier rate could reuse an old
  revision while the intervening revision stayed latest. Reuse now selects only
  the current publication; changed inputs incorporate the superseded revision
  into the new identity. Added an A→B→A correction regression. Configuration
  changes during preparation are also checked before reuse, and verified
  full-close calendar coverage is explicit through 2027 (unknown dates fail).
- Round 2: reviewed complete-window coverage, effective-date semantics and
  holiday boundaries. Found that an unexpected official print on a configured
  closure could be ignored, especially at the cutoff. Fetches now extend through
  the actual cutoff and reject any disagreement between official observations
  and the calendar. Added that regression; missing middle days, malformed rows,
  duplicate dates and unpublished terminal observations already fail closed.
- Round 3: rechecked full-calculation injection, immutable persisted observations,
  the base-rate cap, failed-refresh retention, revision provenance in the HTTP
  response, month-opening carry-forward seeds and independence of finalized raw
  chain caches. Eighteen targeted checks pass, including a complete configured
  Grove calculation with deterministic transport that forbids YAML rate loading.
  Live official-source validation accepted complete coverage through 2026-09-14
  and rejected missing 2026-09-15 before publication. No unresolved P1/P2 findings
  in this resumed review. Production remains paused until merge/deployment.

## Required-input publication follow-up

The user authorized fixing the post-merge P1 recorded on PR #211. Daily guarded
source operations now abort past monthly fallback handlers on exhausted reads;
the worker owns retry and failure recording. Optional typed contract probes and
successful transport retries remain supported.

- Round 1: reviewed optional probe classification and cache behavior. Found Curve
  enumeration could still treat malformed/unknown exceptions as a valid coin
  count. In daily publication, only typed reverts after at least two coins may
  terminate enumeration; added regressions. Required cached execution reverts
  are also checked at the public eth_call boundary. Raw finalized caches remain
  reusable, and failed calculations do not write successful result revisions.

- Round 2: reviewed transport retries and errors encoded in successful HTTP
  responses. Added a guard for unregistered JSON-RPC error/malformed envelopes
  and protected RPC result decoding for native balances/bytecode. Tests exercise
  Grove's actual cash-distribution fallback through a complete calculation,
  HTTP/timeout/JSON-RPC failures, recovered and exhausted HyperSync throttling,
  and transport failures during optional selector probes. Each exhausted read
  records failure without publishing; a successful internal retry is accepted.
- Round 3: rechecked all changed call sites, guard/thread lifecycle, strict versus
  optional cached reverts, source retry boundaries, durable attempt recording,
  and immutable result publication. Added a regression proving a fresh worker
  attempt can recover after a terminal failure without carrying the sticky flag
  forward. No unresolved P1/P2 findings. The six-prime restart/reuse matrix passes;
  this remains deterministic wiring/cache evidence, not live nonzero parity.

## Operational acceptance follow-up — PR #213

- Round 1: reviewed measurement inputs and deadline semantics. The verifier now
  uses official reference snapshots and treats only the explicit official
  refresh as an allowed freshness read; reference time is included in extraction.
  The completion deadline is based on the last due scheduled run, not midnight.
  Regression testing exposed a fixture subprocess reading a live local-cache
  entry. Required-Postgres mode now fetches local-only misses from the source
  instead of promoting them, and every test gets an isolated pickle directory.
  Added refetch/reuse/write-failure and invalid future-attempt regressions.
  Checked 121 production finalized date anchors: none matched the deterministic
  fixture block-number signature. No production data was deleted or rewritten.
