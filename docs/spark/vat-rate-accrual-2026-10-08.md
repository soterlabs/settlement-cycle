# Spark: historical Vat rate accrual is not allocated cash

The combined replay at `26013f9` completed successfully. Its follow-up numerical
bridge failed because it assumed that cash draws minus repayments must equal
non-MSC debt. That equality omits historical `Vat.fold` accrual.

The independent August control exceeds the saved cash-draw balance by
**75,612,301.038815520442580349418127135701354204000940874 USDS** on every
August day. August's day-to-day debt changes agree. No nonzero Frob block in the
independent debt inventory is missing from the capital history.

## Mechanism and evidence

`Vat.fold(ilk, surplus_destination, delta_rate)` changes the ilk rate and credits
`Art * delta_rate` to the surplus destination. It increases debt without sending
capital to the ALM. See the [Vat implementation](https://github.com/makerdao/dss/blob/fa4f6630afb0624d04a003e920b0d71a00331d98/src/vat.sol#L238).
Historical Spark stability fees are explicit in the
[January 23 spell](https://github.com/sky-ecosystem/spells-mainnet/blob/17926e1879ca79d017c7e42525825907d8f673b1/archive/2025-01-23-DssSpell/DssSpell.sol#L113)
and [February 6 spell](https://github.com/sky-ecosystem/spells-mainnet/blob/17926e1879ca79d017c7e42525825907d8f673b1/archive/2025-02-06-DssSpell/DssSpell.sol#L124).
The audit uses actual event deltas, not these advertised annual rates.

The event proof in `audit_spark_vat_accrual.py`:

1. Starts from independently read zero Art and the initialized rate immediately
   before the first cash draw, at block 21,215,062.
2. Processes canonical Frob, Grab and Fold logs in block/log order. Integer RAD
   arithmetic separates cash draws, non-MSC rate accrual, and grab-origin MSC
   debt. Unknown nonzero grabs or unexpected surplus destinations fail.
3. Checks every nonzero Frob transaction's cash against the saved capital
   history, rather than deriving cash or accrual from the desired closing gap.
4. Reproduces pinned Art and rate at block 25,878,704, then independently checks
   every August day's total debt, rate and prior/current MSC components.

The diagnostic bridge accepts this separately reconstructed accrual only when
its cash series matches the replay and cash plus accrual matches the debt
control. Missing logs, changed cash or altered controls still fail. Historical
rate accrual is not inserted as borrowed principal in an allocation.

## Scope

This is a read-only financing explanation. Global borrowing charges, debt,
monthly settlements and API revenues remain unchanged. Grab-based MSC financing
remains a separate component. The accrual component is a cumulative debt
identity term, not a claim that no interest has ever been repaid: all actual
cash repayments remain in the cash series.

The full replay's modeled August allocation costs are **2,333,198.918893 USDS**,
versus global costs excluding MSC of **5,711,428.495958 USDS**. These modeled
costs remain uncertified because Savings V2 and other funding attribution are
unresolved. A numerical bridge does not resolve their provenance.

## Verified result

The proof validates **166,546 logs**: 83,287 Fold, 83,248 Frob and 11 Grab,
plus **74,694 nonzero cash transactions** from the saved capital history.
Every August daily debt/MSC control and the closing RPC state matches.

- First non-cash increase: **20.485596907649 USDS**, November 18, 2024,
  block 21,215,179,
  [transaction](https://etherscan.io/tx/0xba320595a1f70aac3f43fe09cd09732b5cc401539411ea2f99859d6acdda8542).
- Last non-cash increase: **3,243.059691729810 USDS**, September 10, 2025,
  block 23,333,715,
  [transaction](https://etherscan.io/tx/0x0b8efd5a3622d682a1d5d53ff52227993dea4b62e5c41dd21faf748ce04cef5f).
- Closing rate: **1.045310470203353538792275155**. Subsequent zero Fold
  changes do not increase accrued debt.
- Cumulative non-MSC accrual: **75,612,301.038815520443 USDS**, exactly the
  previously unexplained debt difference on every August day.

The additional August financing attributable to this debt-identity component
is **234,768.881402 USDS** at the existing daily blended rates. It is already
inside global charges, not a newly imposed charge or an allocation deposit.

Reproduce the independent proof without RPC or a capital replay:

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_spark_vat_accrual.py \
  --evidence tests/fixtures/spark_vat_accrual_events.json.gz \
  --debt-control tests/fixtures/spark_vat_accrual_debt_control.json \
  --output /tmp/spark-vat-accrual.json
```

The result is committed in `reconciliation/spark_vat_rate_accrual_2026_08.json`.
The general numerical bridge accepts the same raw fixture through
`--vat-evidence` and reconstructs it independently. Missing/duplicate events,
changed cash, mismatched control debt, and absent proof days are covered by
regression tests. No amount is assigned merely to make a comparison pass.

## Rechecked numerical bridge

The saved, hash-verified replay checkpoint was reused; no full replay or
settlement regeneration was needed.

| August component | USDS |
|---|---:|
| Modeled allocation costs | 2,333,198.918893 |
| Financing in accounts outside the allocation subtotal | 3,481,943.859777 |
| Historical Vat-accrual financing | 234,768.881402 |
| Modeled versus global deduction difference | −338,483.164113 |
| Global borrowing costs excluding MSC | 5,711,428.495958 |

The unrounded numerical remainder is **−1.40e-19 USDS**. Missing asset-basis
financing and modeled-cost inconsistency are also below 1e-19. This is a
conditional numerical identity, **not certified per-allocation reconciliation**.

Of the outside subtotal, **3,389,037.311478 USDS** sits in unallocated
accounts. Other components are PSM custody (80,594.213776), CCTP custody
(1,804.523678), NFT accounts (10,507.810313), and rounding (0.000532). These
account categories describe where the model currently holds basis; they do not
establish the economic purpose of every flow. In particular, do not relabel
the whole unallocated amount as Savings V2 without further tracing.

The deduction difference is independently recomputed from the modeled account
basis/venue deductions and the published global deduction ownership. It is not
a new discount or a balancing adjustment. Funding attribution and deduction
ownership must be resolved before certification; eligible allocation costs
still remain zero. Exact results and input/code hashes are in
`reconciliation/spark_financing_numerical_bridge_2026_08.json`.
