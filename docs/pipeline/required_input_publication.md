# Required-input failures during daily publication

Daily workers and the fresh-process verifier run inside `PublicationGuard`.
Exhausted RPC/HyperSync/CoinGecko operations raise `RequiredInputFailure`, a
signal outside `Exception` so the monthly pipeline's historical zero/flat-balance
fallback handlers cannot swallow it. The daily worker catches that signal at
its durable attempt boundary, records a failure and performs its bounded retries.
No failed calculation is published; prior results remain readable. Ordinary
monthly calls outside this guard retain their existing behavior.

The guard is process-wide, including the calculation's source threads, like the
existing transport audit. Concurrent guarded calculations require separate
worker processes. Cleanup restores normal behavior; a sticky failure also
prevents publication if a caller incorrectly catches BaseException.

Retries belong to a complete source operation, not individual HTTP attempts.
A 503 followed by a successful RPC retry is accepted. Once an operation exhausts
its retries, a later request cannot erase its failure. Unknown HTTP paths are
also checked by the worker's transport audit; failures outside registered source
operations abort instead of becoming financial defaults. Errors exposed in the
attempt ledger contain only the error type, not provider credentials.

Explicit capability/selector probes allow only typed deterministic EVM reverts:
Curve ABI/count discovery, scaled balances, Chronicle readWithAge, Aave metadata,
ERC-7540 diagnostic selectors and configured NAV oracle candidates. Transport
failures still abort in those probes. Curve enumeration additionally requires
at least two coins; malformed/untyped failures cannot establish the count.
Required eth_call reverts abort, including typed negative responses read from
persistent cache. Raw finalized inputs stay reusable; no global cache reset or
daily data artifacts are introduced.

Verification includes failure injection through the real worker and Postgres,
recovered retries, retaining a prior publication, cached reverts, source threads,
guard cleanup, Curve probes and the six-prime deterministic restart/reuse matrix.
That fixture matrix is not proof of every live nonzero venue branch.
