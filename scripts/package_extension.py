#!/usr/bin/env python3
"""scripts/package_extension.py — build a distributable zip of an extension
for re-hosting (CI artifact, manual GitHub release upload, etc.) without a
running Odysseus instance. Shares src.extension_host.build_package_zip with
routes/extension_routes.py's GET /api/extensions/{id}/package.zip so the two
never drift.

Usage: python scripts/package_extension.py <ext_id> [output_path]
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

from src import extension_host


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ext_id", help="Extension id, e.g. 'rss' or 'ithaca'")
    parser.add_argument("output", nargs="?", help="Output zip path (default: <ext_id>.zip in cwd)")
    args = parser.parse_args()

    try:
        data = extension_host.build_package_zip(args.ext_id)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.output) if args.output else Path(f"{args.ext_id}.zip")
    out_path.write_bytes(data)
    print(f"Wrote {out_path} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
