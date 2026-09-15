"""Compare complete calculations without writing or replacing settlement files.

The baseline uses Dune for the migrated event inputs. The candidate uses the
checked-in configuration, with every Dune execute_query alias forbidden even
on a cache hit. RPC contract valuation remains shared between both runs.
"""

import argparse
import dataclasses
import hashlib
import json
import sys
from contextlib import ExitStack
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from settle.compute import Sources, compute_monthly_pnl
from settle.domain.config import load_prime_by_id
from settle.domain.period import Month
from settle.domain.sky_tokens import PSM3_LEG_TOKENS
from settle.extract import dune
from settle.normalize.sources._paths import QUERIES_DIR
from settle.normalize.sources.dune_balances import DuneBalanceSource
from settle.normalize.sources.dune_debt import DuneDebtSource
from settle.normalize.sources.dune_psm3 import DunePsm3Source
from settle.normalize.sources.dune_ssr import DuneSSRSource
from settle.normalize.sources.hypersync_block_resolver import HyperSyncBlockResolver


class DailyDunePsm3Oracle(DunePsm3Source):
    """Dune share histories and exact EOD reserves for this month's calculation.

    The daily SQL preserves the legacy reserve running sum, reducing only the
    export size. Only certified EOD blocks are answerable; intraday requests
    fail instead of carrying a daily closing balance into an earlier block.
    Parent valuation arithmetic and RPC pricing are unchanged.
    """

    def __init__(self, prime, month, resolver):
        super().__init__(block_resolver=resolver)
        self.daily_reserves = {}
        self.pins = {}
        days = [month.first_day - timedelta(days=1) + timedelta(days=i)
                for i in range((month.last_day - month.first_day).days + 2)]
        for chain, cfg in prime.psm.items():
            blocks = {day: resolver.block_at_or_before(
                chain.value, datetime.combine(day, time(23, 59, 59), UTC),
            ) for day in days}
            pin = blocks[month.last_day]
            self.pins[chain.value] = pin
            self._load_holder_history(chain.value, prime.alm[chain].value, pin_block=pin)
            self._load_pool_history(chain.value, pin_block=pin)
            tokens = PSM3_LEG_TOKENS[chain]
            params = {"psm3": cfg.address.value, "usdc": tokens["USDC"].address.value,
                      "usds": tokens["USDS"].address.value,
                      "susds": tokens["sUSDS"].address.value, "start_month": "2024-01-01"}
            frame = dune.execute_query(
                QUERIES_DIR / f"psm3_reserves_daily_{chain.value}.sql", params, pin,
            )
            histories = {}
            for row in frame.to_dict("records"):
                token = row["token"]
                token = bytes(token) if isinstance(token, (bytes, bytearray)) else bytes.fromhex(str(token).removeprefix("0x"))
                day = pd.Timestamp(row["block_date"]).date()
                raw = Decimal(str(row["cum_balance_raw"]))
                if raw != raw.to_integral_value() or raw < 0:
                    raise ValueError("Invalid raw Dune PSM3 reserve")
                history = histories.setdefault(token, {})
                if day in history:
                    raise ValueError("Duplicate Dune PSM3 token/day")
                history[day] = int(raw)
            for token in tokens.values():
                history = histories.get(token.address.value)
                if not history:
                    raise ValueError(f"Missing Dune PSM3 reserve history: {chain}/{token.symbol}")
                for day, block in blocks.items():
                    value = next((v for d, v in sorted(history.items(), reverse=True) if d <= day), 0)
                    self.daily_reserves[chain.value, cfg.address.value, token.address.value, block] = value

    def pool_reserve_at(self, chain, token, psm3, block, *, decimals):
        key = (chain, bytes(psm3), bytes(token), block)
        if key not in self.daily_reserves:
            raise AssertionError(f"PSM3 daily oracle requested outside certified EOD pins: {chain}/{block}")
        return self.daily_reserves[key]


