# Three upstream sUSDS funding routes

This extends the October 26 original-funding diagnostic for S32 through
August 31, 2026. It does not change production revenue, debt, financing charges,
settlement or API data. Pre-2026 spell evidence establishes opening funding only.

| Contribution | Borrowed principal identified at contribution | Additional borrowed capital surviving at August-end |
|---|---:|---:|
| July 15 mixed USDS deposit | 53,983,649.23 | 11,054,773.33 |
| July 13 Optimism sUSDS return | 100,939,898.13 | 5,327,870.93 |
| July 13 Base sUSDS return | 71,503,201.44 | 2,796,860.42 |
| **Total additional August-end attribution** | | **19,179,504.69** |

Closing traced capital rises from **617,587,857.46 to 636,767,362.15 USD**
(**90.56%** of 703,133,724.15 USD), leaving **66,366,362.00 USD** unclassified.
This is original-funding coverage, not reconciliation of current borrowing costs.

The earlier “operations/Uniswap” description was incorrect: the second row is
an **Optimism native bridge withdrawal**, not a Uniswap position.

## July 15: mixed does not mean untraceable

[Transaction](https://etherscan.io/tx/0x90dce95b9d809344872e6a830feb6c7aa4e4fcd471c36f4e264979e7fd90bb7d)
combines a 53,983,649.231941 USDS Sky draw, 156,434.075183 USDS received from
a vault, and 33,974.469256 USDS opening cash into a 54,174,057.776380 sUSDS
deposit. Separately, Savings sends 2,131,023.208436 USDT to the ALM.

The optional mixed-prefix validator permits additional incoming USDS before
the deposit, but no intervening outgoing cash. It deducts every unclassified
receipt and the opening cash before attributing the drawn USDS. Vault
Draw/Wipe, signed Vat debt, mint, Buffer transfer, deposit, shares and closing
cash checks remain mandatory. The other 190,408.544439 USDS stays unclassified.
Of the cached 126 receipts, 108 now qualify and 18 still fail the proof.

## July 13: carry origin through authenticated custody

The existing adapters authenticate the July 6 withdrawals and July 13 arrivals
using exact canonical bridge messages, token movements and executed spells:

- [Base withdrawal evidence](base-native-withdrawals-2026-10-08.md)
- [Optimism/Unichain withdrawal evidence](op-unichain-withdrawals-2026-10-08.md)
- [Early Base funding spells](early-base-seed-2026-10-08.md)
- [June 2025 Optimism/Unichain funding spell](native-seed-2026-10-08.md)

`scripts/audit_spark_native_return_origins.py` replays borrowed basis through
the L2 USDS, sUSDS and Base Fluid positions and authenticated native-bridge
claims. It retains all other movements in each relevant batch so competing
uses constrain new-draw attribution. Untracked receipts and passive gains
create no borrowed basis. Ethereum S32 is excluded from this source ledger;
its two incoming returns receive exactly the basis released from their claims.
They do not mint new debt or receive both a direct-draw and a transfer credit.

The Base return delivers 211,709,381.80 USD of sUSDS, of which only
71,503,201.44 is traced borrowed principal. Optimism delivers 201,346,048.97,
of which 100,939,898.13 is traced. We do not classify either return fully as
borrowed. Their remaining origins, along with other residual contributions,
still need investigation. Later S32 withdrawals explain why only 8.125m of
these two newly traced origins remains at August-end.

## Reproduce and validate

```sh
PYTHONPATH=src .venv/bin/python scripts/audit_spark_savings_origin.py \
  --history /tmp/pr215-spark-v1-history.jsonl.gz \
  --receipts tests/fixtures/spark_susds_ordered_draw_receipts.json.gz \
  --start 2026-01-01 --end 2026-08-31 \
  --allow-mixed-prefix --include-native-returns \
  --output reconciliation/spark_susds_upstream_origins_2026_08.json.gz
```

Both options are explicit; the prior audit behavior remains available. The new
artifact includes both source and destination ledgers, route proofs, rejected
receipts, residual ranking, and input/source hashes. The compact normalized
source fixture retains all 106 relevant batches with every competing movement.
Regression tests cover actual source amounts, no invented basis without draws,
missing custody legs, partial cutoffs, mixed cash rejection, unchanged closing
capital and single-credit attribution. Existing independent bridge-message and
spell-seed tests also cover the underlying route adapters.
