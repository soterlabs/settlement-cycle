# E14 display-only revenue scope omitted traced AUSD financing

E14 is the primary ALM's raw AUSD balance. `config/grove.yaml` deliberately
marks it display-only because it routes capital and does not earn standalone
venue revenue. The financing normalizer already records its movements, as well
as its pending Agora subscription/redemption custody, but the financing report
iterated only revenue venues and explicitly registered tracing-only venues.
That omitted E14's borrowed capital from the sum.

`include_grove_secondary_cash` now registers the exact observed E14 account as
tracing-only as well. It retains its existing custody accounts. It creates no
movements, borrowing, revenue, or idle exemption. If E14 is already a revenue
row, the financing layer emits it once. Its reporting-only row has no revenue,
net PnL, or APY; the historical uncertainty remains explicit.

On the August 2026 pinned replay:

- E14 average borrowed basis: **149,072.810086 USDS**.
- Additional modeled BLOOM-A allocation cost: **462.137279 USDS**.
- Modeled BLOOM-A allocation costs: **3,448,147.745142 USDS**.
- Global BLOOM-A cost excluding MSC: **3,448,941.990029 USDS** (unchanged).
- Remaining modeled difference: **794.244887 USDS**, down from 1,256.382167.

The remaining modeled difference comprises 639.220386 of financing retained in
execution/LP residual accounts, 0.000032 of numerical rounding accounts, and
155.024469 of financing on realized borrowed-principal losses. The material
realized loss is the August 4, 2025 BUIDL redemption's 50,001.117168 missing
principal after both its small advance and final payment; a further Agora
redemption contributes less than half a cent of realized borrowed basis.
Those are expense financing, not unidentified investment deposits.

**This is a conditional numerical bridge, not certified allocation costs.**
The three unidentified incoming payments (49,596 RLUSD and 1 USDC) still make
BLOOM-A origin attribution uncertain. Its rows remain unresolved and excluded
from the eligible subtotal. GROVE-A remains independently traced, with its
0.143297 cost difference explained by two PAU swap execution shortfalls.

Published reports, API data, debt, rates, and global borrowing costs are
unchanged. A synthetic replay test checks borrowing through routing cash and
reinvestment, excludes earnings from borrowed basis, and prevents duplicate
rows when the revenue scope also includes E14.
