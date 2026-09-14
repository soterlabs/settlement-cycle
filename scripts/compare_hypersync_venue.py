#!/usr/bin/env python3
"""Compare a venue's Dune inputs with HyperSync at chain-correct monthly pins.

No settlement files are written. Exit nonzero on errors or mismatches. JSON
evidence includes normalized-input hashes, full-series maximum differences,
counts, parameters and mismatch samples. Dune stays an independent oracle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd

from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.extract import hypersync
from settle.extract.dune import execute_query
from settle.normalize.positions import _MERKL_DISTRIBUTORS
from settle.normalize.sources._paths import QUERIES_DIR
from settle.normalize.sources.dune_balances import DuneBalanceSource
from settle.normalize.sources.dune_v3_inflow import DuneV3InflowSource
from settle.normalize.sources.hypersync_balances import HyperSyncBalanceSource
from settle.normalize.sources.hypersync_lp import (
    HyperSyncV3PositionSource,
    HyperSyncV4PositionSource,
)
from settle.normalize.sources.hypersync_venue_events import (
    centrifuge_flows,
    merkl_raw,
    transfer_raw,
)
from settle.normalize.sources.uniswap_v4 import DuneUniswapV4FlowsSource


def _text(value: Any) -> str:
    return "0x" + bytes(value).hex() if isinstance(value, (bytes, bytearray)) else str(value)


def _records(frame: pd.DataFrame, keys: list[str], values: list[str]) -> dict:
    rows = {}
    for row in frame.to_dict("records"):
        key = tuple(_text(row[k]) for k in keys)
        if key in rows:
            raise ValueError(f"Duplicate comparison key {key}")
        rows[key] = {v: Decimal(str(row[v])) for v in values}
    return rows


def compare_frames(label: str, dune: pd.DataFrame, hs: pd.DataFrame,
                   keys: list[str], values: list[str], tolerance: Decimal) -> dict:
    d, h = _records(dune, keys, values), _records(hs, keys, values)
    maxima = {c: Decimal(0) for c in values}
    previous = [{c: Decimal(0) for c in values} for _ in range(2)]
    mismatches = []
    for key in sorted(d.keys() | h.keys()):
        pair = []
        for index, source in enumerate((d, h)):
            # Sparse cumulative histories retain the previous value; daily
            # flows on absent dates are zero. Counterparty keys have no carry.
            row = source.get(key, {})
            normalized = {c: row.get(c, previous[index][c] if c.startswith("cum_") else Decimal(0))
                          for c in values}
            previous[index] = normalized
            pair.append(normalized)
        diffs = {c: abs(pair[0][c] - pair[1][c]) for c in values}
        for c, diff in diffs.items():
            maxima[c] = max(maxima[c], diff)
        if any(diff > tolerance for diff in diffs.values()):
            mismatches.append({"key": key, "dune": pair[0], "hypersync": pair[1]})
    def digest(rows):
        return hashlib.sha256(json.dumps(sorted(rows.items()), default=str).encode()).hexdigest()
    return {"input": label, "matched": not mismatches, "tolerance": str(tolerance),
            "dune_rows": len(d), "hypersync_rows": len(h), "max_abs_difference": maxima,
            "dune_sha256": digest(d), "hypersync_sha256": digest(h),
            "mismatch_count": len(mismatches), "mismatches": mismatches[:10]}


def compare(prime_id: str, venue_id: str, month_label: str, tolerance: Decimal) -> dict:
    prime = load_prime_by_id(prime_id)
    venue = next(v for v in prime.venues if v.id == venue_id)
    month = Month.parse(month_label)
    pins = {}
    def pin(chain, day):
        key = (chain, str(day))
        if key not in pins:
            ts = int(datetime.combine(day, time(23, 59, 59), UTC).timestamp())
            pins[key] = hypersync.find_block_at_or_before(chain, ts)
        return pins[key]
    chain = venue.chain.value
    end = pin(chain, month.last_day)
    holder = (venue.holder_override or prime.alm.get(venue.chain))
    checks = []
    dune = DuneBalanceSource()
    hs = HyperSyncBalanceSource(decimals_of=lambda c, t, b: venue.token.decimals)
    def frames(label, d, h, keys, columns, tol=tolerance):
        check = compare_frames(label, d, h, keys, columns, tol)
        checks.append(check)
        print(f"{prime_id}/{venue_id}/{month_label} {label}: {'MATCH' if check['matched'] else 'MISMATCH'} "
              f"rows={check['dune_rows']}/{check['hypersync_rows']} max={check['max_abs_difference']}", flush=True)
    def directed(label, token, frm, to, c=chain, decimals=None):
        args = (c, token, frm, to, prime.start_date, pin(c, month.last_day))
        backend = hs if decimals is None else HyperSyncBalanceSource(decimals_of=lambda *a: decimals)
        frames(label, dune.directed_inflow_timeseries(*args), backend.directed_inflow_timeseries(*args),
               ["block_date"], ["daily_inflow", "cum_inflow"])
    category = venue.pricing_category.value
    if holder is not None and not venue.skip and category != "S2" and not venue.lp_kind == "uniswap_v4":
        args = (chain, venue.token.address.value, holder.value, prime.start_date, end)
        threshold = venue.min_transfer_amount_usd or Decimal(0)
        frames("transfer_timeseries.sql", dune.cumulative_balance_timeseries(*args, min_transfer_amount=threshold),
               hs.cumulative_balance_timeseries(*args, min_transfer_amount=threshold),
               ["block_date"], ["daily_net", "cum_balance"])
        if category in {"A", "EOA"}:
            frames("inflow_by_counterparty.sql", dune.inflow_by_counterparty(*args), hs.inflow_by_counterparty(*args),
                   ["block_date", "counterparty"], ["signed_amount"])
        if category in {"B", "C", "D"}:
            directed("mint:venue_inflow.sql", venue.token.address.value, bytes(20), holder.value)
            directed("burn:venue_inflow.sql", venue.token.address.value, holder.value, bytes(20))
            for q in venue.share_burn_destinations:
                directed("queue-out:" + q.value.hex(), venue.token.address.value, holder.value, q.value)
                directed("queue-refund:" + q.value.hex(), venue.token.address.value, q.value, holder.value)
    for cash in venue.cash_distributions:
        c = cash.chain or venue.chain
        # Query token decimals through the standard source: cash tokens need
        # not share the venue token's decimal count (e.g. an off-chain note).
        from settle.normalize.sources.hypersync_balances import _default_decimals
        decimals = _default_decimals(c.value, cash.token.value, pin(c.value, month.last_day))
        directed("cash:" + cash.payer.value.hex(), cash.token.value, cash.payer.value,
                 prime.alm[c].value, c.value, decimals)
    if venue.centrifuge_vault:
        params = {"vault": venue.centrifuge_vault.value, "holder": holder.value, "start_date": str(prime.start_date)}
        frames("erc4626_centrifuge_flow.sql", execute_query(QUERIES_DIR / "erc4626_centrifuge_flow.sql", params, end),
               centrifuge_flows(chain, venue.centrifuge_vault.value, holder.value, prime.start_date, end),
               ["block_date"], ["assets_in_raw", "assets_out_raw", "shares_in_raw", "shares_out_raw"], Decimal(0))
    if category in {"C", "D"}:
        for sender in prime.external_alm_sources.get(venue.chain, []):
            if sender.value in _MERKL_DISTRIBUTORS.get(venue.chain, set()):
                params = {"distributor": sender.value, "distributor_padded_hex": "00" * 12 + sender.value.hex(),
                          "user_padded_hex": "00" * 12 + holder.value.hex(), "atoken": venue.token.address.value,
                          "atoken_padded_hex": "00" * 12 + venue.token.address.value.hex(),
                          "start_date": str(month.first_day), "end_date": str(month.last_day)}
                d = execute_query(QUERIES_DIR / "merkl_claims_ethereum.sql", params, end)
                raw = merkl_raw(chain, sender.value, venue.token.address.value, holder.value, month.first_day, month.last_day, end)
                frames("merkl_claims_ethereum.sql", d.assign(key="total"),
                       pd.DataFrame([{"key": "total", "total_amount_raw": raw}], dtype=object),
                       ["key"], ["total_amount_raw"], Decimal(0))
            else:
                params = {"chain": chain, "token": venue.token.address.value, "holder": holder.value,
                          "sender": sender.value, "start_date": str(month.first_day), "end_date": str(month.last_day)}
                d = execute_query(QUERIES_DIR / "atoken_external_inflow.sql", params, end)
                raw = transfer_raw(chain, venue.token.address.value, sender.value, holder.value, month.first_day, month.last_day, end)
                frames("atoken_external_inflow.sql", d.assign(key="total"),
                       pd.DataFrame([{"key": "total", "total_amount": Decimal(raw) / 10**venue.token.decimals}]),
                       ["key"], ["total_amount"])
    if venue.lp_kind in {"uniswap_v3", "uniswap_v4"}:
        som = pin(chain, month.first_day - timedelta(days=1))
        if venue.lp_kind == "uniswap_v3":
            if chain != "ethereum":
                raise ValueError("The retained V3 Dune SQL is Ethereum-only; requires a chain-specific oracle")
            kwargs = {"nfpm_per_chain": {venue.chain: venue.nft_position_manager}} if venue.nft_position_manager else {}
            args = (chain, holder.value, venue.token.address.value, som, end)
            d = DuneV3InflowSource(**kwargs).liquidity_events_in_pool(*args)
            h = HyperSyncV3PositionSource(**kwargs).liquidity_events_in_pool(*args)
        else:
            from settle.compute.monthly_pnl import _univ4_pool_key_for
            kwargs = {"position_manager_per_chain": {venue.chain: venue.nft_position_manager}} if venue.nft_position_manager else {}
            ds, hs_lp = DuneUniswapV4FlowsSource(**kwargs), HyperSyncV4PositionSource(**kwargs)
            pool_id = _univ4_pool_key_for(venue).pool_id()
            args = (venue.chain, ds._pool_manager(venue.chain), pool_id, som, end)
            d, h = ds._modify_liquidity_events(*args), hs_lp._modify_liquidity_events(*args)
        checks.append({"input": venue.lp_kind + " liquidity events", "matched": d == h,
                       "dune_rows": len(d), "hypersync_rows": len(h),
                       "dune": [asdict(e) for e in d], "hypersync": [asdict(e) for e in h]})
    return {"prime": prime_id, "venue": venue_id, "month": month_label, "start": str(prime.start_date),
            "pins": {c + "/" + day: block for (c, day), block in pins.items()},
            "matched": bool(checks) and all(c["matched"] for c in checks), "checks": checks}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prime", required=True)
    ap.add_argument("--venue", required=True)
    ap.add_argument("--month", required=True)
    ap.add_argument("--tolerance", default="0.000001", help="absolute token units; raw integer comparisons remain exact")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    report = compare(args.prime, args.venue, args.month, Decimal(args.tolerance))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, default=str, indent=2) + "\n")
    return 0 if report["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
