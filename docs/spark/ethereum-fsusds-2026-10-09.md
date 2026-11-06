# Ethereum fsUSDS: use its actual sUSDS underlying for capital tracing

The capital pricing fix for Base/Arbitrum fsUSDS did not cover Ethereum S17,
because the legacy report configuration names USDS as its underlying. Historical
`asset()` calls on `0x2bbe31d63e6813e3ac858c04dae43fb2a72b0d11` instead return
`sUSDS`, `0xa3931d71877c0e7a3148cb7eb4463524fec27fbd`, at every observed exchange.

Seven complete receipts and pinned `asset()`/`convertToAssets()` values are in
`tests/fixtures/spark_ethereum_fsusds.json.gz`. They cover three deposits in
April 2025 and four withdrawals in June 2025. Every vault event agrees with the
actual sUSDS transfer to/from Fluid's liquidity reserve
`0x52aa899454998be5b000ad077a46bbe360f4e497`.

For example, the [April 11 transaction](https://etherscan.io/tx/0x61b4b24b102e0b19f9aa791fd4ac0c41dfcf66d2829de2226c708b78bc57d6f0)
draws 10m USDS, deposits it into sUSDS, and deposits most of those sUSDS shares
into fsUSDS. The old tracing input valued the outer leg at only 9,545,103.97 USD.
Its correct cost is 9,995,812.97 USD, with the rest retained as sUSDS. The
450,708.99 USD apparent missing outflow was a unit-pricing error.

The normalizer now uses a capital-only corrected venue view for this exact
wrapper, converting both its position and raw vault-event assets to USD. The
report configuration and published valuation/revenue remain unchanged.

The diagnostic input repair changes only the wrapper's opening value and
movement in the seven witnessed batches. All debt fields and other assets are
unchanged. Every transaction now has a value residual below 1e-8 USD. The
aggregate old apparent outflow was 904,370.010831 USD; apparent receipts were
1,037,527.289903 USD. These are historical valuation discrepancies, not August
revenue or an estimate of the borrowing-cost impact.

`reconciliation/spark_ethereum_fsusds_2026_08.json` records every before/after
value and evidence hash. Regression tests verify actual underlying cash,
unchanged debt/other movements, rejection of the wrong asset or incomplete cash
evidence, and the new normalizer path. The complete suite passed 1,878 tests
alongside the SubProxy fix.
