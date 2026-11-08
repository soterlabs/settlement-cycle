# Spark September RLUSD route audit

This investigation identifies the destinations missing from the saved capital
replay. It does not publish API data, change settlement scope, or recalculate
conditional allocation borrowing costs.

## Confirmed routes

The scan covers Ethereum Spark ALM Proxy
`0x1601843c5e9bc251a3272907010afa41fa18347e`, September 1–30, 2026 UTC.
Pinned boundaries are blocks **25,878,704** (August 31 23:59:59 UTC) and
**26,093,737** (September 30 23:59:59 UTC).

| Counterparty | RLUSD out of ALM | RLUSD into ALM | Outgoing transfers |
|---|---:|---:|---:|
| Sentora RLUSD Morpho vault, `0xfc8c624b6080a0a780583799f2a862de936f6e22` | 249,999,653.513509 | 3,160.267309 | 2,525 |
| SparkLend spRLUSD, `0x59275fb72c8004f44ba44432e25082932fd677f1` | 10,000,000.000000 | 2,086.692136 | 1 |
| Uniswap V4 PoolManager, `0x000000000004444c5dc75cb358380d2e3de08a90` | 0 | 103,271,075.850710 | 0 |
| Configured RLUSD incentive payer, `0xb70e0fad7d0cf4c187365a84e728802ab24ca78b` | 0 | 1,373,208.000000 | 0 |

The PoolManager receipts are movements of underlying tokens; they are not
classified as revenue by this scan. Likewise, the table records cash returned
from the vaults without assuming it is all earnings or all principal.

All **2,525 Sentora deposits** match, within each transaction:

1. RLUSD transferred from the ALM to Sentora;
2. a Sentora `Deposit` event naming the ALM as owner, with exactly the same assets;
3. the matching number of Sentora shares minted to the ALM.

The September 23 SparkLend deposit similarly transfers **10M RLUSD** to spRLUSD
and mints **10M spRLUSD** to the ALM. Separate spUSDC redemption and Sky debt
repayment legs in that transaction are not confused with the RLUSD deposit.

Pinned contract calls confirm `Sentora.asset()` and
`spRLUSD.UNDERLYING_ASSET_ADDRESS()` both equal RLUSD. Sentora identification
also agrees with `src/settle/revenue/spark_completion.py`; spRLUSD is S70 in
`config/spark.yaml`.

## Full-month raw RLUSD balance control

- Opening ALM balance: **251,703,857.665156950129832429 RLUSD**.
- Closing ALM balance: **96,353,734.961802937276127564 RLUSD**.
- Sum of incoming less outgoing transfers:
  **−155,350,122.703354012853704865 RLUSD**.
- Difference against the independently read balances: **exactly zero**.

The joined scan contains 70,029 distinct logs in 3,085 transactions. The saved
receipt checks for the initial four transactions independently agree with the
indexer evidence. Logs are deduplicated by block/log index; conflicting copies
fail validation.

## Link to the old replay's residual costs

Neither receipt-token account appears in the saved replay's venue mapping.
Consequently, observed RLUSD outflows are retained in synthetic
`unallocated:ethereum:<tx>` accounts instead of attributed to the actual receipt
positions.

| Verified destination | Associated September cost in old residual accounts |
|---|---:|
| Sentora RLUSD vault | $355,939.00 |
| SparkLend spRLUSD | $7,406.66 |
| **Total** | **$363,345.66** |

This identifies transaction routes associated with **90.44%** of the previous
$401,755.11 net gap. It is **not** a new calculated allocation-cost total. The
remaining arithmetic difference after tagging these accounts is $38,409.45,
but deductions, returned principal, Savings refinancing and subsequent
reinvestment still need to be reflected in the corrected calculation.

The earlier $358,974.57 RLUSD estimate grouped transactions by *net negative*
RLUSD movements in the normalized history. This new $363,345.66 association
uses actual outgoing transfer legs: 36 deposit transactions with net-positive
RLUSD movements add $4,373.75 of associated costs, while $2.66 of the old
net-negative grouping is outside these September deposit routes. This is a
refined classification, not additional borrowing cost.

## Representative transactions