def differences(left, right, path="result"):
    if dataclasses.is_dataclass(left):
        left, right = dataclasses.asdict(left), dataclasses.asdict(right)
    if isinstance(left, dict) and isinstance(right, dict):
        for key in left.keys() | right.keys():
            if key not in left or key not in right:
                yield {"path": f"{path}.{key}", "reason": "missing key"}
            else:
                yield from differences(left[key], right[key], f"{path}.{key}")
    elif isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        if len(left) != len(right):
            yield {"path": path, "reason": "different lengths"}
        for i, (a, b) in enumerate(zip(left, right, strict=False)):
            yield from differences(a, b, f"{path}[{i}]")
    else:
        # The daily audit breakdown serializes amounts as strings already,
        # while headline fields remain Decimal. Apply the same numeric gate
        # to both representations; nonnumeric labels still compare exactly.
        if not isinstance(left, bool) and not isinstance(right, bool):
            try:
                a, b = Decimal(str(left)), Decimal(str(right))
            except InvalidOperation:
                pass
            else:
                if not a.is_finite() or not b.is_finite():
                    yield {"path": path, "reason": "nonfinite numeric value"}
                elif abs(a - b) > Decimal("0.000001"):
                    yield {"path": path, "dune": str(left), "hypersync": str(right), "difference": str(abs(a - b))}
                return
        if left != right:
            yield {"path": path, "dune": str(left), "hypersync": str(right)}


def compare(prime_id, month_label):
    prime, month = load_prime_by_id(prime_id), Month.parse(month_label)
    oracle = dataclasses.replace(
        prime, venues=[dataclasses.replace(v, event_source="dune") for v in prime.venues],
        psm={chain: dataclasses.replace(cfg, event_source="dune") for chain, cfg in prime.psm.items()},
    )
    print("Dune baseline", prime_id, month_label, flush=True)
    psm3 = DailyDunePsm3Oracle(oracle, month, HyperSyncBlockResolver()) if oracle.psm else None
    baseline = compute_monthly_pnl(oracle, month, sources=Sources(
        debt=DuneDebtSource(), balance=DuneBalanceSource(), ssr=DuneSSRSource(), psm3=psm3,
    ))
    print("HyperSync candidate (Dune forbidden)", prime_id, month_label, flush=True)
    original = dune.execute_query
    forbidden = []
    with ExitStack() as stack:
        # Imports throughout normalize bind the function directly. Patch all
        # aliases plus its defining module so future imports fail too.
        for module in list(sys.modules.values()):
            if module and getattr(module, "__name__", "").startswith("settle."):
                for name, value in list(vars(module).items()):
                    if value is original:
                        forbidden.append(stack.enter_context(patch.object(module, name, side_effect=AssertionError("Unexpected Dune query in migrated calculation"))))
        candidate = compute_monthly_pnl(prime, month)
    calls = sum(mock.call_count for mock in forbidden)
    if calls:
        raise AssertionError(f"Migrated calculation attempted {calls} Dune queries (including swallowed fallback errors)")
    mismatches = list(differences(baseline, candidate))
    fields = ["sky_revenue", "agent_rate", "prime_agent_revenue", "monthly_pnl"]
    def digest(result):
        return hashlib.sha256(json.dumps(dataclasses.asdict(result), sort_keys=True, default=str).encode()).hexdigest()
    return {"prime": prime_id, "month": month_label, "matched": not mismatches,
            "comparison": "all MonthlyPnL dataclass fields; absolute numeric tolerance 0.000001",
            "candidate_dune_calls": 0, "mismatches": mismatches,
            "psm_oracle": "Dune lifetime share histories and daily reserve running sums" if psm3 else None,
            "psm_pins": psm3.pins if psm3 else {},
            "output_sha256": {"dune": digest(baseline), "hypersync": digest(candidate)},
            "headlines": {f: {"dune": str(getattr(baseline, f)), "hypersync": str(getattr(candidate, f))} for f in fields}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prime", required=True)
    parser.add_argument("--month", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.prime, args.month)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print("matched", report["matched"], flush=True)
    return 0 if report["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
