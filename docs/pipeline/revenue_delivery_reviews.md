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
