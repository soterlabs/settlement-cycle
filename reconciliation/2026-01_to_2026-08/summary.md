# Spark and Grove reconciliation — January through August 2026

Scope: 2026-01-01 through 2026-08-31, inclusive. Published monthly settlement
reports are not regenerated; the corrections ride the September settlement.

## Amounts owed

| Prime | Jan-Aug correction | Settlement treatment |
|---|---:|---|
| Spark | $2,392,354.07 | `sv_adj: +2392354.07`; increases MSC debt mint and Send to prime equally |
| Grove | $165,013.90 | `sky_adj: -165013.90`; reduces MSC debt mint, with no separate Send to prime |

## Spark calculation

The full $2,392,354.07 is reserve-factor revenue received in spTokens and
previously classified as capital. It belongs in Prime-side Supply-Side revenue.

| Month | Reserve-factor revenue |
|---|---:|
| January | $187,229.81 |
| February | $776,974.45 |
| March | $192,240.53 |
| April | $107,239.57 |
| May | $58,633.73 |
| June | $281,611.86 |
| July | $317,345.18 |
| August | $471,078.95 |
| **Authoritative unrounded total** | **$2,392,354.07** |

The displayed monthly figures independently sum to $2,392,354.08; the
authoritative total uses unrounded provenance values, producing the one-cent
presentation difference.

The source addresses activate for direct recognition on 2026-09-01, so a
historical replay cannot recognize these receipts in Jan-Aug and also pay the
true-up.

## Grove calculation

| Component | Credit |
|---|---:|
| BUIDL redemption fees settled during Jan-Aug | $162,505.35 |
| August 31 in-flight SDE CoF correction | $2,508.55 |
| **Total Grove credit** | **$165,013.90** |

The in-flight amount was obtained by replaying August with and without the
$24,986,500.50 BUIDL settlement receivable. Sky-side Supply-Side revenue falls
from $8,339,810.888358439873673301722 to
$8,337,302.342456265200035031809, a $2,508.545902174673638269913 credit,
rounded to cents in settlement data.

The $321,627.21 September transition markdown is not a Jan-Aug amount owed.
It is generated once by the BUIDL haircut's 2026-09-01 effective date. The
$12,499.85 fee on the August 31 redemption settled September 1 and is likewise
outside this reconciliation window.
