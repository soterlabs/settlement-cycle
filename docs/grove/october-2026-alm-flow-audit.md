# Grove October 2026 ALM flow and booking audit

**Not all legitimate movements are booked or classified. The previous $38,944.45 pre-DR headline is incomplete and must not be used as the final settlement.** The scan found an unconfigured Base vault with approximately $1.30m of October income, unclassified cash/native movements, and smaller execution/pricing reconciliation differences. This audit records evidence; it does not force unresolved transfers into income or change the settlement engine.

## Scope and completeness

The scan covers `[2026-10-01 00:00:00, 2026-11-01 00:00:00)` UTC at six configured holders: Ethereum legacy ALM, Ethereum Diamond PAU ALM, Base ALM, Avalanche ALM, Plume ALM and the configured Monad holder. Basins, escrow wallets, subproxy treasury and unrelated interior EOAs are not treated as additional ALM proxies.

| Chain | ALM holder(s) | First October block | Last October block | Token transfers |
|---|---|---:|---:|---:|
| Ethereum | `0x491edf…a44e`, `0x0dcd92…a153` | 26,093,738 | 26,316,188 | 1,533 |
| Base | `0x9b746d…cecee` | 52,011,727 | 53,350,926 | 314 |
| Avalanche C | `0x7107dd…3644` | 96,488,900 | 99,082,938 | 0 |
| Plume | `0x1db91a…f812` | 96,561,571 | 102,924,353 | 0 |
| Monad | `0x94b398…ccbc` | 109,459,391 | 118,321,696 | 3 |

HyperSync queries selected every log mentioning a holder in indexed topics 1–3, without a token allowlist, and retained all sibling logs and transactions. Every page reaches its exclusive end block. Transfer identities are deduplicated and conflicting duplicates fail. The resulting **1,850 ERC-20 transfers** include **11 reviewed spam/counterfeit events**, leaving **1,839 non-spam transfer events**. No boundary ERC-721 or ERC-1155 movements were found.

All **16 active non-spam token/holder balance controls** reconcile exactly: opening raw balance plus incoming raw transfers minus outgoing raw transfers equals closing raw balance. This proves captured quantity conservation for those accounts, not that every economic movement was classified correctly.

Successful value-bearing native traces were independently queried on Dune across all five chains, including internal calls, creations and self-destruct transfers. The queries return one native receipt (AVAX). All **six native balance controls** reconcile after including the two Monad transaction gas expenses. Ethereum and Base HyperSync trace scans also found no value-bearing ALM calls. Transaction fees paid by external keepers are not expenses of the ALM holder.

## Material booking gaps

| Finding | October amount | Current treatment / required work |
|---|---:|---|
| Unconfigured Base Grove x Steakhouse USDC vault | **1,298,280.769218 USDC implied venue income** | Missing from `config/grove.yaml`, hence absent from the original October venue revenue. Add the venue and rerun the close before trusting the headline. |
| Six Ethereum USDC receipts, now identified | **2,057,003 USDC received** | Approved historical JAAA/JTRSY partner rebates: 915,857.20 to Grove and 1,141,145.80 to Sky. See the [receipt classification](october-2026-partner-rebates.md); do not book again on top of the existing approved adjustment. |
| Two genuine Monad USDC transfers | **152,288.127280 USDC sent** | Configured holder signs direct transfers to an unclassified destination. Requires a counterparty/asset-or-expense booking, rather than treating the outflow as spam or assuming it is principal. |
| Native Avalanche receipt, October 9 | **76,133.73 AVAX received** | Real native receipt, independently confirmed by RPC and traces. No configured venue/income classification or USD valuation exists. Preserve native inventory and resolve its economic classification. |
| E19/E23 Base vault exits | **15,310.572711 USDC aggregate difference** | Booked returns exceed cash withdrawals less exact opening `convertToAssets(full balance)`. Reconcile execution proceeds, fees and unit-price precision before accepting those venue returns. |
| 39 authenticated AUSD/USDC swaps | **−4,332.476098 USD net at the configured par convention** | Actual transfers prove +1,159.810081 gains and −5,492.286179 shortfalls. Existing cash venues carry zero revenue; reconcile these trading gains/losses separately from E12 LP income. This is an execution difference, not automatically a funding loss. |
| Native gas paid by Monad holder | **0.020509722559798944 MON** | Two receipt-proven gas charges exactly explain the native balance decrease. No dedicated MSC expense booking is established in this audit. |

The six receipts were subsequently identified by the approved statement in merged PR #226. Remaining counterparty classification requests are tracked in **QUESTIONS.md G30**. The audit does not assume the $152k outflow is an expense or the AVAX receipt is yield.

### Missing Base vault: independent amount control

Contract `0xbeef0786756810478b88982de00f3cd7fdb8e7c7` reports name **Grove x Steakhouse USDC**, symbol `grove-steakUSDC`, 18 share decimals, and the canonical Base USDC asset `0x833589fcd6edb6e08f4c7c32d4f71b54bda02913`.

| Control | USDC |
|---|---:|
| September 30 closing assets, full owned shares | 45,007,634.741392 |
| October deposits, 99 transfers | 451,185,223.899918 |
| October withdrawals, one transfer | 1,822,976.047858 |
| October 31 closing assets, full owned shares | 495,668,163.362670 |
| Closing − opening − deposits + withdrawals | **1,298,280.769218** |

Both share balances reconcile exactly to all 100 mint/burn transfers. These are actual ALM-owned shares and underlying cash movements, not a vault-wide TVL estimate. The income is a position/cash-flow measurement; final allocation, any applicable revenue split, and settlement effects must be recomputed through the configured pipeline.

