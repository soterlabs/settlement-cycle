#!/usr/bin/env python3
"""Publish reviewed monthly analytics without rerunning revenue or capital.

Default: emit a reviewable JSON bundle. --publish atomically imports all
documents into the DATABASE_URL store. Never writes daily revenue/settlements.
"""

import argparse
import gzip
import hashlib
import json
from pathlib import Path

from settle.revenue.allocations import apply_schema, build_documents, publish
from settle.store.db import connect


def prepare(readiness_path, yields_path):
    readiness = json.loads(gzip.decompress(readiness_path.read_bytes()))
    yields = json.loads(yields_path.read_text())
    # The yield artifact must have been calculated from exactly this capital
    # result. No mix of old yield and newly revised borrowing costs is allowed.
    if (
        hashlib.sha256(readiness_path.read_bytes()).hexdigest()
        not in yields["input_sha256"].values()
    ):
        raise ValueError("Yield artifact does not reference this capital snapshot")
    for path, expected in readiness["input_sha256"].items():
        if Path(path).name == "provenance.json" and yields["input_sha256"].get(path) != expected:
            raise ValueError("Revenue control hashes differ")
    return build_documents(
        readiness,
        yields,
        source_hashes={
            "capital_artifact": hashlib.sha256(readiness_path.read_bytes()).hexdigest(),
            "yield_artifact": hashlib.sha256(yields_path.read_bytes()).hexdigest(),
            "publisher": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--readiness", type=Path, required=True)
    p.add_argument("--yields", type=Path, required=True)
    p.add_argument("--output", type=Path)
    p.add_argument("--publish", action="store_true")
    args = p.parse_args()
    if args.output and args.output.resolve() in {args.readiness.resolve(), args.yields.resolve()}:
        p.error("Output must not overwrite either input")
    documents = prepare(args.readiness, args.yields)
    if args.output:
        args.output.write_text(json.dumps(documents, indent=2) + "\n")
    if args.publish:
        with connect() as conn:
            with conn.transaction():
                apply_schema(conn)
                revisions = {d["prime"]: publish(conn, d) for d in documents}
        print(json.dumps({"published": revisions}))
    else:
        print(
            json.dumps(
                {
                    "publish": False,
                    "documents": [
                        {"prime": d["prime"], "month": d["month"], "allocations": d["count"]}
                        for d in documents
                    ],
                }
            )
        )


if __name__ == "__main__":
    main()
