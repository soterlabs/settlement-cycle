#!/usr/bin/env python3
"""Publish reviewed conditional estimates as a separate additive monthly revision.

Dry run by default. No replay, revenue calculation, daily-table or settlement
writes. --publish verifies the baseline in the database and rejects stale work.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from settle.revenue.allocations import publish, read
from settle.revenue.conditional_allocations import build_conditional_document
from settle.revenue.verification import digest
from settle.store.db import connect

ROOT = Path(__file__).resolve().parents[1]


def prepare(base, manifest_path):
    manifest = json.loads(manifest_path.read_text())
    if manifest["mode"] != "conditional_estimates_only" or (
        manifest["prime"],
        manifest["month"],
    ) != (base["prime"], base["month"]):
        raise ValueError("Reviewed manifest does not authorize this publication scope")
    inputs, hashes = (
        {},
        {"conditional_manifest": hashlib.sha256(manifest_path.read_bytes()).hexdigest()},
    )
    for name, entry in manifest["evidence"].items():
        path = (ROOT / entry["path"]).resolve()
        if not path.is_relative_to(ROOT):
            raise ValueError("Evidence must be inside the repository")
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != entry["sha256"]:
            raise ValueError("Conditional evidence hash differs from reviewed manifest")
        inputs[name] = json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)
        hashes["conditional_" + name] = actual
    return build_conditional_document(
        base,
        inputs["estimates"],
        inputs["bridge"],
        inputs["savings"],
        inputs["control"],
        inputs["anchorage"],
        source_hashes=hashes,
    )


def publish_reviewed(conn, base, manifest_path, reviewed):
    # Monthly writers are serialized for this short read/verify/insert section.
    # No locks on daily revenues, cache tables or settlement data.
    conn.execute("LOCK TABLE allocation_monthly_results IN SHARE ROW EXCLUSIVE MODE")
    stored = read(conn, base["prime"], base["month"], revision=base["revision_id"])
    if stored is None:
        raise ValueError("Reviewed baseline revision is not stored")
    rebuilt = prepare(stored, manifest_path)
    if digest(rebuilt) != digest(reviewed):
        raise ValueError("Reviewed document does not match the stored baseline")
    latest = read(conn, base["prime"], base["month"])
    if latest["revision_id"] not in {base["revision_id"], digest(rebuilt)}:
        raise ValueError("A newer baseline exists; review again before publishing")
    return publish(conn, rebuilt)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--publish", action="store_true")
    args = p.parse_args()
    manifest = json.loads(args.manifest.read_text())
    protected = {args.base.resolve(), args.manifest.resolve()} | {
        (ROOT / entry["path"]).resolve() for entry in manifest["evidence"].values()
    }
    if args.output.resolve() in protected:
        p.error("Output cannot overwrite an input")
    base = json.loads(args.base.read_text())
    document = prepare(base, args.manifest)
    args.output.write_text(json.dumps(document, indent=2) + "\n")
    if args.publish:
        with connect() as conn, conn.transaction():
            revision = publish_reviewed(conn, base, args.manifest, document)
        print(
            json.dumps(
                {"published": revision, "prime": document["prime"], "month": document["month"]}
            )
        )
    else:
        print(
            json.dumps(
                {
                    "publish": False,
                    "revision": digest(document),
                    "conditional_allocations": document["conditional_funding"]["allocation_count"],
                }
            )
        )


if __name__ == "__main__":
    main()
