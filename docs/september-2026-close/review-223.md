# PR #223 review — October 1, 2026

Reviewed `60bad9d` against main. CI is green (unit and PostgreSQL integration).
No on-chain action or historical report regeneration was performed.

## Findings

1. **P2 — The authorized SOFR close is not reproducible from the PR alone.**
   The September Spark/Grove provenance correctly records 3.88%, sourced from
   September 29 for September 30, with `coverage_complete=False`. However, its
   rate preparer lives only in `/tmp/run_september30_sofr_estimate.py`, and the
   report assembler is also outside the repository. The normal API-to-monthly
   loader (`src/settle/revenue/monthly.py:95`) rejects both saved snapshots as
   `invalid reference-rate snapshot`; the regular live runner does not inject
   the authorized snapshot. Commit a narrowly scoped, explicit override and a
   reproducible close command rather than weakening the general completeness
   guard. The operator has reconfirmed use of September 29 for this close.

2. **P2 — Refund cash coverage is checked by day, not transaction.**
   `src/settle/compute/non_msc.py:261-268` sums all cash surplus returns on the
   settlement date. If the backend omits the Gelato return but includes an
   unrelated return of at least 42,469.146527 on that date, the guard passes
   and subtracts an offset for cash that was never booked. Reproduction:
   one unrelated 50,000 cash row plus the Gelato offset is accepted and yields
   7,530.853473 income. Bind the offset to the specific Blow/join transaction
   in the cash extraction, retaining enough identity to check it. The actual
   September Gelato cash transfer was separately verified, so this is not
   evidence of an error in the current September total.

## Confirmed

- Submodule is exactly `1e9ecb2`; finalized DR input matches the requested
  September venue amounts and Grove Farm emitted-code split.
- Four historical Skybase corrections total 124,694.330541 and appear once,
  separately from normal September revenue. Prior reports remain unchanged.
- 1997–1999 and 1020 resolve to Skybase; deliberately unpaid/unresolved codes
  stay excluded and disclosed.
- Redundant independent DR replay and its watcher are stopped. The upstream
  finalized output is authoritative; no downstream replay is required.
- SOFR September 29 is 3.88%. New York Fed scheduled publication is around
  08:00 America/New_York, or 12:00 UTC during daylight saving time. September
  30's observation is therefore expected October 1 around 12:00 UTC. The
  public five-observation feed does not provide exact publication timestamps.

Sources: https://www.newyorkfed.org/markets/reference-rates/sofr and
https://markets.newyorkfed.org/api/rates/secured/sofr/last/5.json.
