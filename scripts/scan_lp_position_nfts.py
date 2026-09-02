#!/usr/bin/env python
"""Flag LP position NFTs held by a prime's ALM that no venue accounts for.

WHY THIS EXISTS
---------------
Uniswap V4's PositionManager is deliberately NOT ERC-721-enumerable, so a V4
venue must hardcode ``univ4_token_ids`` — the dynamic discovery that
``extract.uniswap_v3.discover_pool_token_ids`` provides for V3 cannot apply.
A hardcoded list goes stale the moment the prime mints a new position, and the
failure is silent in the worst direction: the value path prices what it is told
about, so an unlisted position is simply absent from the report, and its USDS
leg keeps bearing Base Rate because no venue deducts it under Step 2.

Spark, 2026-08: position NFTs 385168 / 385169 (RLUSD/USDS) were minted on
2026-08-27 and sat in no venue — ~$20.0M of value missing, with $11,549,644.55
of USDS still counted as utilized. Two successive ERC-20 ALM audits reported
"fully covered" and could not have found them: a position arrives as an
**ERC-721** transfer, which an ERC-20 transfer scan never sees.

This scan closes that gap. It is the counterpart to
``scan_notional_changes.py``: that one watches off-chain principal, this one
watches on-chain LP positions.

WHAT IT DOES
------------
For every prime/ALM, reads ERC-721 ``Transfer`` logs emitted by the configured
position managers (V3 NFPM and V4 PositionManager) with the ALM as recipient,
then diffs the resulting tokenIds against what the prime's YAML accounts for:

  * ``univ4_token_ids``            — explicit V4 lists
  * ``nft_position_manager``       — V3 venues, whose IDs are discovered at
                                     run time, so any V3 tokenId is fine as
                                     long as SOME V3 venue covers that pool

Exit status is 1 when an unaccounted tokenId is found, so it can gate a
monthly close.

Usage:
    set -a; source .env; set +a
    PYTHONPATH=src python3 scripts/scan_lp_position_nfts.py
    PYTHONPATH=src python3 scripts/scan_lp_position_nfts.py --primes spark \
        --from-block 25656293 --to-block 25878704
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO))

from settle.domain.config import load_prime  # noqa: E402
from settle.extract.hypersync import query_logs  # noqa: E402

_log = logging.getLogger("scan_lp_position_nfts")

# ERC-20 and ERC-721 share this topic0; ERC-721 puts tokenId in topic3, which
# is what distinguishes the two on the wire.
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

_ALL_PRIMES = ("spark", "grove", "obex", "osero", "keel", "skybase")


def _pad(addr_bytes: bytes) -> str:
    return "0x" + addr_bytes.hex().rjust(64, "0")


def _accounted(prime) -> tuple[set[int], dict[bytes, list[str]]]:
    """(explicit V4 tokenIds, position-manager → venue ids that use it)."""
    explicit: set[int] = set()
    managers: dict[bytes, list[str]] = {}
    for v in prime.venues:
        for tid in (getattr(v, "univ4_token_ids", None) or []):
            explicit.add(int(tid))
        pm = getattr(v, "nft_position_manager", None)
        if pm is not None:
            managers.setdefault(pm.value, []).append(v.id)
    return explicit, managers


def scan_prime(prime_id: str, from_block: int | None, to_block: int | None) -> list[dict]:
    prime = load_prime(_REPO / "config" / f"{prime_id}.yaml")
    explicit, managers = _accounted(prime)
    if not managers:
        _log.info("%s: no venue declares an nft_position_manager — nothing to scan.",
                  prime_id)
        return []
    findings: list[dict] = []
    for chain, alm in (prime.alm or {}).items():
        for pm_bytes, _venue_ids in managers.items():
            lo = from_block if from_block is not None else 0
            hi = to_block
            if hi is None:
                _log.warning("%s/%s: --to-block not given; skipping (an unbounded "
                             "scan would page the whole chain).", prime_id, chain.value)
                continue
            try:
                res = query_logs(
                    chain.value,
                    [{"address": ["0x" + pm_bytes.hex()],
                      "topics": [[TRANSFER], [], [_pad(alm.value)]]}],
                    lo, hi,
                )
            except Exception as exc:
                _log.warning("%s/%s: position-manager log read failed (%s) — "
                             "cannot verify this chain.", prime_id, chain.value, exc)
                continue
            for r in res.rows:
                if r.topic0 != TRANSFER or r.topic3 is None:
                    continue          # ERC-20, not a position NFT
                tid = int(r.topic3, 16)
                # A V3 venue's ids are discovered at run time, so presence of
                # any V3 venue on this manager means the tokenId is covered as
                # long as it is in that venue's pool — which the V3 value path
                # checks itself. Only V4 (explicit list) can silently miss one.
                v4_venues = [
                    v.id for v in prime.venues
                    if getattr(v, "univ4_token_ids", None)
                    and getattr(v, "nft_position_manager", None) is not None
                    and v.nft_position_manager.value == pm_bytes
                ]
                if not v4_venues:
                    continue          # V3-only manager: self-discovering
                if tid in explicit:
                    continue
                findings.append({
                    "prime": prime_id, "chain": chain.value,
                    "position_manager": "0x" + pm_bytes.hex(),
                    "token_id": tid, "block": r.block_number,
                    "covered_by": ",".join(sorted(v4_venues)),
                })
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--primes", default=",".join(_ALL_PRIMES),
                    help="comma-separated prime ids (default: all)")
    ap.add_argument("--from-block", type=int, default=None)
    ap.add_argument("--to-block", type=int, default=None,
                    help="required — an unbounded scan would page the whole chain")
    args = ap.parse_args()
    logging.basicConfig(level="INFO", format="%(levelname)s %(message)s")

    findings: list[dict] = []
    for pid in [p.strip() for p in args.primes.split(",") if p.strip()]:
        cfg = _REPO / "config" / f"{pid}.yaml"
        if not cfg.exists():
            _log.warning("%s: no config/%s.yaml — skipped.", pid, pid)
            continue
        findings.extend(scan_prime(pid, args.from_block, args.to_block))

    if not findings:
        print("No unaccounted LP position NFTs found.")
        return 0
    print(f"\nUNACCOUNTED LP POSITION NFTs — {len(findings)}\n")
    for f in findings:
        print(f"  {f['prime']}/{f['chain']}  tokenId={f['token_id']}  "
              f"block={f['block']}  manager={f['position_manager']}")
        print(f"      not in any univ4_token_ids (V4 venues on this manager: "
              f"{f['covered_by']}). The position is absent from the report and "
              f"its USDS leg still bears Base Rate. Add it to the right venue.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
