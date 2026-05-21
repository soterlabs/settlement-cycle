# fix/centrifuge-erc4626-flows

## Summary

Replaces the token-transfer × NAV methodology for Centrifuge vault inflows
(Cat E venues E8 JAAA and E9 JTRSY) with exact USDC amounts read directly
from on-chain ERC-4626 `Deposit`/`Withdraw` events.  Also adds a pre-flight
ERC-7540 in-flight request check that warns when pending or claimable assets
exist at the SoM/EoM settlement boundary.

---

## Background: why the old method was wrong

Previously all Cat E (RWA Tranche) inflows were computed by tracking net
changes in the prime ALM's token balance and repricing those changes at the
period-end NAV oracle value.  This works correctly for secondary-market
positions but produces incorrect numbers for Centrifuge vaults because:

1. **NAV drift between transfer date and EOM.** A $300M withdrawal on
   March 11 would be repriced at the March 31 NAV, not at the NAV on the
   day the USDC actually left the vault.  If NAV changed between those two
   dates the inflow is misstated, and revenue (= EOM − SOM − inflow)
   absorbs the error.

2. **Round-trip accounting mismatch.** External counterparties calculate
   flows using the `assets` field of the ERC-4626 event, which is the
   exact USDC transferred.  The previous method produced non-round numbers
   that diverged from those calculations.

---

## How the new methodology works

### Step 1 — Event-based inflow query

A new Dune query (`erc4626_centrifuge_flow.sql`) reads `Deposit` and
`Withdraw` events directly from `ethereum.logs` for the vault contract,
filtered to the prime's ALM address.  It sums the raw `assets` amounts per
day using the first 32 bytes of event `data` (the `uint256 assets` field).

The key difference from the old path: this captures the exact USDC amount
that moved — not the token balance change repriced at NAV.

### Step 2 — Revenue formula

The accounting identity is unchanged in form:

```
revenue = EOM_usd − SOM_usd − inflow_usd
```

With event-based inflows, `inflow_usd` is the exact USDC deposited or
withdrawn.  Because EOM and SOM are still computed from `balance × NAV`,
the formula naturally isolates yield accrual:

```
EOM_usd = (SOM_shares + deposited_shares − redeemed_shares) × EOM_NAV
        = SOM_usd_at_EOM_NAV + net_capital_at_EOM_NAV + yield_accrual

revenue = EOM_usd − SOM_usd − net_capital_in_USDC
        ≈ yield_accrual  (+ small basis from NAV drift on redeemed shares)
```

The "small basis" term is real economics, not a bug — a withdrawal at
intra-period NAV below the SOM NAV produces a genuine small loss.  The
March 2026 E8 implied yield of −$477K is this effect: 318M shares were
redeemed at $1.0261/share on March 11 vs the SOM NAV of $1.0276/share.

### Step 3 — Share-balance sanity check

After computing inflows, the pipeline verifies:

```
SOM_shares + Σ deposit_shares − Σ withdraw_shares ≈ EOM_shares
```

If drift exceeds 0.5% a WARNING is logged.  This catches cases where the
event-based accounting diverges from the observed on-chain balance — for
example if a corporate action, share migration, or missed event caused an
untracked balance change.

---

## Interaction with SDE cap-weighting

Cat E venues (JAAA, JTRSY) are capped Sky Direct Exposures.
`_daily_capped_sd_revenue` computes the SDE slice day-by-day using
`sd_share_d = min(cap, position_d) / position_d`.

This computation requires an inflow timeseries that is on the **same clock**
as the daily position snapshots.  The daily position values (`_sde_ts`) are
derived from ERC-20 token-transfer timing, so the inflow timeseries passed to
`_daily_capped_sd_revenue` must also use token-transfer timing — not vault
event timing.  If a large withdrawal is recorded as happening one day earlier
in the vault events vs the token-transfer ledger, `_daily_capped_sd_revenue`
would apply an incorrect cap ratio on that day and misstate `sd_revenue` by
up to ~$16M (as observed during development).

**Resolution:** the pipeline passes the token-transfer `inflow_timeseries` to
`_daily_capped_sd_revenue` (unchanged), and passes the vault-event
`erc4626_period_inflow` only to the `actual_revenue` formula and the output.

### Simplification: excess yield from vault-event tracking is attributed entirely to Sky

Because the two inflow methodologies can differ slightly (vault events capture
exact USDC; token-transfer reprices shares at NAV), `actual_revenue_vault`
can differ from `actual_revenue_rwa`.  The delta is small but nonzero.

This delta is currently attributed **entirely to `sd_revenue`** (i.e. entirely
to Sky), with `prime_revenue` pinned to its RWA-computed value:

```
sd_revenue = sd_revenue_rwa + (actual_revenue_vault − actual_revenue_rwa)
prime_revenue = actual_revenue_vault − sd_revenue
              = actual_revenue_rwa − sd_revenue_rwa   (unchanged from RWA path)
```

**Why this simplification is reasonable in most cases:**

Most Centrifuge Cat E venues fall into one of two situations:

1. **Position is above the cap** (typical for JAAA at $450M–$750M vs $325M
   cap) — `sd_share_d` for every day is `cap / position_d ≈ 0.5–0.7`, so
   the vast majority of yield goes to Sky regardless.  Routing the small
   methodology delta to Sky rather than splitting it is indistinguishable
   from noise.

