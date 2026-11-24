#!/usr/bin/env python3
"""Export configured MSC address references without RPC or settlement execution."""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from settle.domain.config import _parse_view  # noqa: E402

ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")
CHAIN_IDS = {
    "ethereum": 1,
    "base": 8453,
    "arbitrum": 42161,
    "optimism": 10,
    "unichain": 130,
    "avalanche_c": 43114,
    "plume": 98866,
    "monad": 143,
    "robinhood": 4663,
    "xlayer": 196,
}
FIELDS = [
    "prime",
    "chain",
    "chain_id",
    "address",
    "role",
    "label",
    "venue_id",
    "venue_label",
    "msc_enabled",
    "reporting_enabled",
    "settlement_method",
    "watch_alm",
    "skip_position",
    "start_date",
    "end_date",
    "source_file",
    "source_path",
    "chain_basis",
    "is_zero_address",
]


def role_for(path: str) -> str:
    """Roles describe config references, not unrestricted payment permissions."""
    if path.startswith("addresses."):
        if path.endswith(".alm"):
            return "alm_proxy"
        if path.endswith(".subproxy"):
            return "subproxy"
        if ".psm." in path:
            return "psm"
    if path == "basin_idle_usds.holder":
        return "alm_proxy"
    if path.startswith("basin_idle_usds.basins"):
        return "basin"
    if path == "chronicle_points.farm":
        return "chronicle_farm"
    for prefix, role in (
        ("external_alm_sources.", "external_yield_source"),
        ("external_alm_source_start_dates.", "external_yield_activation"),
        ("capital_income_sources.", "capital_income_source"),
        ("principal_return_overrides.", "principal_return_override"),
    ):
        if path.startswith(prefix):
            return role
    if ".nav_oracle." in path:
        return "oracle"
    if ".implementations[" in path:
        return "implementation"
    if ".async_allocation.managers[" in path:
        return "redemption_manager"
    if ".share_burn_destinations[" in path:
        return "share_burn_destination"
    if ".notional_counterparties[" in path:
        return "funding_counterparty"
    key = path.rsplit(".", 1)[-1]
    return {
        "holder_override": "position_holder",
        "payer": "payment_source",
        "recipient": "payment_recipient",
        "cash_payer": "redemption_payer",
        "request_receiver": "redemption_receiver",
        "cash_token": "cash_token",
        "share_token": "share_token",
        "centrifuge_vault": "investment_vault",
        "nft_position_manager": "nft_position_manager",
        "hooks": "pool_hook",
        "currency0": "pool_asset",
        "currency1": "pool_asset",
        "coin": "pool_asset",
        "sde_coin": "sde_asset",
        "pool": "incentive_pool",
        "vault": "incentive_vault",
        "paired_source": "paired_source",
        "token": "cash_token",
    }.get(
        key,
        "underlying_token"
        if ".underlying.address" in path
        else "venue_token"
        if ".token.address" in path
        else "configured_address",
    )


