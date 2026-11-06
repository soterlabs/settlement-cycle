# Merkl wrapper rewards are earned funding

The February 6 and April 24 Grove claims deliver **$6,175,678.647311** of
RLUSD aTokens (E1 and E3). Existing revenue accounting already recognizes these
claims. Capital tracing recognized direct distributor transfers, but missed
wrapper rewards because the actual `BalanceTransfer` sender is wrapper custody.

The corrected normalizer uses the existing revenue policy's event association:
allowlisted Merkl `Claimed.token` equals the aToken `Mint.caller`, with the same
recipient and transaction. These Mints are interest accrual, not new deposits.
The actual incoming `BalanceTransfer` amounts, reconstructed with their liquidity
indices, must match the claimed nominal amount to raw-unit rounding. Missing
receipts cannot create income; extra unrelated receipts fail the association.
Direct distributor transfers keep their existing classification.

Canonical events are retained in `tests/fixtures/grove_merkl_wrapper_events.json`.
The two transaction links and exact raw amounts are in
`compute/grove_merkl_rewards.py`; its validated, idempotent adapter applies the
same classification to pinned histories without a multichain re-extraction.

The August replay removes both receipt gaps (**28 → 26**). Unmatched outflows
remain **256** and eligible allocation borrowing costs remain **$1,328.22**.
GROVE-A's diagnostic range remains **$11,095.81–$11,784.74**. Neither ilk fully
reconciles: identifying rewards does not establish the remaining capital routes.
See `reconciliation/grove_merkl_rewards_2026_08.json` for controls and hashes.

This changes capital provenance only. Revenue already includes these rewards;
no published settlements, API outputs, total debt or global financing costs change.
