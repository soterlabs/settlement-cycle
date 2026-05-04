"""Command-line entry point.

Usage:
    settle version
    settle config check --prime <id>
    settle debug rpc-balance --chain <c> --token <addr> --holder <addr> [--block <n>]
    settle run --prime <id> --month <YYYY-MM> [--output-dir <path>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .domain import Address, Chain
from .domain.config import load_prime_by_id
from .domain.period import Month, Period


def _cmd_version(_args: argparse.Namespace) -> int:
    print(f"settle {__version__}")
    return 0


def _cmd_config_check(args: argparse.Namespace) -> int:
    prime = load_prime_by_id(args.prime)
    print(f"Prime:           {prime.id}")
    print(f"Ilk (bytes32):   0x{prime.ilk_bytes32.hex()}")
    print(f"Start date:      {prime.start_date}")
    print(f"Chains:          {sorted(c.value for c in prime.chains)}")
    print(f"Subproxy:")
    for chain, addr in sorted(prime.subproxy.items()):
        print(f"  {chain.value:14s} {addr.hex}")
    print(f"ALM proxy:")
    for chain, addr in sorted(prime.alm.items()):
        print(f"  {chain.value:14s} {addr.hex}")
    print(f"Venues ({len(prime.venues)}):")
    for v in prime.venues:
        u = f" ← {v.underlying.symbol}" if v.underlying else ""
        print(f"  {v.id:4s} [{v.pricing_category.value}] {v.chain.value:10s} "
              f"{v.token.symbol}{u} ({v.label})")
    return 0


def _cmd_debug_rpc_balance(args: argparse.Namespace) -> int:
    from .extract import rpc as rpc_mod

    chain = Chain(args.chain)
    token = Address.from_str(args.token)
    holder = Address.from_str(args.holder)
    block = args.block or rpc_mod.latest_block(chain)

    raw = rpc_mod.balance_of(chain, token, holder, block)
    decimals = rpc_mod.decimals_of(chain, token, block)
    units = raw / (10 ** decimals)

    print(f"chain:    {chain.value}")
    print(f"token:    {token.hex}")
    print(f"holder:   {holder.hex}")
    print(f"block:    {block}")
    print(f"decimals: {decimals}")
    print(f"raw:      {raw}")
    print(f"units:    {units:.{decimals}f}")
    return 0


def _cmd_debug_position_value(args: argparse.Namespace) -> int:
    """Live end-to-end probe: balance + price + value for one venue."""
    from .extract import rpc as rpc_mod
    from .normalize.positions import get_position_balance, get_position_value
    from .normalize.prices import get_unit_price

    prime = load_prime_by_id(args.prime)
    venue = next((v for v in prime.venues if v.id == args.venue), None)
    if venue is None:
        ids = [v.id for v in prime.venues]
        print(f"Unknown venue {args.venue!r}; available: {ids}", file=sys.stderr)
        return 2

    block = args.block or rpc_mod.latest_block(venue.chain)
    balance = get_position_balance(prime, venue, block)
    price = get_unit_price(venue, block)
    value = get_position_value(prime, venue, block)

    print(f"prime:        {prime.id}")
    print(f"venue:        {venue.id} ({venue.label})")
    print(f"category:     {venue.pricing_category.value}")
    print(f"chain:        {venue.chain.value}")
    print(f"token:        {venue.token.address.hex}  ({venue.token.symbol})")
    if venue.underlying:
        print(f"underlying:   {venue.underlying.address.hex}  ({venue.underlying.symbol})")
    print(f"block:        {block}")
    print(f"balance:      {balance:,.{venue.token.decimals}f} {venue.token.symbol}")
    print(f"unit_price:   ${price} USD/{venue.token.symbol}")
    print(f"VALUE:        ${value:,.6f}")
    return 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    """Live point-in-time balance sheet for a prime — parity target is BA labs
    `stars-api.blockanalitica.com/stars/{prime}/`.
    """
    import json as _json
    from .snapshot import compute_snapshot

    prime = load_prime_by_id(args.prime)
    pin_blocks = {Chain.ETHEREUM: args.block} if args.block else None
    snap = compute_snapshot(prime, pin_blocks=pin_blocks)

    if args.json:
        out = {
            "prime_id": snap.prime_id,
            "generated_at_utc": snap.generated_at_utc.isoformat(),
            "pin_blocks": {c.value: b for c, b in snap.pin_blocks.items()},
            "venues_total_usd": str(snap.venues_total_usd),
            "treasury_balance_usd": str(snap.treasury_balance_usd),
            "idle_assets_usd": str(snap.idle_assets_usd),
            "in_transit_assets_usd": str(snap.in_transit_assets_usd),
            "assets_usd": str(snap.assets_usd),
            "debt_usd": str(snap.debt_usd) if snap.debt_usd is not None else None,
            "liabilities_usd": str(snap.liabilities_usd),
            "nav_usd": str(snap.nav_usd),
            "venues": [
                {"venue_id": v.venue_id, "label": v.label, "chain": v.chain.value,
                 "value_usd": str(v.value_usd), "shares": str(v.shares),
                 "block": v.block, "note": v.note}
                for v in snap.venues
            ],
            "idle": [
                {"label": h.label, "category": h.category, "shares": str(h.shares),
                 "value_usd": str(h.value_usd), "block": h.block}
                for h in snap.idle
            ],
        }
        print(_json.dumps(out, indent=2))
        return 0

    print("=" * 78)
    print(f"SNAPSHOT — {snap.prime_id} — {snap.generated_at_utc.isoformat(timespec='seconds')}")
    print("=" * 78)
    print(f"  pin_blocks:           {dict((c.value, b) for c, b in snap.pin_blocks.items())}")
    print()
    print(f"  Venues ({sum(1 for v in snap.venues if v.value_usd > 0)} non-zero / {len(snap.venues)} total):")
    for v in sorted(snap.venues, key=lambda x: -x.value_usd):
        if v.value_usd == 0 and not v.note:
            continue
        flag = "" if not v.note else f"  [{v.note[:40]}]"
        print(f"    {v.venue_id:5s} {v.chain.value:11s} {v.label[:40]:40s} ${v.value_usd:>16,.2f}{flag}")
    print()
    print(f"  Idle / treasury holdings:")
    for h in snap.idle:
        print(f"    {h.label:25s} ({h.category:9s})  ${h.value_usd:>16,.2f}")
    print()
    print(f"  ─── Aggregates ─────────────────────────────────────────────")
    print(f"    venues_total:       ${snap.venues_total_usd:>20,.2f}")
    print(f"    treasury_balance:   ${snap.treasury_balance_usd:>20,.2f}")
    print(f"    idle_assets:        ${snap.idle_assets_usd:>20,.2f}")
    print(f"    in_transit_assets:  ${snap.in_transit_assets_usd:>20,.2f}")
    print(f"    ─────────────────────────────────────")
    print(f"    assets:             ${snap.assets_usd:>20,.2f}")
    print(f"    debt (Vat.ilks):    ${(snap.debt_usd or 0):>20,.2f}")
    print(f"    liabilities:        ${snap.liabilities_usd:>20,.2f}")
    print(f"    nav:                ${snap.nav_usd:>20,.2f}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    """End-to-end settlement run: Extract → Normalize → Compute → Load."""
    from pathlib import Path

    from .compute import compute_monthly_pnl
    from .load import default_output_dir, write_settlement

    prime = load_prime_by_id(args.prime)
    month = Month.parse(args.month)

    print(f"settle run {prime.id} {month}")
    print("  resolving pin blocks via RPC (~50 calls per chain)…")
    result = compute_monthly_pnl(prime, month)

    print()
    print("=" * 70)
    print(f"MONTHLY PnL — {prime.id} — {month}")
    print("=" * 70)
    print(f"  Period:                   {result.period.start} → {result.period.end}")
    print(f"  EoM block (ethereum):     {result.period.pin_blocks.get(Chain.ETHEREUM)}")
    print(f"  SoM block (ethereum):     {result.pin_blocks_som.get(Chain.ETHEREUM)}")
    print()
    print(f"  prime_agent_revenue:      ${result.prime_agent_revenue:>20,.2f}")
    print(f"  agent_rate:               ${result.agent_rate:>20,.2f}")
    print(f"  sky_revenue:             −${result.sky_revenue:>20,.2f}")
    print(f"  ─────────────────────────────────────────────")
    print(f"  monthly_pnl:              ${result.monthly_pnl:>20,.2f}")
    print()
    if result.venue_breakdown:
        print("  Per-venue breakdown:")
        for v in result.venue_breakdown:
            print(f"    {v.venue_id:5s}  som=${v.value_som:>15,.2f}  "
                  f"eom=${v.value_eom:>15,.2f}  "
                  f"inflow=${v.period_inflow:>13,.2f}  "
                  f"revenue=${v.revenue:>15,.2f}")

    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(prime.id, str(month))
    written = write_settlement(result, output_dir)
    print()
    print(f"  Artifacts written to: {output_dir}")
    for name, path in written.items():
        print(f"    {name:11s} {path.relative_to(output_dir.resolve().anchor) if path.is_absolute() else path}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="settle", description="MSC monthly settlement pipeline")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version", help="Print version").set_defaults(func=_cmd_version)

    p_cfg = sub.add_parser("config", help="Config inspection")
    p_cfg_sub = p_cfg.add_subparsers(dest="config_cmd", required=True)
    p_cfg_check = p_cfg_sub.add_parser("check", help="Load and print a prime config")
    p_cfg_check.add_argument("--prime", required=True, help="Prime id (e.g. 'obex')")
    p_cfg_check.set_defaults(func=_cmd_config_check)

    p_dbg = sub.add_parser("debug", help="Extract-layer probes")
    p_dbg_sub = p_dbg.add_subparsers(dest="debug_cmd", required=True)
    p_dbg_bal = p_dbg_sub.add_parser("rpc-balance", help="ERC-20 balanceOf via RPC")
    p_dbg_bal.add_argument("--chain", required=True, choices=[c.value for c in Chain])
    p_dbg_bal.add_argument("--token", required=True, help="ERC-20 contract address")
    p_dbg_bal.add_argument("--holder", required=True, help="Holder address")
    p_dbg_bal.add_argument("--block", type=int, help="Block number (default: latest)")
    p_dbg_bal.set_defaults(func=_cmd_debug_rpc_balance)

    p_dbg_pv = p_dbg_sub.add_parser(
        "position-value",
        help="Live balance + unit price + USD value for one prime/venue",
    )
    p_dbg_pv.add_argument("--prime", required=True, help="Prime id (e.g. 'obex')")
    p_dbg_pv.add_argument("--venue", required=True, help="Venue id (e.g. 'V1')")
    p_dbg_pv.add_argument("--block", type=int, help="Block number (default: latest)")
    p_dbg_pv.set_defaults(func=_cmd_debug_position_value)

    p_run = sub.add_parser("run", help="Run a settlement end-to-end")
    p_run.add_argument("--prime", required=True, help="Prime id")
    p_run.add_argument("--month", required=True, help="Settlement month YYYY-MM")
    p_run.add_argument(
        "--output-dir",
        help="Override output directory (default: <repo>/settlements/<prime>/<month>/)",
    )
    p_run.set_defaults(func=_cmd_run)

    p_snap = sub.add_parser(
        "snapshot",
        help="Live point-in-time balance sheet (parity target: BA labs stars-api)",
    )
    p_snap.add_argument("--prime", required=True, help="Prime id (e.g. 'grove' / 'spark')")
    p_snap.add_argument("--block", type=int, help="Pin Eth block (default: latest per chain)")
    p_snap.add_argument("--json", action="store_true", help="Emit JSON instead of human-readable table")
    p_snap.set_defaults(func=_cmd_snapshot)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
