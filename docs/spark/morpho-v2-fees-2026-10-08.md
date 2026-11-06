# Spark: Morpho V2 fee mints were missing from capital-origin accounting

Two Spark USDT vaults use Morpho Vault V2, whose fee-event format and emission
order differ from MetaMorpho V1. The tracer only recognized the reviewed Base
V1 vault. Consequently, earned fee shares from S11 (`0xc7cd…bd22`) and S65
(`0xb0c4…5b91`) appeared as unexplained capital or altered a net withdrawal.

The [May 7 migration spell test](https://github.com/sparkdotfi/spark-spells/blob/d67876686f82656710ca1baa352ddab65d49907d/archive/20260507/Spell_20260507.t.sol)
explicitly verifies that both vaults have the Spark Ethereum ALM as their
performance-fee recipient and a 10% performance fee. The
[V2 contract](https://github.com/morpho-org/vault-v2/blob/f19803940f3d690a6dc6d4a0a2edfa39fc5203aa/src/VaultV2.sol#L609)
emits `AccrueInterest(previousAssets, newAssets, performanceFeeShares,
managementFeeShares)` **before** its nonzero fee mints. V1 emits its two-word
accrual event after the fee mint. These are distinct authenticated patterns.

The tracer now fetches both event signatures for the explicitly reviewed vault
addresses. It checks each actual mint's adjacent log position, token, recipient,
transaction and exact units. It separately handles performance and management
fees, including a different recipient whose mint is absent from a holder-filtered
query. Ordinary ERC4626 deposit mints are not fees.

There was also a withdrawal interaction: the old exact-cash branch required
`burnedShares == -netShareChange`. A fee mint in the same transaction invalidates
that equation. The correct condition is
`burnedShares == feeShares - netShareChange`. When no simultaneous deposit exists,
the tracer now uses the actual withdrawal assets/shares ratio for the opening
holding and fee gift, so the outgoing amount is exactly the observed cash.
This also works when the fee mint exceeds the burn and net shares increase.

## Concrete example

[May 8 execution](https://etherscan.io/tx/0x060d7433083e8ea93a82edb44f96f6a1e2231b2dd6ba67deda2b3106ac1ac822):

- S11 earns 5.408494053834774235 fee shares, worth 5.426742 USDT at the
  withdrawal's execution ratio.
- Two withdrawals return 49,547.393188 USDT.
- The old snapshot has zero fee income and a net position change of
  −49,541.91992599397 USD. It combines missing fee classification with a rounded
  one-share valuation instead of the exact cash exit.
- The same transaction returns 105,041,707.207647 USDT to Savings V2. Correcting
  the Morpho leg does not resolve the separate saver-funding/refinancing model.

Full receipts for this old-vault example and an August 1 new-vault example are
retained in `tests/fixtures/spark_morpho_v2_fee_examples.json.gz`. Regression
coverage checks the real omitted fee mints, both net-burn/net-mint withdrawals,
unchanged total borrowed basis, other recipients, deposits and malformed events.

## Existing tracing history

`scripts/repair_spark_morpho_fee_history.py` can repair a **separate copy** of the
old normalized history using the complete canonical share/fee/deposit/withdrawal
stream for these two vaults. It verifies the old movement against reconstructed
share units, reuses recoverable original prices, and uses actual proceeds for
the exact-withdrawal branch. It rejects missing inception units, conflicting
logs, already-classified income, unprovable prices and unmatched fee events.

The original history is immutable. The repaired copy has a new diagnostic
fingerprint and records input hashes, including its source history and fee
recognizer. It is explicitly **not** labeled a fresh extraction of all venues.
Debt fields and unrelated movements are copied unchanged. Both this repair and
the fresh normalizer fix affect only the allocation-capital tracer; published
revenue, API data, settlement totals and global borrowing costs are untouched.

The complete query through Ethereum block **25,878,704** returned **93,689**
events. The repaired diagnostic input changes **14,448 positions in 14,447
transactions**, recognizing **53,649.185061 USD** of historical earned fee
shares. This cumulative figure is not August revenue or a settlement adjustment.
One opening S65 deposit at block 25,274,077 required an explicit exact-block
normalizer quote (1.005683 USD/share), retained alongside the canonical events.
The other prices were recoverable from the old snapshot or exact withdrawal
cash/share quantities. The repair audit is
`reconciliation/spark_morpho_fee_history_repair_2026_08.json`.

Reproduce with the original immutable diagnostic history:

```sh
PYTHONPATH=src .venv/bin/python scripts/repair_spark_morpho_fee_history.py \
  --history /tmp/pr215-resumed-spark/history.jsonl.gz \
  --events tests/fixtures/spark_morpho_v2_fee_history.json.gz \
  --unit-prices tests/fixtures/spark_morpho_v2_fee_unit_prices.json \
  --output /tmp/spark-fees-repaired.jsonl.gz \
  --audit /tmp/spark-fee-repair-audit.json
```

The complete unit suite passed 1,771 tests before adding the final explicit-price
regression; all 24 focused normalizer/repair tests pass with that addition.
The final financing replay will consume this repaired input plus the previously
committed bridge, issuer and reserve-gift adapters. No aggregate borrowing-cost
reconciliation improvement is claimed until that replay completes.
