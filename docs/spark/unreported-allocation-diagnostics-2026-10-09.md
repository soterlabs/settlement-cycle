# Funded allocations absent from historical revenue snapshots

Spark's current tracing configuration includes S66, the RLUSD/USDS Uniswap V4
position. Its first recorded movements are on 2026-08-27, in transaction
`0x7bb7ad0094a9e608c817c2254ae982f46c0b792090e4ffa4a6ac1b9913d86116`.
The published August revenue provenance includes S61 and S62 but has no S66 row.
Previously the financing diagnostic selected only reported venues and explicitly
declared tracing-only venues. S66's already-traced capital consequently remained
in the numerical bridge's outside-allocation NFT category.

The diagnostic now also includes configured, unreported allocations with funded
balances during the requested period. These rows expose modeled principal and
financing costs, but remain uncertified: revenue, certified cost, net PnL and APY
are unavailable. In particular, the missing historical revenue metadata cannot
establish the venue's complete exemption policy. The change neither invents a zero
revenue nor treats a modeled gross financing cost as the correct net charge.
Dormant unfunded allocations are not added. Existing reported allocations retain
their actual revenue and deductions.

As an independently checked baseline, the completed pre-Savings-policy checkpoint
assigned S66 an August average Sky-funded basis of 3,380,179.336569 USDS and
10,507.810313 USD of gross financing cost before venue deductions. This is an old
checkpoint diagnostic, not the updated replay result or a settlement adjustment.
The corresponding NFT IDs are 385168 and 385169, at position manager
`0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e`.

The separate saved daily deduction inputs **do** contain S66's idle-USDS
balances for August 27–31. These are used by the new diagnostic row, even
though the venue is absent from the revenue snapshot. At the unchanged daily
Sky rates, they represent **8,151.092172 USD** of deductions. Applied to the
old baseline above, the resulting modeled net cost is **2,356.718141 USD**,
not the 10,507.810313 gross figure. The new funded replay can change the
principal attribution; it reuses these same daily dollar deductions.

The completed overnight replay produces average Sky principal of
3,438,052.650675 USDS, gross financing of 10,687.733211 USD and modeled net
financing of 2,536.641039 USD. The published August control omits S66's
exemption as well as its venue row. Its extra modeled credit is therefore
offset in the numerical bridge's deduction adjustment: adding this diagnostic
row does not alter the historical payable charge. See
`overnight-financing-results-2026-10-09.md` for the complete reconciliation.

Completed funded replays can be projected through this reporting change without
replaying transactions. The projection must retain every existing allocation row,
debt control and funding movement unchanged; only previously missing provisional
rows are added. Published settlements and API outputs are untouched.

Validation: regression coverage for an omitted funded NFT, a dormant allocation,
and an already-reported NFT with an actual idle deduction; full unit suite:
1,926 passed, 1 skipped (seven existing warnings).
