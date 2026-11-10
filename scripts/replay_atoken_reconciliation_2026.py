"""Replay current Aave/SparkLend venue accounting from retained archive inputs.

This calls production capital, external-revenue and venue-revenue mechanisms.
It is a read-only component comparison, not a complete monthly settlement run.
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import hashlib
import json
import os
import socket
from datetime import UTC, date, datetime
from decimal import Decimal as D
from pathlib import Path
from unittest.mock import patch

from settle.compute.prime_agent_revenue import VenueRevenueInputs, compute_venue_revenue
from settle.domain import Address, Chain, Period
from settle.domain.config import load_prime_by_id
from settle.extract import hypersync_store, rpc
from settle.extract._keccak import keccak256
from settle.extract.hypersync import LogRow
from settle.extract.transfer_logs import TRANSFER_TOPIC0
from settle.normalize.positions import _atoken_external_revenue_usd, _atoken_index_weighted_inflow
from settle.normalize.sources import hypersync_venue_events
from settle.revenue.verification import canonical

ROOT = Path(__file__).resolve().parents[1]


def request_key(chain, method, params):
    return json.dumps([str(chain), method, params], sort_keys=True, separators=(",", ":"))


class RetainedStates:
    def __init__(self, document):
        self.doc = document
        self.rows = {}
        for query_key, query in document["queries"].items():
            for r in query["rows"]:
                key = (
                    json.loads(query_key)[0],
                    r["address"],
                    r["block_number"],
                    r["log_index"],
                    r["transaction_hash"],
                )
                if key in self.rows and self.rows[key] != r:
                    raise ValueError("Conflicting retained event")
                self.rows[key] = r
        self.scaled_proofs = []
        self.asset_by_key = {
            (v["chain"], v["token"], v["holder"]): v for v in document["venues"].values()
        }
        self.known = {}
        for identity, v in self.asset_by_key.items():
            blocks = []
            selector = (
                "0x" + keccak256(b"scaledBalanceOf(address)")[:4].hex() + v["holder"][2:].zfill(64)
            )
            for key in document["rpc"]:
                chain, method, params = json.loads(key)
                if (
                    chain == v["chain"]
                    and method == "eth_call"
                    and params[0]["to"] == v["token"]
                    and params[0]["data"] == selector
                ):
                    blocks.append(int(params[1], 16))
            self.known[identity] = sorted(blocks)

    def post(self, url, method, params):
        chain = url.removeprefix("retained:")
        key = request_key(chain, method, params)
        if key not in self.doc["rpc"]:
            raise ValueError("Missing retained archive state: " + key)
        return self.doc["rpc"][key]

    def scaled(self, chain, token, holder, block):
        identity = chain, "0x" + token.hex(), "0x" + holder.hex()
        v = self.asset_by_key[identity]
        known = self.known[identity]
        if block in known:
            return rpc.scaled_balance_of(Chain(chain), Address(token), Address(holder), block)
        # Binary search/empty-period probes can reuse an observed scaled
        # balance only when the complete Transfer history proves no movement.
        i = bisect.bisect_right(known, block) - 1
        reference = known[i] if i >= 0 else 0
        if not reference and self.doc["queries"][v["query"]]["from_block"] != 0:
            raise ValueError("State probe precedes the verified opening anchor")
        rows = self.doc["queries"][v["query"]]["rows"]
        if any(
            r["topic0"] == TRANSFER_TOPIC0 and reference < r["block_number"] <= block for r in rows
        ):
            raise ValueError("Uncaptured scaled state follows a holder event")
        value = (
            rpc.scaled_balance_of(Chain(chain), Address(token), Address(holder), reference)
            if reference
            else 0
        )
        self.scaled_proofs.append(
            dict(
                chain=chain,
                token=v["token"],
                holder=v["holder"],
                block=block,
                reference_block=reference,
                value=str(value),
                basis="Pinned scaled balance plus complete history with no intervening holder Transfer",
            )
        )
        return value

    def balance(self, chain, token, holder, block):
        if self.scaled(chain, token, holder, block) == 0:
            return 0
        return rpc.balance_of(Chain(chain), Address(token), Address(holder), block)

    @staticmethod
    def covers(selection, source):
        if not set(selection["address"]).issubset(source["address"]):
            return False
        for i, allowed in enumerate(source.get("topics", [])):
            chosen = selection.get("topics", [])[i] if i < len(selection.get("topics", [])) else []
            if allowed and (not chosen or not set(chosen).issubset(allowed)):
                return False
        return True

    def fetch(self, chain, selections, start, end, **kwargs):
        queries = [q for key, q in self.doc["queries"].items() if json.loads(key)[0] == chain]
        for s in selections:
            if not any(
                q["from_block"] <= start
                and q["to_block"] >= end
                and q["archive_height"] >= end
                and any(self.covers(s, full) for full in q["selections"])
                for q in queries
            ):
                raise ValueError("Retained source does not cover external-income selection")
        unique = {}
        for q in queries:
            for r in q["rows"]:
                if not start <= r["block_number"] <= end:
                    continue
                if any(
                    r["address"] in s["address"]
                    and all(
                        not t or r["topic" + str(i)] in t for i, t in enumerate(s.get("topics", []))
                    )
                    for s in selections
                ):
                    unique[r["block_number"], r["log_index"]] = LogRow(**r)
        return [unique[k] for k in sorted(unique)]


def replay(path):
    document = json.loads(gzip.decompress(path.read_bytes()))
    if document["status"] != "event_states_complete":
        raise ValueError("Pinned capture is incomplete")
    for name, digest in document["config_sha256"].items():
        if hashlib.sha256((ROOT / "config" / f"{name}.yaml").read_bytes()).hexdigest() != digest:
            raise ValueError("Captured configuration changed")
    source = RetainedStates(document)
    results = []

    def blocked(*args, **kwargs):
        raise AssertionError("Offline replay attempted network")

    with (
        patch.dict(os.environ, {"SETTLE_NO_CACHE": "1"}),
        patch.object(socket.socket, "connect", blocked),
        patch.object(rpc, "rpc_url", lambda c: "retained:" + c.value),
        patch.object(rpc, "_post", source.post),
        patch.object(hypersync_store, "fetch_logs", source.fetch),
    ):
        for record in sorted(document["baselines"], key=lambda r: (r["prime"], r["month"])):
            prime = load_prime_by_id(record["prime"])
            start, end = map(
                date.fromisoformat, [record["period"]["start"], record["period"]["end"]]
            )
            period = Period(start, end, {Chain(c): b for c, b in record["closing"].items()})
            for _identity, capture in document["venues"].items():
                if capture["prime"] != prime.id:
                    continue
                venue = next(v for v in prime.venues if v.id == capture["venue"])
                if venue.start_date and end < venue.start_date:
                    continue
                old = next((r for r in record["venues"] if r["venue_id"] == venue.id), None)
                if old and D(old["sd_share"]) != 0:
                    raise ValueError("Unexpected SDE: full SDE replay required")
                som = record["opening"][venue.chain.value]
                eom = record["closing"][venue.chain.value]
                holder = venue.holder_override or prime.alm[venue.chain]
                rows = document["queries"][capture["query"]]["rows"]
                blocks = {
                    r["block_number"]: r["block_time"]
                    for r in rows
                    if r["topic0"] == TRANSFER_TOPIC0 and som < r["block_number"] <= eom
                }
                boundaries = [
                    (b - 1, b, datetime.fromtimestamp(t, UTC).date())
                    for b, t in sorted(blocks.items())
                ]
                kwargs = dict(
                    scaled_balance_at=source.scaled,
                    balance_at=source.balance,
                    transfer_event_blocks=lambda *args, b=boundaries: b,
                )
                flows = _atoken_index_weighted_inflow(
                    prime, venue, som, eom, period_end_date=end, **kwargs
                )
                opening = (
                    D(
                        source.balance(
                            venue.chain.value, venue.token.address.value, holder.value, som
                        )
                    )
                    / 10**venue.token.decimals
                )
                closing = (
                    D(
                        source.balance(
                            venue.chain.value, venue.token.address.value, holder.value, eom
                        )
                    )
                    / 10**venue.token.decimals
                )
                if old and (
                    abs(opening - D(old["value_som"])) > D(".000001")
                    or abs(closing - D(old["value_eom"])) > D(".000001")
                ):
                    raise ValueError("Archive balances differ from the settled valuation baseline")

                def floor(chain, day, record=record, start=start):
                    if chain not in record["opening"]:
                        raise ValueError("Missing income source chain pin")
                    if day != start:
                        raise ValueError("Unexpected income source interval")
                    return record["opening"][chain]

                with patch.object(hypersync_venue_events, "_default_start_block", floor):
                    external = _atoken_external_revenue_usd(prime, venue, period)
                new = compute_venue_revenue(
                    period,
                    VenueRevenueInputs(
                        venue=venue,
                        value_som=opening,
                        value_eom=closing,
                        inflow_timeseries=flows,
                        external_revenue=external,
                    ),
                )
                prior = D(old["revenue"]) if old else D(0)
                delta = new.revenue - prior
                results.append(
                    dict(
                        prime=prime.id,
                        month=record["month"],
                        venue=venue.id,
                        chain=venue.chain.value,
                        opening_block=som,
                        closing_block=eom,
                        old=old,
                        new=canonical(new),
                        income_delta=str(delta),
                        native_income_delta=str(
                            new.actual_revenue - (D(old["actual_revenue"]) if old else D(0))
                        ),
                        external_income_delta=str(
                            external - (D(old["external_revenue"]) if old else D(0))
                        ),
                        flows=canonical(flows.to_dict("records")),
                        event_blocks=len(blocks),
                        global_cof_delta="0",
                    )
                )
                print(prime.id, record["month"], venue.id, "income change", delta, flush=True)
    return dict(
        status="production_component_replay_complete",
        earning_window=["2026-01", "2026-09"],
        input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        implementation_files_sha256={
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in [
                "scripts/replay_atoken_reconciliation_2026.py",
                "src/settle/normalize/positions.py",
                "src/settle/compute/prime_agent_revenue.py",
                "src/settle/normalize/sources/hypersync_venue_events.py",
            ]
        },
        results=results,
        constant_scaled_state_proofs=source.scaled_proofs,
        note="Raw income changes require joint prior-payment and period-net settlement reconciliation before booking.",
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inputs", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    result = replay(args.inputs)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
