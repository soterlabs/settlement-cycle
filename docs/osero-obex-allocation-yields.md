# Osero and Obex monthly allocation yields

The reporting workflow accepts one prime independently of Spark/Grove. It reuses settled revenue and daily financing rates, and never rewrites settlement reports or daily API revenue.

- Obex V1: Maple syrupUSDC. Carry original principal through deposits, redemptions and custody; appreciation is not new borrowing.
- Osero O1: SparkLend spUSDS. Deduct its exact daily share of idle pool USDS from original principal before charging the existing rate.
- Osero O2: idle USDS at the ALM is exempt. No positive exposure means no APY, rather than a fabricated zero yield.

Net APY uses retained venue revenue minus the allocation's borrowing cost. Prime-level financing differences, including MSC debt without invested proceeds, are not distributed across allocations to force a match.

## Reproduce September

For each `PRIME` (`osero` or `obex`), capture history with `validate_allocation_capital.py --capture-only --provenance reconciliation/monthly-allocation-controls/PRIME/2026-09/provenance.json`, then capture deductions with `validate_allocation_financing.py --capture-idle-only --block-resolver hypersync` against the same control and history directory.

Use `report_allocation_capital_readiness.py --prime PRIME --month 2026-09 --history DIR/history.jsonl.gz --idle-deductions DIR/idle_deductions.json --settlements-root reconciliation/monthly-allocation-controls --output READY.gz --markdown READY.md`. This requires a manifest matching the prime, month-end pins and history hash, including for August.

Then run `report_allocation_yields.py --month 2026-09 --settlements-root reconciliation/monthly-allocation-controls --readiness READY.gz --output YIELDS.json --markdown YIELDS.md`. It loads only the primes present in the readiness artifact. The existing publisher validates the matching capital and revenue hashes before importing a new immutable monthly API revision.

The saved September controls reproduce the Osero/Obex settled revenue and borrowing costs. They come from the independently validated full-month September run; neither prime was affected by the later Spark/Grove SOFR or S1/E10 selective refresh.

## Resolver review finding

The historical RPC/Dune boundary cache can contain early-day blocks. For September 1, RPC returned Ethereum block 25879436 (02:26:59 UTC), whereas HyperSync returned 25885878 (23:59:59 UTC). September 2 similarly returned 25886373 (01:39:23) instead of 25893051 (23:59:59). Such inputs fail the daily idle-control comparison and must not be published or scaled to fit.

Idle capture now records its resolver and does not reuse a capture made by a different resolver. September captures use HyperSync and must still reproduce every saved daily idle deduction. This change does not repair or overwrite the shared historical RPC cache.
