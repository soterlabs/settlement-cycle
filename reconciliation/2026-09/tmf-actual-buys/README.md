# September TMF: actual-purchases stream sizing

User-authorized September TMF correction, using the BA calculator's methodology
from the September 2026 cycle. This changes the TMF recommendation only: no
prime settlements, non-MSC revenue or consolidated Sky revenue were rerun.

The historical SNR remains 14,635,947 USDS (rounded). The actual September
burn-window purchases are 40547581.670324591074558856 SKY. Their staking share gives
33,175,294.09 SKY/month, a 99,525,882 SKY 90-day stream and approximately
7,740,902 SKY per weekly distributor pull. See `tmf.json` for exact precision.

The previous TWAP-sized recommendation was 114,797,518 SKY. USDS allocations,
hop, actual purchases and burn attribution are unchanged. The new vest amount
matches the operator-supplied September calculator and its stated October
executive amount; this rerun does not execute or create another stream.

The SKY-farm APY is recomputed using the original September-end staked balance;
it need not equal the calculator's illustrative APY denominator.

Reproduce using normal credentials:

```sh
PYTHONPATH=src python scripts/run_tmf_2026.py --months 2026-09
```

`tmf.json` retains activity and boundary state. `manifest.json` binds the report,
SNR input, configuration and calculation code. The October TMF output and all
other previously published monthly reports are unchanged by this rerun.
