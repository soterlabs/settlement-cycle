# Spark replay checkpoint: native routes, BUIDL and Ethereum Morpho fees

The full inception-to-August replay completed using code commit `063186a` and
the explicitly repaired Ethereum Morpho V2 input. This checkpoint precedes the
Base Morpho exact-cash, nested fsUSDS and June Base withdrawal fixes; a second
replay with those corrections is running. The comparison is against the earlier
replay that already propagated funding uncertainty, not the obsolete $12,080.98
eligible-cost figure from before that protection.

| August borrowing cost, USDS | Before | This checkpoint |
|---|---:|---:|
| Modeled allocation costs (uncertified) | 2,261,986.710840 | 2,320,839.345281 |
| Eligible allocation costs | 0 | 0 |
| Global borrowing costs | 6,108,910.339051 | 6,108,910.339051 |
| MSC exclusion | 397,481.843094 | 397,481.843094 |
| Global costs excluding MSC | 5,711,428.495958 | 5,711,428.495958 |

The zero eligible subtotal is a funding-provenance limitation. It does not mean
that no capital or modeled cost was traced. Remaining unidentified funding and
Savings V2 refinancing affect the active positions, so their modeled costs have
not been promoted into eligible net APY or a certified reconciliation.

| Historical unmatched flows, inception through August | Before | This checkpoint |
|---|---:|---:|
| Incoming transaction count | 42,500 | 35,578 |
| Incoming gross amount, USD | 20,928,160,891.66 | 17,811,074,442.19 |
| Outgoing transaction count | 24,212 | 24,464 |
| Outgoing gross amount, USD | 20,265,130,026.27 | 17,150,844,409.65 |

The fixes remove approximately **$3.117 billion of incoming** and **$3.114 billion
of outgoing** historical discrepancies. These totals include repeated capital
turnover; they are not revenue, outstanding debt, monthly borrowing costs, or
settlement adjustments. The outgoing count increases because 275 newly visible
small residuals total $229.626931, while 23 former large residuals disappear.
Those small residuals remain evidence for further investigation; they are not
silently declared fees or losses.

The Savings cash inventory now exactly matches, within one cent, 7,861 whole
incoming residuals totaling $13,649,638,162.72 and 8,910 whole outgoing residuals
totaling $13,388,283,665.49. This identifies vault funding/returns without
classifying them as income. Some other large residuals also contain Savings
cash plus a small valuation difference. Therefore the artifact calls its next
list `largest_residuals_not_wholly_matched_to_savings`, not “non-Savings flows.”

Drawn and repaid amounts by ilk are identical. The published August provenance
hash, daily global costs and MSC exclusion are identical. No published report,
API revenue, debt, or rate was regenerated. The next essential modeling step is
separate saver funding, accrued saver obligations and source replacement when
Sky-funded cash repays savers; see `savings-liability-2026-10-08.md` and the two
exact refinancing/separate-route witnesses.

## Reproduce the comparison

```sh
PYTHONPATH=src PYTHON_DOTENV_DISABLED=1 .venv/bin/python \
  scripts/compare_spark_financing_snapshots.py \
  --before /tmp/pr215-spark-current-financing.json \
  --after /tmp/pr215-spark-current-fixes-financing.json \
  --control settlements/spark/2026-08/provenance.json \
  --savings-events tests/fixtures/spark_savings_v2_funding.json.gz \
  --output /tmp/spark-comparison.json
```

`reconciliation/spark_native_and_morpho_financing_2026_08.json` records exact
figures, replay/input hashes and remaining examples. The comparison fails if
published controls, per-ilk draws/repayments, global costs or MSC amounts differ.
