# Both remaining outflows are E11 LP valuation differences

The two residuals left by the swap audit are fully reproduced from actual
ALM/pool cash transfers, LP mint/burn transfers, and block-pinned pool reserves
and total supply. E11's existing Method B price is the sum of USDC and AUSD
reserves at par divided by LP supply. **It is not Curve's virtual price.**

| Execution | Cash paid/received | LP marked value | Residual |
|---|---:|---:|---:|
| February 4 deposit, block 24380799 | 24,998,000.000000 | 24,997,998.29798544 | 1.70201456 |
| August 20 final withdrawal, block 25797582 | 5,002,074.106834 | 5,002,074.12215344 | 0.01531944 |

Deposit transaction: https://etherscan.io/tx/0x1530367e2696dfb97ba380e63ab899d25b9ed3e79217666906cbbbdab024f20b

Withdrawal transaction: https://etherscan.io/tx/0xddf938b6315808e72d826a6361b2e5ddb1fb498ecc3862df10f288dfb8d35346

The deposit sent both stablecoins directly to the configured pool and received
LP tokens. The withdrawal burned the remaining ALM LP tokens and returned both
stablecoins directly to the ALM. Neither difference represents money sent to
an unidentified address. We call them valuation differences, not a claim that
a specific quoted protocol fee equals each residual. After the final burn the
pool still has a small seed supply, whose reserve-based unit price is used by
the existing normalizer to mark the burned units.

Together with the swap audit, this identifies the economic shape of all 239
raw outflow residuals in the August benchmark. It **does not certify the funding
split**: three unidentified incoming payments remain, and financing retained
for execution expenses/realized losses must be kept separate from investments.
No capital movements, global CoF, settlement revenue, or API values are changed.

Reproduce:

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_grove_lp_residuals.py \
  --events tests/fixtures/grove_curve_lp_residual_events.json \
  --swap-audit reconciliation/grove_swap_execution_shortfalls_2026_08.json \
  --output /tmp/grove-lp-residuals.json
```

The fixture contains full transaction logs and raw historical `balances(0)`,
`balances(1)`, `totalSupply()` and virtual-price reads. The virtual prices are
retained for evidence but are deliberately not substituted for Method B.
