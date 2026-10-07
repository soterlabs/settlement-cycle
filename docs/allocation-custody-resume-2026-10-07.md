# PR215: custody links and per-ilk August reconciliation

This continues [the principal-return investigation](allocation-resume-2026-10-07.md). Latest main was integrated at `4860a40`; merge commit `4c87d9d` preserves both allocation-yield XLSX output and main's isolated report output directory. The hash-pinned September refresh script explicitly migrates its older snapshots' absent `allocation_financing` field; normal production decoding remains strict.

## Implemented

- **Anchorage S23:** actual USDC funding creates a facility claim. Verified principal returns release its borrowed basis, including the December19 2025 $5m zero-net round trip. Configured partial returns retain interest as income. No notional schedule seeds principal. The July16 and August17 2026 returns still have unconfirmed principal/interest splits; the facility remains unresolved rather than treating all returned cash as borrowing.
- **Grove E10 BUIDL:** existing reviewed request/payment matching now also links the historical capital claims. Basis stays with the claim until cash arrives, including overnight and later payments. Realized exit fees reduce the funding that cash can carry. August24's $1 and $49,999,999 requests explicitly link to August25's $49,974,999.629718 payment; this link is before the September revenue-policy history boundary and does not restate revenue.
- **Grove E42 Galaxy:** actual USDC payments to the configured principal relay create the facility balance. The fixed notional schedule does not seed or cap borrowed capital. Uncertain source cash remains uncertain after investment.
- **Grove PAU NFT1352494:** trace the second holder's AUSD/USDC position as `E12_PAU`, separate from E12. This is an analytics-only account: no revenue row is added to settlement and unavailable revenue/net APY stay null. This pool has no USDS leg, so this change adds no USDS idle exemption.
- **Funding origin:** drawn principal retains its originating ilk through swaps, withdrawals, claims, bridges and reinvestment. Simultaneous draw/repayment in different ilks is no longer reduced to a net zero draw. Repaying A with B-funded cash refinances remaining A holdings with B; using earned cash reduces only the repaid ilk's remaining basis.
- **Daily idle inputs:** the read-only validator persists exact daily dollar deductions and checks their daily totals against the published control. Missing/stale inputs cannot silently pass as zero deductions.
- **Per-ilk comparison:** the validator accepts pinned daily debt/MSC balances and explicitly verified deduction owners. Daily debt, deduction, charge and period charge totals must reproduce the existing settlement. Allocation subtotals must reproduce the separate per-ilk subtotals. MSC financing is separately identified; no residual is added to allocations to force a pass.

## Validation scope

The August diagnostic uses immutable normalized transaction histories from the previous audit. Targeted migrations add canonical facility transfers, BUIDL request/payment links and PAU NFT movements. Original transaction identities, timestamps, draws and per-ilk debt changes are asserted unchanged. This is a **saved-input replay with targeted source refresh**, not a fresh extraction of every venue under latest main. Inherited normalization gaps remain material.

Exact historical daily idle inputs are read separately and compared to every published daily control. August's Grove SDE positions are held by the legacy ALM and belong to BLOOM-A; all other Grove August idle deductions are zero. The September-effective Basin deduction belongs to GROVE-A but is zero for this August comparison. Spark has one funding ilk. The per-ilk reference uses the existing prime-wide blended daily borrowing rate, preserving the published aggregate subsidy charge.

Machine-readable results, input hashes, daily debt controls, deduction owners and idle deductions are in `reconciliation/allocation_resume_2026_08.json`. Published settlement files and API outputs were not regenerated.

| Ilk | Traced allocation cost | Existing global CoF | MSC cost | Global excluding MSC | Remaining gap |
|---|---:|---:|---:|---:|---:|
| ALLOCATOR-SPARK-A | 12,080.98 | 6,108,910.34 | 397,481.84 | 5,711,428.50 | 5,699,347.52 |
| ALLOCATOR-BLOOM-A | 6,353.42 | 3,708,820.11 | 259,878.12 | 3,448,941.99 | 3,442,588.57 |
| ALLOCATOR-GROVE-A | 1,233.51 | 11,784.74 | 0.00 | 11,784.74 | 10,551.22 |

Validation: **1,424 tests passed**, two skips (optional Keccak dependency and isolated PostgreSQL integration URL). Final deduction-scope/financing regression tests also pass; Ruff and `git diff --check` pass. Spark S66 is excluded from the August idle control because that venue was absent from the published report: its exact idle amounts explain the entire August27–31 scope difference. No deduction is rescaled to match the control.

The diagnostic subtotal changes from $12,080.10 to $12,080.98 for Spark and from $6,304.10 to $7,586.93 for Grove. Neither prime reconciles. A null allocation cost means its funding history is unresolved; it does not mean the position has no economic borrowing cost. These model subtotals are not validated settlement charges: unresolved funding, including repayments, can still influence attribution. Gross/net APY acceptance is still incomplete; no permanent bounds warning or publication gate is introduced.

Grove's unmatched receipt count falls from 295 to 273 and its unmatched outflow count from 529 to 495. Spark's unmatched outflows fall from 25,356 to 24,212, but unmatched receipts rise from 43,020 to 43,161. The newly exposed receipts total $61,620,788.13: $61,304,382 comprises the unconfirmed July/August Anchorage returns, and $316,406.13 comprises other funding imbalances. Those imbalances remain unresolved; a lower outflow count is not proof of reconciliation. There are still 54 unresolved Spark allocations and 24 unresolved Grove allocations.

Both GitHub checks (`pytest` and `input-cache-postgres`) passed on `da561d5`. Long replays now log transaction-count progress; the Spark validation above completed on the same accounting logic before that logging-only update.

Grove's remaining unmatched history begins with a July24 2025 $50m JAAA receipt and includes later cross-chain JAAA movements. This predates the added redemption/facility links. Those earlier source gaps propagate into later holdings, so adding a custody adapter alone does not make their net APY reliable. Anchorage's July/August principal/interest split also still needs confirmation.

## Reproduction

First generate a current normalized history with `scripts/validate_allocation_capital.py`, preserving the desired period/pins. Then run:

```sh
PYTHONPATH=src .venv/bin/python scripts/validate_allocation_financing.py \
  --provenance settlements/grove/2026-08/provenance.json \
  --history-dir /path/to/diagnostic-history \
  --debt-control /path/to/grove-debt-control.json \
  --deduction-owners /path/to/grove-deduction-owners.json
```

The debt-control argument is a JSON list of daily `day`/`by_ilk` records. The owner file is a JSON object mapping deduction fields to `0x`-prefixed ilk IDs. Both are required together. The checked August inputs are embedded under each prime in the evidence JSON; future periods need their own independently checked debt and deduction ownership. `--capture-idle-only` captures the daily idle inputs without loading or replaying history. The output is diagnostic `financing.json`, never the source provenance file.
