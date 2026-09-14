# Smart Burn Engine history — dashboard dataset

Machine-readable, versioned. Built by `scripts/build_tmf_history.py` from
on-chain events (HyperSync), mirrored into `soterlabs/settlement-reports` under
`reports/tmf/data/`. **Consumers read `sbe_history.json`, not the markdown.**

| File | What | Precision |
|---|---|---|
| `sbe_history.json` | aggregates by month / quarter / year, totals, latest kick, parameter timeline | 2 dp (price 6 dp) |
| `sbe_kicks.csv` | one row per Splitter kick since deployment | exact (trailing zeros stripped) |
| `sky_burns.csv` | one row per SKY transfer into a burn sink, with its `kind` | exact |

## `sbe_history.json`

```
schema_version      semver — bump on any field rename / removal
generated_at        ISO-8601 UTC
source              chain, from_block, to_block, to_ts, contracts, event definitions
definitions         one sentence per series (below)
notes               caveats a reader needs (regime history, empty burn series, …)
totals              a period row over the whole range
latest_kick         ts, block, tx, the three USDS amounts, SKY bought, burn/hop in force
periods.monthly     [period row]   period = "YYYY-MM"
periods.quarterly   [period row]   period = "YYYY-Qn"
periods.annual      [period row]   period = "YYYY"
parameter_changes   every Splitter/Kicker File event (hop, burn, farm, flapper, kbump, khump)
```

A **period row**:

| Field | Meaning |
|---|---|
| `kicks` | number of Splitter kicks |
| `usds_buyback` | USDS sent to the Flapper to buy SKY (`Kick.lot`) — "Sky buyback" |
| `usds_to_stakers` | USDS sent to the USDS staker farm (`Kick.pay`) — "USDS returned as dividends" |
| `usds_total` | the two above summed (= `Kick.tot`, USDS pulled from the surplus) |
| `sky_bought` | SKY received by the Flapper's receiver (`Exec.bought`) |
| `sky_avg_price` | `usds_buyback / sky_bought`, volume-weighted |
| `sky_burn_engine` | SKY burned by the Smart Burn Engine — the 10/55 share of a month's buys, retired by the following month's spell. **The buyback-policy number** |
| `sky_burn_supply_correction` | SKY burned by the protocol *before* the TMF took effect (see below) |
| `sky_burn_protocol` | `sky_burn_engine + sky_burn_supply_correction`. Predates the split and is dominated by the correction — prefer `sky_burn_engine` |
| `sky_burn_other` | SKY sent to `0x…dEaD` by a third party |
| `burn_events` | number of burn transfers |
| `first_ts` / `last_ts` | first / last kick in the period |

Periods appear only when they contain at least one kick or burn. A period with
kicks under `splitter.burn = 100%` has `usds_to_stakers = 0`. A period with
burns but no kicks has `kicks = 0` and **null** `sky_avg_price`, `first_ts`,
`last_ts`.

`parameter_changes` rows: `ts, block, tx, contract (chainlog role), address
(emitting contract — the legacy and live Flapper both carry the MCD_FLAP role),
what, value`. Numeric values are exact decimals as plain digits (no exponent,
no trailing zeros); `File(address)` values are the address.

Schema history: **1.2.0** added `kind` on every burn row, `sky_burn_engine` /
`sky_burn_supply_correction` on every period row, and `source.tmf_effective_from`
(all additive — `sky_burn_protocol` keeps its old meaning); 1.1.0 added `address`
on `parameter_changes` and normalised `value` formatting; 1.0.0 initial.

## Burn definition and classification

Which transfers count as a burn:

* `0x…dEaD` — any sender; `protocol` flags the Pause Proxy.
* zero address (`SKY.burn()`) — **protocol senders only**. The MKR↔SKY
  converter also burns SKY to the zero address; that is a conversion, not a
  treasury burn, and is excluded.

Every burn row carries a **`kind`**, because the two protocol kinds are
indistinguishable on-chain — both are Pause Proxy → zero address — while being
economically unrelated:

| `kind` | What |
|---|---|
| `engine` | the Smart Burn Engine's own burn: 10/55 of a month's buys, retired by the following month's spell. First one 2026-09-13, 2,860,943.76 SKY |
| `supply_correction` | the 2025-06-30 spell retiring 426,292,860.23 SKY of supply created in the MKR→SKY conversion |
| `third_party` | anyone who is not the protocol sending SKY to `0x…dEaD` |

The rule is a single timestamp, `source.tmf_effective_from` (echoed from
`config/tmf.yaml` `policy.tmf_effective_from`, currently 2026-08-17T14:02:23Z —
the TMF's first cast): a protocol burn at or after it is `engine`, before it is
`supply_correction`. **`supply_correction` is a closed category** — the boundary
is a fixed past timestamp, so no later burn can join it.

Why it matters: the correction is ~150× the first engine burn, so
`sky_burn_protocol` is dominated by a one-off that has nothing to do with
buyback policy. Use `sky_burn_engine`.

## Refresh

```
set -a; source .env; set +a
PYTHONPATH=src python3 scripts/build_tmf_history.py
```

Incremental when `DATABASE_URL` is set (HyperSync log store, reorg-safe: only
finalized blocks are persisted). The upper bound is the latest finalized block
unless `--to-block` is given; it is recorded in `source.to_block`.
