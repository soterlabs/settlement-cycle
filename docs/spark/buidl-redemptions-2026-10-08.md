# Spark: two historical BUIDL requests and three cash payments

PR #215 now carries existing S19 funding through two issuer redemption requests
and their actual USDC payments. The pinned history previously recorded the
shares leaving and cash arriving as unrelated movements. This is a tracing-only
repair: it does not publish historical fees, regenerate revenue, or change debt.

| Request | BUIDL face | Actual cash | Face less cash |
| --- | ---: | ---: | ---: |
| May 14, 2025 | 250,000 | 249,874.297369 | 125.702631 |
| July 18, 2025 | 200,000,000 | 199,899,997.229175 | 100,002.770825 |
| Total | 200,250,000 | 200,149,871.526544 | 100,128.473456 |

The July payment is **98.543517 + 199,899,898.685658 USDC**. Ignoring the first
installment would incorrectly overstate its remaining claim and cash shortfall.
Each aggregate differs from face less the observed 5bp redemption charge by
less than $3. The full difference above is an observed exit shortfall, not a
claim that every residual dollar is a documented issuer fee.

Evidence is the complete indexed boundary query through the saved August 2026
pin: all BUIDL transfers from Spark's Ethereum ALM and all USDC transfers from
issuer payer `0xcfc0f98f30742b6d880f90155d4ebb885e55ab33` to that ALM, with full
transaction logs retained in `tests/fixtures/spark_buidl_redemptions.json.gz`.
Both requests send BUIDL to `0x8780dd016171b91e4df47075da0a947959c34200`, the
same redemption boundary already independently modeled for Grove.

- May [request](https://etherscan.io/tx/0xea83b684e5db29239501ba7b5839634d28f88222aa87059905dee4d6e1fd585b), block 22,482,359;
  [payment](https://etherscan.io/tx/0xf79e894c03a379f636f0451690b55ff5289b9e6904f665ef5700630548ed8b0f), block 22,482,792.
- July [request](https://etherscan.io/tx/0x3cc7028c9cbe39daaab0ac3b142dfb171aac3f85060c6f058060bd39a4589847), block 22,947,529;
  [advance](https://etherscan.io/tx/0xcf6ba0c45a0c85e4deb822d5a70f2c7f7e6e3074684666526abd887b4840a244), block 22,948,631;
  [final payment](https://etherscan.io/tx/0xbb0d744db0f957e891378dd3d5ae3bee60d034cbf53602926b2d32f4fee9cfad), block 22,948,693.

These are **reviewed cash associations**, supported by the known issuer
boundary, exactly one outstanding request in each group and fee-consistent
aggregate proceeds. The transfer events do not emit a common payment/request
identifier. This is not the cryptographic message proof used for native bridges.
The code limits the association to these exact transactions, blocks, timestamps,
asset accounts and amounts; it does not introduce a general nearest-date rule.

At request time, the claim inherits existing borrowed and own-funds basis.
Partial cash releases only the amount received. At final payment, the remaining
claim closes at actual proceeds. Existing ledger policy consumes own earnings
before recognizing a loss of borrowed principal. Thus the $100,128.47 face/cash
difference must **not** automatically be called lost borrowed principal: that
also depends on the position's observed funding mix. Unknown provenance remains
unknown, and a cash receipt without its request does not create borrowed basis.
Claims remain attached to S19 and receive no new idle exemption.

The April 14 [4,999.749278 USDC receipt](https://etherscan.io/tx/0x052424941cb05b0b795deb1fe70f6b028eb9281453daa7b49baa921a4dbc023e)
from the same payer has no matching BUIDL request in this complete boundary
stream. It remains unassigned. July's portfolio sale to Grove and September's
forwarded BUIDL interest use their separate, already-reviewed spell treatments.

Regression coverage checks canonical transfers, both complete settlements,
borrowed/earned mixes, the cutoff after the July advance, unmatched cash without
a funded request, altered inputs, idempotency, and unrelated-prime isolation.
The actual six saved batches are also checked for unchanged original cash/share
amounts and unchanged debt. Full August cost impact awaits the queued replay.
