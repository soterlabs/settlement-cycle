# Actual swap execution explains 237 of 239 outflow residuals

The capital replay leaves residual funding when the stablecoins received from
a swap are worth less at par than the stablecoins paid. Those amounts are
execution shortfalls, not cash sent to an unidentified allocation.

The read-only audit verifies the pool events against exact token transfers at
Grove's primary ALM and PAU. It checks 95 RLUSD/USDC Curve swaps and 150
AUSD/USDC Uniswap V3 swaps. The Curve pool's precise authorizing spell is linked
in `normalize/allocation_curve_swaps.py`; the Uniswap pool and tokens are the
configured E12 pool. Router calls must still match the actual ALM payment and
receipt. Conflicting/missing evidence fails validation.

**237 of the 239 raw outflow residuals** match verified execution shortfalls,
totaling **$213,307.785713401139395562**. This includes 84 RLUSD/USDC Curve swaps, five AUSD/USDC Curve swaps,
and 150 Uniswap swaps. Two transactions combine a Curve swap and a Uniswap
swap; their combined shortfall must explain the whole transaction residual. The December STAC/FalconX/Curve multicall is identified by its
original transaction hash despite its reviewed adapter suffix. Tiny normalization
rounding differences remain visible in the output; matching tolerates less than
$0.00001, not an accounting materiality adjustment.

Two residuals remain: E11 Curve LP entry and final exit valuation differences
of $1.7020145572484927291 and $0.015319437502490759669. Other observed swaps are retained separately when their
shortfall does not explain the whole transaction residual. In particular, one
sub-cent Curve cost was already treated as rounding, the combined swaps are now matched together.

This audit **does not assign borrowing costs to expenses or change the allocation
sum**. An execution shortfall is not necessarily all debt-funded if the money
traded included earnings. Nor does the pool's quoted fee explain all the loss:
price impact and stablecoin execution prices also matter. No fake asset, idle
cash exemption, MSC charge or balancing credit is introduced.

Reproduce against a financing replay:

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_grove_execution_shortfalls.py \
  --financing /tmp/pr215-grove-bounds-financing.json \
  --curve-events tests/fixtures/grove_curve_execution_events.json \
  --uniswap-events tests/fixtures/grove_uniswap_execution_events.json \
  --ausd-curve-events tests/fixtures/grove_ausd_curve_execution_events.json \
  --period 2026-08 \
  --output /tmp/grove-execution-shortfalls.json
```

Evidence: `reconciliation/grove_swap_execution_shortfalls_2026_08.json`, including
each matched trade, exact residual, normalization difference, and remaining two
transactions. The three unknown receipts remain separate. GROVE-A's full
$0.143297 financing difference is already explained by its two PAU swaps;
BLOOM-A still requires funding attribution and expense financing reconciliation.
