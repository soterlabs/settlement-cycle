# Smart Burn Engine history — dashboard dataset

Machine-readable, versioned. Built by `scripts/build_tmf_history.py` from
on-chain events (HyperSync), mirrored into `soterlabs/settlement-reports` under
`reports/tmf/data/`. **Consumers read `sbe_history.json`, not the markdown.**

| File | What | Precision |
|---|---|---|
| `sbe_history.json` | aggregates by month / quarter / year, totals, latest kick, parameter timeline | 2 dp (price 6 dp) |
| `sbe_kicks.csv` | one row per Splitter kick since deployment | exact (trailing zeros stripped) |
| `sky_burns.csv` | one row per SKY transfer into a burn sink | exact |

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
| `sky_burn_protocol` | SKY sent to a burn sink by the Pause Proxy — "true Sky burn" |
| `sky_burn_other` | SKY sent to `0x…dEaD` by anyone else |
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

Schema history: **1.1.0** added `address` on `parameter_changes` and normalised
`value` formatting; 1.0.0 initial.

## Burn definition

* `0x…dEaD` — any sender; `protocol` flags the Pause Proxy.
* zero address (`SKY.burn()`) — **protocol senders only**. The MKR↔SKY
  converter also burns SKY to the zero address; that is a conversion, not a
  treasury burn, and is excluded.

History: the 2025-06-30 executive burned 426,292,860.23 SKY from the Pause Proxy
(`SKY.burn()`). TMF-rule burns (10/55 of the previous month's buys) start with
the September 2026 executive.

## Refresh

```
set -a; source .env; set +a
PYTHONPATH=src python3 scripts/build_tmf_history.py
```

Incremental when `DATABASE_URL` is set (HyperSync log store, reorg-safe: only
finalized blocks are persisted). The upper bound is the latest finalized block
unless `--to-block` is given; it is recorded in `source.to_block`.
