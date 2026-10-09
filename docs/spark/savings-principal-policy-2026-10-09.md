# Savings refinancing: provisional principal policy

The operator authorized proportional attribution as a working assumption during
the October 8–9 investigation. This is a diagnostic model for PR #215, not a
change to published debt, borrowing charges, settlements or API revenues.

## Verified cash and contract arithmetic

All 841 historical `VsrSet` events across S56/S57/S59/S60 are saved in
`tests/fixtures/spark_savings_v2_rates.json`. Together with the existing complete
liability and cash fixtures they reproduce every emitted Drip and all four
pinned closing supplies, chi, rho, rates and totalAssets, using integer contract
arithmetic. Contract source: [SparkVault.sol at 51c6d7a](https://github.com/sparkdotfi/spark-vaults-v2/blob/51c6d7a1da85944804ba87234f2eac13dba8330e/src/SparkVault.sol).

A Take or plain return does not have to call `drip()`. Using the last emitted
Drip therefore understates interest at payment time. The new audit evaluates
`nowChi()` at each cash event, including the contract's ray exponentiation
rounding. It compares cumulative accrual before/after each event so a later
Drip cannot count the same interest again. Share rounding and direct other cash
remain separately identified in the existing closing-liability audit.

## Attribution assumption

Cash taken from Savings is non-Sky funding, never income or Sky debt. A return
retires principal and outstanding VSR interest proportionally. This policy is
not encoded by the vault itself. Exact payment-time accrual removes the apparent
historical overpayments: no prepaid balance/refund operation is needed in this
history. Future overpayments require separate handling, not a negative loan.

For the [May 18 transaction](https://etherscan.io/tx/0x3267f7a7508ad892778e1afafb965ecb12660d5d0c8e9fcc278210a19d07ccf6), the
399,989,732.847945 USDC return splits under that policy into:

- 398,895,575.396220 USDC saver principal;
- 1,094,157.451725 USDC VSR interest.

Sky funding paying saver principal can refinance the existing saver-funded
holdings. Funding paying interest belongs in a financing-expense account, not
in an investment's principal. Gains never become borrowed principal.

The policy produces closing non-Sky principal of approximately $297.38m USDC
Ethereum, $349.86m USDT Ethereum, effectively zero PYUSD, and $8.38m USDC
Avalanche. These are policy results, not additional Sky borrowing or certified
per-allocation balances. Full measured outputs and input hashes are in
`reconciliation/spark_savings_principal_2026_08.json`.

## Reproduction

```
PYTHONPATH=src .venv/bin/python scripts/audit_spark_savings_principal.py \
  --output /tmp/spark-savings-principal.json
```

The regression tests reproduce the complete rate history and reject a missing
rate change. The subsequent ledger replay must still qualify transaction-level
pooling where independent token routes have not yet been proved. A numerical
cash bridge alone does not establish allocation funding provenance.

## Ledger integration

`allocation_external_funding.py` keeps external lender balances separate from
`borrowed_by_ilk`. Transfers carry both classes of funding; a realized loss caps
their combined basis at the actual proceeds. Refinancing replaces the repaid
source proportionally across its remaining holdings. Where the original loan
funded a realized loss, replacement funding remains in a zero-value financing
account instead of inventing an investment. VSR payments also retain any Sky
basis in a separate zero-value financing-expense account.

`scripts/patch_spark_savings_funding.py` attaches the audited operations to a
separate frozen diagnostic snapshot, after the reviewed historical adapters.
It checks daily debt by ilk, preserves the input hash, and routes the verified
May 2 independent USDT branch separately. It does not enable this provisional
policy in fresh production extraction. Funding uncertainty bounds explicitly
decline these inputs until their solver can represent external refinancing;
ordinary conditional replay remains available. No publication is blocked.

The full pre-replay unit suite passed 1,857 tests; the additional repeated mixed
refinancing test also passes, checking conservation of each lender's principal.