The vault already held $45.0m at September-end. Consequently the earlier comparison of the two old Base vaults did **not** cover the full Base book. Their exit is consistent with migration into this missing position, rather than proof that Grove stopped earning on Base. September also needs a separate inventory-complete comparison before drawing a portfolio trend conclusion; this audit does not restate September.

### Unresolved receipts and outflows

The six USDC receipts come from the shared Coinbase Prime wallet `0xcd531ae9efcce479654c4926dec5f6209531ca7b`. The approved statement maps October 20’s three payments to **986,596 USDC of JAAA rebates** and October 21’s three to **1,070,407 USDC of JTRSY rebates**. These are historical partner rebates, not principal returns or Apollo proceeds. [Details and source evidence](october-2026-partner-rebates.md). The complete transaction identities remain in the transfer CSV.

The genuine Monad USDC outflows are **10.001130** and **152,278.126150** USDC, both to `0x2c58e28cb9b2b8afb7ed1f2235ca01e9b06e5755`. Transactions [`0x06d525…956bd`](https://monadscan.com/tx/0x06d525b9f77439b3a88f55e63239ec0bf2a8e33da7a630fb4f59ceac317956bd) and [`0xcf29d5…6adc`](https://monadscan.com/tx/0xcf29d57cd1061f4ff59650fce15c06a39eaf343919f2a6e6431f26d167e86adc) originate from the configured holder and call the canonical USDC contract. Its balance falls from 152,288.127280 USDC to zero. These are separate from the counterfeit `UṢDC` poisoning event, which uses a different token contract and a third-party transaction sender.

The AVAX receipt comes from `0x9f1d8d40d9d8e6b69e0d9aa66b51c3007adc1c2f` in [`0x79abef…3d2`](https://snowtrace.io/tx/0x79abefd7a236396bfbbff35bb09911173e617f0124d86c78a93593703efd93d2). Native inventory increases from **404,347.90** to **480,481.63 AVAX**, exactly matching the receipt. No USD amount is inferred.

## Identified routes and verified bookings

- **Galaxy Warehouse and ARCH distributions:** the two qualifying USDC receipts total **2,037,042.08** and match the original booked E42/E21 revenue exactly: 1,894,876.53 and 142,165.55.
- **CCTP v2:** all **84 messages** match actual source payments/burns, Circle Iris nonce bindings, source message templates, destination authenticated message bodies and destination USDC mints. Ethereum→Base: **82 / 190,153,788.881463 USDC**. Base→Ethereum: **2 / 10,122,976.047858 USDC**. These are custody transfers, not new revenue. The existing `grove_cctp_v2_capital.py` adapter contains exact historical routes through August; October's new proofs still need integration for complete funding-basis propagation. This audit's recognition is not a claim that the existing funding replay already consumes them.
- **BUIDL:** the **44,645,040.435256 USDC** receipt matches the two October share-redemption requests in the booking snapshot. The current ledger includes the **−0.377494 USD** cash-versus-carrying-value adjustment. The 21 issuer mints total 364,724.88 shares; at the existing 0.9995 carrying convention, less that cash adjustment, this reconciles exactly to **364,542.140066 USD** of booked E10 income directed to Sky.
- **JTRSY:** three Ethereum redemption receipts total **235,315,891.396877 USDC**, matching the current E9 capital-outflow amount. The four share exits and in-flight position convention remain distinct from recognizing the cash as income.
- **Other mapped routes:** USDS/debt-buffer movements, DAI conversion, LitePSM, configured Ethereum vault activity, Agora subscriptions/redemptions, and the remaining LP liquidity/fee legs are identified in the CSV. Identifying a route alone does not certify its entire financial ledger or funding origin.

The machine-readable statuses partition all 1,850 transfers:

| Status | Events |
|---|---:|
| Existing route identified, not blanket booking certification | 1,353 |
| Missing Base venue, shares and cash legs | 200 |
| Verified CCTP source/destination cash legs | 168 |
| Authenticated swap legs requiring execution reconciliation | 78 |
| Base E19/E23 exits requiring cash reconciliation | 30 |
| Reviewed unsolicited/counterfeit exclusions | 11 |
| Approved JAAA/JTRSY partner rebate receipts | 6 |
| Booked Galaxy distributions verified exactly | 2 |
| Unresolved genuine USDC outflows | 2 |

The excluded contracts are ODYSSEY `0x113f5c…1ce1`, TENDIE `0x4a4a79…e5b2`, ALPHA `0x68725d…40a6`, SEND `0x91d6cf…5ab7`, and counterfeit Monad `UṢDC` `0x29f9c4…3946`. Their unsolicited events are retained, with full contract addresses and raw quantities, rather than deleting evidence. No rule excludes all unknown tokens or all small transfers.

## Evidence and reproduction

The complete [transaction ledger CSV](../../reconciliation/grove_alm_flows_2026_10/transfer-audit.csv), [compressed ledger JSON](../../reconciliation/grove_alm_flows_2026_10/transfer-audit.json.gz), raw chain captures, Circle responses, RPC balance/valuation controls, Dune SQL/results, original booking snapshot and SHA-256 manifest are under `reconciliation/grove_alm_flows_2026_10/`. `capture-queries.json` records the exact source queries without credentials. `vault-exit-checks.json` records the two smaller vault cash differences.

Re-run the offline audit, including bridge-message verification, exact Galaxy revenue matching and all 22 balance controls:

```sh
python scripts/audit_grove_alm_flows.py --input-dir reconciliation/grove_alm_flows_2026_10
python -m pytest tests/unit/test_grove_alm_flow_audit.py -q
```

Resolve the inventory/pricing gaps and G30 classifications before generating a final October settlement. October DR remains a separate missing input in the checked submodule workbook. The original configured-book numbers are retained solely as the baseline being audited.
