#!/usr/bin/env python3
"""Prepare an idle-USDC-only August allocation API revision; dry-run by default."""

import argparse
import json
from pathlib import Path

from settle.revenue.allocations import publish, read
from settle.revenue.spark_idle_usdc_repair import EVIDENCE, ROOT, repair_document
from settle.revenue.verification import digest
from settle.store.db import connect


def publish_reviewed(conn, base, reviewed):
    conn.execute("LOCK TABLE allocation_monthly_results IN SHARE ROW EXCLUSIVE MODE")
    stored = read(conn, "spark", "2026-08", revision=base["revision_id"])
    if stored is None:
        raise ValueError("Reviewed baseline is not stored")
    rebuilt = repair_document(stored)
    if digest(rebuilt) != digest(reviewed):
        raise ValueError("Reviewed idle-USDC repair differs from stored baseline")
    latest = read(conn, "spark", "2026-08")
    if latest["revision_id"] not in {base["revision_id"], digest(rebuilt)}:
        raise ValueError("Newer baseline exists; review again")
    return publish(conn, rebuilt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    manifest_path = ROOT / "reconciliation/spark_s66_august_repair_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    protected = {args.base.resolve(), (ROOT / EVIDENCE).resolve(), manifest_path.resolve()}
    protected.update((ROOT / e["path"]).resolve() for e in manifest["evidence"].values())
    if args.output.resolve() in protected:
        parser.error("Output cannot overwrite evidence or baseline")
    base = json.loads(args.base.read_text())
    repaired = repair_document(base)
    args.output.write_text(json.dumps(repaired, indent=2) + "\n")
    if args.publish:
        with connect() as conn, conn.transaction():
            revision = publish_reviewed(conn, base, repaired)
        print(
            json.dumps(
                {
                    "published": revision,
                    "prime": "spark",
                    "month": "2026-08",
                    "venues": ["S45", "S53", "S55"],
                }
            )
        )
    else:
        print(
            json.dumps(
                {"publish": False, "revision": digest(repaired), "venues": ["S45", "S53", "S55"]}
            )
        )


if __name__ == "__main__":
    main()
