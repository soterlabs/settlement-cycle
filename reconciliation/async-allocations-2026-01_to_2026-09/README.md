# Spark and Obex async allocation audit, January–September 2026

Implementation dependency: #226 (`ca28e58`). Isolated comparison baseline:
`9b290a3f164882f2e19cad9fbc7ad597bce400a7` (main before this implementation).
Scope follows #263. This is a read-only component replay, not a new monthly
settlement run, an ALM transfer-review certificate, or an executed payment.
Frozen published workbooks remain unchanged.

The shared #226 exposure and economic-capital interfaces now retain Maple
queued/manual shares at NAV, Ethena cooldown underlying until actual payment,
and reviewed issuer claims. Custody transfers use the original share-flow NAV
clock; actual cash removes capital. Repricing pending shares is income, not
capital. Maple pool managers route shares but do not establish ownership;
Grove's ALM is an ordinary inter-prime transfer destination, never an escrow.

Maple queue deployments are verified independently for USDC and USDT. The
historical `uint128` request-event ABI and current `uint256` ABI are both
queried. Current manager implementation support is explicit and fails closed
on unknown deployments/upgrades. `lockedShares(owner)` alone omits queued
shares: ownership is `userEscrowedShares + manualSharesAvailable`.

BUIDL's small exit and advance/final payment have exact reviewed event links.
An unrelated historical receipt remains explicitly unassigned. USTB fixes its
claim at request execution NAV; USCC keeps variable shares until settlement.
New unexplained issuer payments require reviewed associations and fail closed.
A necessary pricing dependency changes USTB/USCC from raw share units (`$1`)
to the existing validated execution/NAV readers. Complete earlier event history
and empty opening claims must establish its zero 2026 income effect; no 2025
credit or published restatement is included.

Atomic deposits retain their existing recognition at issued/delivered shares.
Unissued subscription cash is not a new vault position. Spark JTRSY continues
through its existing #226 adapter. Arkis withdrawals are checked against actual
same-transaction underlying delivery in the retained canonical history.

Retained public inputs: complete owner share/queue/manager/cash histories from
genesis through September's closing pin, cancellation receipts, daily pinned
quotes, and independent RPC ownership/balance proofs at month boundaries.
`sha256.json` identifies the retained inputs. No credentials are included.

Replay (run from the repository root):

```sh
SETTLE_NO_CACHE=1 python scripts/replay_async_allocations.py \
  --baseline 9b290a3f164882f2e19cad9fbc7ad597bce400a7 \
  --from-month 2026-01 --to-month 2026-09 --primes spark obex \
  --output /tmp/async-reconciliation/results.json
```

The replay uses production capital/revenue functions with retained inputs;
unrecorded RPC calls and uncovered log selections fail. It reproduces the old
Maple venue income in published workbooks, then isolates the implementation
delta with identical dates, pins, NAVs and transfers. No historical rates change.
Maple and Ethena have no SDE borrowing exemption: global CoF delta is zero.
Daily economic holdings change venue-level CoF attribution, which must be
reported separately from amounts payable.

Measured Maple income deltas before final review: Spark **−41,042.540767005084**;
Obex **−273,508.895095791665** (July). Neither is a borrowing-cost credit.
Prior async true-ups for these venues: zero; unrelated previously settled
reserve-factor/SDE credits are excluded. Final machine-readable results and
per-venue attribution tables are added before merge. The separate historical
reconciliation PR consumes accepted net values once; this implementation does
not add an adjustment or overwrite a past settlement.
