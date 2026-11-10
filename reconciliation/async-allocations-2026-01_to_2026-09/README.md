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

## Final historical effects

All figures are USDS. Income is recomputed using the same pins and NAVs. These
are reductions in previously overstated prime income, not CoF refunds. The
baseline replay reproduces published Maple income and average positions to
less than 0.000001 USDS. Prior reconciliation posts contain no true-up of these
queue/cash corrections; prior unrelated adjustments remain excluded.

| Month | Spark S14 | Spark S15 | Obex V1 | Global CoF delta |
|---|---:|---:|---:|---:|
| 2026-01 | -9,948.93 | 0.00 | 0.00 | 0.00 |
| 2026-02 | -33.87 | -5.60 | 0.00 | 0.00 |
| 2026-03 | -5,127.91 | -2,616.34 | 0.00 | 0.00 |
| 2026-04 | -7,034.16 | -4,161.79 | 0.00 | 0.00 |
| 2026-05 | -455.14 | -1,271.55 | 0.00 | 0.00 |
| 2026-06 | -29.08 | 0.00 | 0.00 | 0.00 |
| 2026-07 | -10,358.16 | 0.00 | -273,508.90 | 0.00 |
| 2026-08 | 0.00 | 0.00 | 0.00 | 0.00 |
| 2026-09 | 0.00 | 0.00 | 0.00 | 0.00 |
| **Total** | **-32,987.25** | **-8,055.29** | **-273,508.90** | **0.00** |

**Spark: −41,042.54; Obex: −273,508.90.** Totals use unrounded calculations.
Every month of Ethena, BUIDL, USTB and USCC has zero incremental income effect.
Ethena retains its ordinary ~81 sUSDe wallet holding and native income; its
zero effect refers to pending cooldown claims, not zero wallet value.

`results.json` contains 63 month/prime/venue records: old/new positions and
averages, old/new venue CoF attribution, raw and net income deltas, exact unpaid
UTC closes, transaction/log identities and published-workbook hashes. The 27
Maple records carry full production revenue outputs; zero-effect records retain
ordinary positions separately. Daily NAV responses and full source histories
prove that no intramonth 2026 issuer activity was missed. No known prior credit
is netted twice. The prior-post comparisons are
[MSC #5–#9](https://forum.skyeco.com/t/msc-5-msc-9-reconciliation/28037),
[MSC #5–#10](https://forum.skyeco.com/t/settlement-reconciliation-msc-5-10-january-june-2026/28150),
and [MSC #5–#12](https://forum.skyeco.com/t/settlement-reconciliation-msc-5-12-january-august-2026/28273).

Venue attribution redistributes the same frozen whole-prime CoF using the
existing weighted-average formula. It does not change aggregate Sky revenue
or create a separate reimbursement. The implementation itself books no manual
adjustment. The separate reconciliation PR #277 applies these net historical
amounts once and presents their mint/transfer consequences.
