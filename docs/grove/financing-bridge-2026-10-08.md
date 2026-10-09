# Grove's remaining modeled cost difference is numerically accounted for

This is an August 2026 diagnostic over the pinned historical extraction. It is
not a new settlement, and it does not certify allocation origin labels while
three unidentified receipts remain. It distinguishes money invested in venues
from debt still owed after execution costs or realized losses.

| Component, USDS | ALLOCATOR-BLOOM-A | ALLOCATOR-GROVE-A |
|---|---:|---:|
| Modeled allocation borrowing costs | 3,448,147.745142 | 11,784.593973 |
| Financing in verified swap/LP residual accounts | 639.220386 | 0.143297 |
| Financing in rounding accounts | 0.000032 | 0.000000 |
| Financing on debt no longer carried by asset basis | 155.024469 | 0.000000 |
| Allocation versus global deduction difference | 0.000000 | 0.000000 |
| Global borrowing costs excluding MSC | **3,448,941.990029** | **11,784.737270** |
| Unexplained numerical difference | **< 0.00000001** | **< 0.00000001** |

The calculation independently checks observed draws less repayments against the
non-MSC debt control every day. It sums borrowed basis over every ledger account,
separates accounts represented in allocation rows, and applies the unchanged
published daily blended borrowing rate. It recomputes the allocation deductions
from the saved venue/SDE/idle inputs. A wrong modeled cost remains an unexplained
difference; it is not absorbed into a deduction or balancing adjustment.

The 239 raw outflow accounts have separately been identified: 237 transaction
residuals from actual stablecoin swap execution and two LP valuation differences.
See `swap-execution-shortfalls-2026-10-08.md` and
`lp-valuation-residuals-2026-10-08.md`. Their gross nominal shortfalls are not
assumed to be wholly borrowed: the bridge uses the funding actually retained
in each account by the ledger, with the same uncertainty as that ledger.

Realized principal loss at the end is 50,001.121827 USDS, principally the August
4, 2025 BUIDL redemption and a sub-cent Agora redemption difference. Losing
asset value does not repay the ilk. Therefore allocation-only investment costs
cannot equal the debt-based control without separately acknowledging financing
of expenses/losses. No artificial investment or idle deduction was created.

## Remaining verification

Three incoming payments are still unidentified:

- May 7: 5 RLUSD and 49,591 RLUSD from `0xf9a7fb2887ad9d1b167a25832451b06df63b9f8d`.
- July 9: 1 USDC from `0xf417b3871a242930a264fca111a8a59c74d375d2`.

The $1 receipt happened after a Galaxy Warehouse onboarding test deposit, but
no direct transfer from the configured entrypoint to the payer was found in
that interval. Timing and amount alone are insufficient to link it. The RLUSD
payer also funds other contracts, but that does not establish the nature of its
payment to Grove. Neither receipt has been called a gift or returned principal
without evidence. User clarification has been requested for the RLUSD payments.

BLOOM-A's eligible subtotal therefore remains zero, despite the conditional
numerical bridge balancing. GROVE-A independently retains 11,784.593973 in
eligible allocation costs; its 0.143297 expense-financing difference is already
supported by its two exact PAU swaps. Keep PR #215 draft. Spark has now been replayed separately after the uncertainty changes; its
remaining limitations and measured progress are in
`../spark/financing-replay-2026-10-08.md`. It does not inherit these Grove conclusions.

## Reproduce

First generate the current financing JSON with the existing pinned replay.
Then:

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_allocation_financing_bridge.py \
  --history /tmp/pr215-grove-eoa/history.jsonl.gz \
  --financing /tmp/pr215-grove-bounds-financing.json \
  --control settlements/grove/2026-08/provenance.json \
  --debt-control /tmp/pr215-resumed-grove/debt-control.json \
  --idle-deductions /tmp/pr215-resumed-grove/idle_deductions.json \
  --output /tmp/grove-financing-bridge.json
```

The script verifies input hashes, reapplies reviewed historical adapters and
replays borrowed basis. Its output records the source hashes and unresolved
receipts. It neither modifies the financing JSON nor promotes modeled costs
into the eligible subtotal. Published reports and API numbers are untouched.