- [September 14: 10M RLUSD → Sentora](https://etherscan.io/tx/0xbb31c35cbfa6aa02ab4287bb17c098e80a59d44d370223b36dd1ba55edd52196)
- [September 14: another 10M RLUSD → Sentora](https://etherscan.io/tx/0x3bb72d788410d84710283cb7d21b3810dd7ebee9e19a326caea24f702c7d37f7)
- [September 14: 9.138889M RLUSD → Sentora](https://etherscan.io/tx/0x5c949b68dc4e153ba4301e0dcc1f725176b60bf8f47d4ef9a5c4cf89eac50cfb)
- [September 23: 10M RLUSD → SparkLend](https://etherscan.io/tx/0x109d7551495c45ee316fbfd5b03a464e108306838bdc0a4eada69bfb2127d0f1)

## Implementation follow-up

Add these two receipt positions to the capital-tracing coverage, including
withdrawals, without silently broadening the MSC revenue perimeter. Sentora is
currently instantiated separately for reporting; S70 is explicitly
`settlement.enabled: false` and `cof_excluded: true` in the current config.
Those flags must not cause its borrowed capital to disappear from debt
reconciliation, nor should this investigation silently override settlement
policy.

The source history already contains the RLUSD legs. Add verified receipt
movements, preserve their inherited Sky/Savings funding, then reprice the saved
funding calculation and compare deductions to the global control. Keep financing
accounts separate. No source attribution should be invented from closing NAV.

## Reproduce the saved-evidence audit

```sh
PYTHONPATH=src python scripts/audit_spark_rlusd_routes.py \
  --evidence-dir /tmp/spark-september-rlusd \
  --saved-dir /tmp/spark-september-financing-results \
  --control /tmp/september-allocation-inputs/spark/provenance.json \
  --output reconciliation/spark_rlusd_routes_2026_09.json
```

The JSON contains SHA-256 hashes of all inputs, matched counts, counterparties,
representative receipt amounts and associated old residual costs. Raw indexer
logs, RPC receipts and pinned calls are retained locally in the evidence folder.
The evidence was collected with the existing RPC/indexer credentials; none are
included in the artifacts. A full historical replay was not run.

## Completed withdrawal and receipt-token scan

An independent September scan of **both receipt tokens** at the ALM found
2,533 transactions, including two not present in the RLUSD cash-transfer scan.
These are reserve-treasury transfer transactions: a zero spRLUSD transfer on
September 14 and **113.727666594174938539 spRLUSD** from
`0xb137e7d16564c81ae2b0c8ee6b55de81dd46ece5` on September 28. The latter is a
receipt without a borrowed-capital deposit; preserve it separately from funding.

All cash withdrawals match their position events:

| Venue | UTC date | RLUSD returned to ALM | Transaction |
|---|---|---:|---|
| Sentora | Sep 21 | 1,063.120318690079158814 | [0x40356e…](https://etherscan.io/tx/0x40356e99d555e16c0077f4022cf4d36c5912e52b5b43ba1427bd874575d99337) |
| Sentora | Sep 26 | 1,042.974252488902205921 | [0x021e0b…](https://etherscan.io/tx/0x021e0b3cf9d85da97c974a99bade24f02beadf5b6f534534f26bb5a170872567) |
| SparkLend | Sep 28 | 1,023.368960704561673960 | [0x22aa77…](https://etherscan.io/tx/0x22aa775e7f711c0f2f20b0f2e8d2264795c934ae119275d4d632f4092c2e4d3f) |
| Sentora | Sep 29 | 1,054.172737530485892533 | [0x498683…](https://etherscan.io/tx/0x498683133d094154aa638d62121970d4486ce724ce3d293fdd04aa6c01901f80) |
| SparkLend | Sep 30 | 1,063.323175363696712982 | [0x8ffb80…](https://etherscan.io/tx/0x8ffb807ed5693fb86161f27cef2a2e90ab02dd49f0a6302f53d4b58a52ae690f) |
| **Total** | | **5,246.959444777725644210** | |

Sentora's three Withdraw events match both RLUSD cash and burned vault shares.
SparkLend's September 28 withdrawal emits **Mint**, because crystallized interest
exceeds withdrawn cash. Its cash is `balanceIncrease − value`, not a deposit.
The September 30 Burn has cash `value + balanceIncrease`. Existing
`aave_reconstruct.py` already supports this distinction; the new audit tests it
against the cash evidence. Raw Transfer sums alone would misclassify these events.

Position controls also match exactly, to the smallest unit:

- Sentora had no deployed code at the opening pin (`eth_getCode == 0x`), so its
  opening position is zero. Net share transfers reconcile to closing
  **249,995,668.454942237589116344 shares**.
- spRLUSD opening scaled balance is independently zero. Mint/Burn/BalanceTransfer
  reconstruction matches the closing RPC scaled balance exactly. Applying the
  pinned liquidity index also matches `balanceOf` exactly:
  **10,000,313.309428039543889326 spRLUSD**.

There is no remaining unidentified share-balance change within these two
positions for September. This is a custody/position reconciliation, not yet a
recalculated funding split. Withdrawals can include earned value; neither the
$5,246.96 returned cash nor the treasury receipt should simply be subtracted
from the $38,409.45 borrowing-cost residual. Returned RLUSD also continues to
carry cost while held as non-USDS cash, subject to its funding source.

The next calculation must carry inherited funding into these positions,
handle withdrawals and reinvestment consistently, and keep earned value and
treasury receipts out of borrowed principal.

```sh
PYTHONPATH=src python scripts/audit_spark_rlusd_positions.py \
  --evidence-dir /tmp/spark-september-rlusd \
  --output reconciliation/spark_rlusd_positions_2026_09.json
```

The saved output includes all five withdrawal events, the receipt transfers,
closing share controls and hashes of the additional evidence. No API or
settlement values were changed by this scan.
