#!/usr/bin/env python3
"""Build a static furniture reference JSON from USD assets."""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from static_furniture import (
    build_reference_library,
    build_static_furniture_asset_reference,
    find_usd_files,
    inspect_asset,
    save_json,
)


def _default_output_path(input_path: str) -> str:
    base = os.path.basename(os.path.abspath(input_path.rstrip(os.sep))) or "assets"
    return os.path.join(os.getcwd(), f"{base}.static_furniture_reference.json")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Extract static furniture reference features from USD assets.")
    parser.add_argument("input_path", help="USD file or directory containing USD assets")
    parser.add_argument("--output", help="Path to write the reference JSON")
    parser.add_argument("--recursive", action="store_true", help="Recursively scan directories for USD files")
    parser.add_argument("--max-prims", type=int, default=0, help="Limit prim traversal per asset; 0 means unlimited")
    args = parser.parse_args(argv)

    usd_paths = find_usd_files(args.input_path, recursive=args.recursive)
    if not usd_paths:
        print("No USD files found.", file=sys.stderr)
        return 1

    assets = []
    for usd_path in usd_paths:
        inspected = inspect_asset(usd_path, max_prims=max(0, args.max_prims))
        assets.append(build_static_furniture_asset_reference(inspected["report"], inspected["knowledge"]))

    library = build_reference_library(assets, source_root=os.path.abspath(args.input_path))
    output_path = args.output or _default_output_path(args.input_path)
    save_json(output_path, library, pretty=True)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

