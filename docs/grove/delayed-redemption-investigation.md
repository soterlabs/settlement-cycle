# Grove delayed redemptions and remaining funding gaps

Investigation dated 2026-09-22, supplementing PR #215. These are read-only
diagnostics, not changes to published settlement amounts or production
matching rules.

## BUIDL: delayed, grouped and fee-reduced returns

Following the operator's T+0 through T+3 guidance, scanned BUIDL outflows to
`0x8780dd016171b91e4df47075da0a947959c34200` and USDC receipts from
`0xcfc0f98f30742b6d880f90155d4ebb885e55ab33` at Grove's Ethereum ALM.
The request history ends at the published August pin; a separate lookahead
through September 4 identifies payments after month-end. Those later payments
are audit evidence and must not be booked as August cash.

The history contains **24 requests totaling $785,000,000**. A diagnostic
matcher finds **21 candidate payments** covering all 24 requests, including
grouped requests. Observed cash shortfalls total **$392,520.153200**:
5 bps of requested face value ($392,500), plus $20.153200 across payments.
Individual extra differences range from roughly $0.28 to $2.97.
The observed pattern is consistent with fees; it does not identify the
contractual composition of every deduction.

The diagnostic rules are explicit: the above counterparties, payment after
request and within 72 hours, combinations of up to four outstanding requests,
and a 5 bp shortfall plus $0–$5 additional difference. Requests are consumed
once, processing payments chronologically; a payment with multiple eligible
combinations is left unresolved. The fee band was inferred from this dataset,
not independently supplied by the issuer. All selected examples settled on
the same or next UTC date; this sample does not test T+2/T+3 or a business-day
calendar. No generic production heuristic was enabled.

Exact request/payment hashes, amounts and lags are in
`reconciliation/grove_buidl_redemption_candidates.csv`. Examples:

| Request | Face amount | Payment | Cash received | Shortfall |
|---|---:|---|---:|---:|
| 2025-08-04 | 100,000,000 | 2025-08-04 | 99,949,998.438074 | 50,001.561926 |
| 2025-09-15 | 50,000,000 | 2025-09-16 | 49,974,997.183340 | 25,002.816660 |
| 2026-08-24 (1 + 49,999,999) | 50,000,000 | 2026-08-25 | 49,974,999.629718 | 25,000.370282 |
| 2026-08-31 | 24,999,000 | 2026-09-01 | 24,986,500.153219 | 12,499.846781 |

The last payment is transaction
`0x5add7249801ed90f803a6cdfec0c9a6d4fc5f5b3343ee50fa9f9ed4b833cb8cf`.
The $24,999,000 request must remain pending at August month-end. A separate
$1,000 August 31 test request settled that evening for $999.178498.

## Isolated replay sensitivity

Adding the candidate links only to an in-memory copy of Grove's normalized
history changes unmatched receipts **298 → 278** and unmatched outflows
**530 → 507**. The September receipt stays outside the replay, which ends
August 31. Pending redemption custody retains the $24,999,000 face amount.

Borrowed basis follows the request into pending custody. On cash settlement,
the existing ledger recognizes any lost borrowed basis separately rather than
pretending the full gross amount returned. Earlier unknown funding remains
unknown. This experiment is not a passing cost reconciliation and must not be
promoted into production just because the counts decrease. In particular,
fees can leave debt outstanding without a corresponding asset; excluding MSC
financing alone does not eliminate that separate component.

Local evidence: `/tmp/grove-buidl-usdc-events.json`,
`/tmp/grove-buidl-lookahead.json`, `/tmp/grove-buidl-matched-candidates.json`,
and `/tmp/grove-buidl-candidate-replay.json`. The candidate matcher and replay
are `/tmp/match_grove_buidl_candidates.py` and
`/tmp/replay_grove_buidl_candidates.py`.

## A separate confirmed route: Grove–Spark syrupUSDC exchange

The largest remaining unmatched receipt is a documented cross-prime swap,
not a Maple queue redemption:

- July 20 transaction
  `0xfafb7edda92afb685a8ea0cfb8b26648220cc533219d3f4a7059e7171046f464`
  transfers **85,943,747.637271 syrupUSDC shares** from Grove's ALM to Spark's
  ALM. Its normalized quote is $100,928,899.47530557156.
- Transaction
  `0xafa23f703044c5296f42ff5202429b0dd16558ddbf677042d2cc9ea036b87667`
  transfers **100,928,938.340794 USDS** back from Spark's ALM to Grove's ALM.

The governance proposal explicitly describes this exchange. Both receipts
were inspected in full. The existing venue config recognizes the outbound
share transfer for revenue classification, but capital replay still lacks
the cross-transaction link. The roughly $38.87 quote/cash difference must be
retained, not turned into a new loan. This route has stronger independent
evidence than the BUIDL candidate matches.

Other large remaining gaps include Ethereum-to-Avalanche JAAA movements
(roughly $50M each), initial portfolio funding, and asynchronous subscriptions.

## Sources and limits

- [Sky's July 16 executive proposal](https://vote.sky.money/executive/template-executive-vote-monthly-settlement-cycle-for-june-2026-lssky-sky-rewards-normalization-complete-rwa001-a-offboarding-add-emergency-spells-to-the-chainlog-whitelist-osero-almproxy-adjust-vault-parameters-update-safe-harbor-agreement-prime-agent-proxy-spells-july-16-2026)
  confirms the syrupUSDC-for-USDS exchange.
- [Securitize's Zero Hash integration announcement](https://securitize.io/learn/press/securitize-integrates-with-zero-hash-enables-purchase-of-buidl-fund-via-USDC-conversion)
  establishes the BUIDL/USDC conversion relationship, not individual payouts.
- [Zero Hash network-fee documentation](https://docs.zerohash.com/docs/network-fees)
  describes fees deducted from customer withdrawals. It does not confirm
  Grove's contract or the historical rate used for these transactions.
- [BlackRock account resources](https://www.blackrock.com/cash/en-us/account-resources/account-resources-t3)
  lists the observed redemption wallet for a current fund offering. This is
  supporting address evidence, not proof of historical BUIDL order identity.

An issuer/order reference or reviewed explicit settlement mapping is still
needed before promoting the inferred BUIDL pairings to production funding
provenance. No third party has been contacted.