def build_rows(config_dir: Path) -> list[dict[str, str]]:
    documents = {p.name: yaml.safe_load(p.read_text()) for p in sorted(config_dir.glob("*.yaml"))}
    primes = {
        name: d
        for name, d in documents.items()
        if isinstance(d, dict) and "addresses" in d and "id" in d
    }
    if not primes:
        raise ValueError(f"No Prime configurations found in {config_dir}")
    prime_by_id = {d["id"]: d for d in primes.values()}
    venues = {(d["id"], v["id"]): v for d in primes.values() for v in d.get("venues", [])}
    rows = []

    def walk(node, path, context, file):
        ctx = dict(context)
        if isinstance(node, dict):
            # Oracle and receipt-source chains can differ from their parent venue.
            for key in ("chain", "oracle_chain"):
                if node.get(key):
                    ctx.update(chain=node[key], chain_basis=f"{path}.{key}".lstrip("."))
            for key in ("start_date", "end_date"):
                if node.get(key):
                    current = ctx.get(key, "")
                    value = str(node[key])
                    ctx[key] = (
                        max(current, value)
                        if key == "start_date"
                        else min(current, value)
                        if current
                        else value
                    )
            if node.get("symbol"):
                ctx["symbol"] = node["symbol"]
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                child_ctx = dict(ctx)
                if key in CHAIN_IDS:
                    child_ctx.update(chain=key, chain_basis=child)
                if child == "basin_idle_usds" or child == "chronicle_points":
                    child_ctx.update(chain="ethereum", chain_basis="Ethereum-only config section")
                    if isinstance(value, dict) and value.get("effective_from"):
                        child_ctx["start_date"] = str(value["effective_from"])
                if isinstance(key, str) and ADDRESS.fullmatch(key):
                    if path.startswith("external_alm_source_start_dates."):
                        child_ctx["start_date"] = str(value)
                    walk(key, child, child_ctx, file)
                if child == "venues":
                    for index, venue in enumerate(value):
                        venue_ctx = dict(child_ctx)
                        venue_ctx.update(
                            venue_id=venue["id"],
                            venue_label=venue.get("label", venue["id"]),
                            msc_enabled=_parse_view(venue, "settlement").enabled,
                            reporting_enabled=_parse_view(venue, "reporting").enabled,
                            skip_position=venue.get("skip", False),
                            settlement_method=venue.get("settlement", {}).get("method", "existing"),
                        )
                        walk(venue, f"venues[{index}]", venue_ctx, file)
                else:
                    walk(value, child, child_ctx, file)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]", ctx, file)
        elif isinstance(node, str) and ADDRESS.fullmatch(node):
            chain = ctx.get("chain")
            if chain not in CHAIN_IDS:
                raise ValueError(f"Unresolved chain: {file}:{path}")
            role = role_for(path)
            start = ctx.get("start_date", "")
            if role == "external_yield_source":
                starts = {k.lower(): v for k, v in ctx["source_starts"].get(chain, {}).items()}
                start = max(start, str(starts.get(node.lower(), start)))
            row = {key: str(ctx.get(key, "")) for key in FIELDS}
            row.update(
                address=node.lower(),
                chain_id=str(CHAIN_IDS[chain]),
                role=role,
                label=f"{ctx.get('symbol') or ctx.get('venue_label') or ctx['prime']} — {role}",
                source_file=file,
                source_path=path,
                start_date=start,
                watch_alm=str(role == "alm_proxy" and int(node, 16) != 0).lower(),
                is_zero_address=str(int(node, 16) == 0).lower(),
            )
            for key in ("msc_enabled", "reporting_enabled", "skip_position"):
                if type(ctx[key]) is not bool:
                    raise ValueError(f"Invalid {key}: {file}:{path}")
                row[key] = str(ctx[key]).lower()
            rows.append(row)

    for name, doc in primes.items():
        context = dict(
            prime=doc["id"],
            msc_enabled=True,
            reporting_enabled=True,
            skip_position=False,
            source_starts=doc.get("external_alm_source_start_dates", {}),
        )
        walk(doc, "", context, "config/" + name)
    for name, doc in documents.items():
        if name in primes or not isinstance(doc, dict):
            continue
        for index, entry in enumerate(doc.get("venues", [])):
            if not isinstance(entry, dict) or not {"prime", "venue_id"} <= entry.keys():
                continue
            key = (entry["prime"], entry["venue_id"])
            if key not in venues:
                raise ValueError(f"Unknown supplementary venue: {name}:{key}")
            venue = venues[key]
            parent = prime_by_id[entry["prime"]]
            context = dict(
                prime=entry["prime"],
                venue_id=entry["venue_id"],
                venue_label=venue["label"],
                chain=venue["chain"],
                chain_basis="linked venue chain",
                msc_enabled=_parse_view(venue, "settlement").enabled,
                reporting_enabled=_parse_view(venue, "reporting").enabled,
                skip_position=venue.get("skip", False),
                settlement_method=venue.get("settlement", {}).get("method", "existing"),
                start_date=max(
                    str(parent.get("start_date") or ""), str(venue.get("start_date") or "")
                ),
                end_date=min(
                    (str(d["end_date"]) for d in (parent, venue) if d.get("end_date")), default=""
                ),
            )
            walk(entry, f"venues[{index}]", context, "config/" + name)
    return sorted(
        rows,
        key=lambda r: tuple(
            r[k] for k in ("prime", "chain", "address", "source_file", "source_path")
        ),
    )


def render(rows: list[dict[str, str]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    parser.add_argument("--output", type=Path, default=ROOT / "monitoring/msc_addresses.csv")
    parser.add_argument(
        "--check", action="store_true", help="Fail if the committed export is stale"
    )
    args = parser.parse_args()
    content = render(build_rows(args.config_dir))
    if args.check:
        if not args.output.exists() or args.output.read_text() != content:
            parser.exit(
                1, "Address inventory is stale; regenerate with scripts/export_msc_addresses.py\n"
            )
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
