# Yield from skipped position rows still needs income classification

Grove E38 is intentionally skipped for balance/principal pricing because its
AUSD holdings already appear under E14. It still declares `cash_distributions`
for Agora incentive payers. The monthly revenue path processes those sources
before checking whether a position is skipped. Capital tracing must apply the
same distinction, also relevant to the skipped E42 Warehouse venue.

The initial cash-distribution normalizer fix incorrectly filtered out skipped
venues. This checkpoint removes that filter and adds a regression with a
skipped, cross-chain yield venue. The rule remains narrow: configured payer,
token, receipt chain and ALM recipient must all match.

Eight actual Agora receipts total **$2,675,160.44**, including **$857,964** on
August 26. They come from the two existing E38 payers (`0x4a4593…f651` and
`0xdf27ac…3a1d`). No new payer or revenue policy is introduced. A reviewed
adapter applies the same classification to saved histories; canonical receipt
fixtures are tested against the current configuration. Received earnings never
create borrowed basis and can be reinvested or used to repay existing debt.

August replay removes all eight receipt gaps (**36 → 28**); outflows remain
256. The eligible subtotal is still **$1,328.22**. GROVE-A's diagnostic cost
interval narrows from **$10,034.90–$11,784.74** to **$11,095.81–$11,784.74**.
Neither ilk fully reconciles; the interval is not a validated allocation sum.

Evidence: `reconciliation/grove_agora_incentives_2026_08.json`. All 315 relevant
tests pass. Existing revenue, published reports, API outputs and global CoF
are unchanged.