2. **Position is below the cap, OR the venue is a fixed-rate SDE** — Sky
   receives 100% of yield, so there is no prime slice to split into and the
   simplification has zero impact.

**When this simplification may misattribute revenue:**

For a capped SDE venue during a period where the position is *near but above*
the cap — meaning `sd_share_d` is meaningfully less than 1 and could vary
significantly — routing the full methodology delta to Sky rather than splitting
it proportionally would overstate Sky's revenue and understate prime's revenue.
The effect size is bounded by `(actual_revenue_vault − actual_revenue_rwa)`,
which is typically very small (< $50K in tested months).

Revenue numbers for capped SDE venues in months where the position is close
to the cap should be scrutinized when this path is active.  The simplification
is validated for January 2026 and documented with a comment in
`prime_agent_revenue.py` for future reviewers.

---

## ERC-7540 in-flight request check

Centrifuge vaults follow ERC-7540 (asynchronous ERC-4626), where deposits and
redemptions are a two-step process:

1. `requestDeposit` / `requestRedeem` — assets or shares enter the vault's
   escrow queue; they are **not yet reflected in the ALM's share balance** or
   in the USDC balance.
2. Centrifuge epoch processing — the request is executed off-chain.
3. `claimDeposit` / `claimRedeem` — the `Deposit` / `Withdraw` event fires;
   this is the event our pipeline tracks.

If a request is sitting in step 1 or 2 at the SoM or EoM settlement boundary,
the pipeline's share-balance snapshot is incomplete: shares or USDC are held
in escrow and will appear or disappear in a future period's event.

**What the pipeline does:** after pin blocks are resolved (step 2h), the
orchestrator calls `_check_centrifuge_in_flight`, which queries all four
ERC-7540 state functions at both the SoM and EoM pin blocks for each venue
with `centrifuge_vault` configured:

- `pendingDepositRequest` — USDC in escrow, not yet converted to shares
- `claimableDepositRequest` — epoch processed, shares ready to claim
- `pendingRedeemRequest` — shares in escrow, not yet converted to USDC
- `claimableRedeemRequest` — epoch processed, USDC ready to claim

Any non-zero value triggers a **WARNING in caps** naming the venue, amount,
and which boundary block is affected.  The March 11 2026 E8 withdrawal was in
the `claimableRedeemRequest` state before it settled — this mechanism would
have caught it if it had sat across a month boundary.

**What the pipeline does NOT do:** it does not automatically adjust revenue
numbers.  This is a deliberate choice to keep the pipeline simple.  Correctly
counting an in-flight request as part of the current period's inflows requires
knowing whether the corresponding epoch execution was causally linked to
activity in the current period or the next — that determination requires
manual review of the on-chain timeline.

**Consequence:** if a WARNING fires, the revenue numbers for that venue and
period should be manually reviewed and potentially adjusted.  This is a known
limitation to be iterated on as these venues mature.

---

## Files changed

| File | Change |
|---|---|
| `config/grove.yaml` | Add `underlying` (USDC) and `centrifuge_vault` to E8 (JAAA) and E9 (JTRSY) |
| `src/settle/domain/primes.py` | Add `centrifuge_vault: Address | None` field to `Venue` dataclass |
| `src/settle/domain/config.py` | Parse `centrifuge_vault` from YAML |
| `src/settle/queries/erc4626_centrifuge_flow.sql` | New Dune query: Deposit/Withdraw events with raw assets and shares per day |
| `src/settle/normalize/positions.py` | New `_erc4626_event_inflow_timeseries`; fix missing `logging` import in `DuneError` handler |
| `src/settle/compute/monthly_pnl.py` | Wire ERC-4626 path in Cat E block; share-balance sanity check; `_check_centrifuge_in_flight` pre-flight check |
| `src/settle/compute/prime_agent_revenue.py` | `erc4626_period_inflow` field on `VenueRevenueInputs`; SDE revenue adjustment |
| `src/settle/extract/rpc.py` | `is_contract_deployed` (eth_getCode); four ERC-7540 selector constants |
| `src/settle/extract/_abi.py` | No net change (keccak helper added then removed in favour of hardcoded selectors) |
| `tests/unit/test_compute_prime_agent_revenue.py` | 6 new tests for `erc4626_period_inflow` branches |
| `tests/unit/test_centrifuge_in_flight.py` | 12 new tests for `_check_centrifuge_in_flight` |
| `tests/unit/test_erc4626_event_inflow_timeseries.py` | 7 new tests for `_erc4626_event_inflow_timeseries` |
| `tests/integration/test_centrifuge_flow_e2e.py` | Live test pinning `erc4626_centrifuge_flow.sql` against the known E8 March 11 withdrawal |

---

## Test notes

- All 360 unit tests pass.
- The integration e2e test (`test_centrifuge_flow_e2e.py`) is gated behind
  `@pytest.mark.live` and `DUNE_API_KEY`; run with:
  ```
  DUNE_API_KEY=... pytest tests/integration/test_centrifuge_flow_e2e.py -m live -v -s
  ```
- The new unit tests caught a latent bug: the `DuneError` fallback in
  `_erc4626_event_inflow_timeseries` referenced `_logging` before it was
  imported.  Fixed in the final commit (`0b26353`).
