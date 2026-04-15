#!/usr/bin/env python3
"""Recommend static furniture SimReady settings from a reference JSON and a USD asset."""

from __future__ import annotations

import argparse
import os
from typing import List, Optional

from static_furniture import inspect_asset, load_json, recommend_from_reference, save_json


def _default_output_path(input_usd: str) -> str:
    for suffix in (".usdz", ".usdc", ".usda", ".usd"):
        if input_usd.lower().endswith(suffix):
            return input_usd[: -len(suffix)] + ".static_furniture_recommendation.json"
    return input_usd + ".static_furniture_recommendation.json"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Recommend static furniture SimReady settings for a USD asset.")
    parser.add_argument("reference_json", help="Path to static furniture reference JSON")
    parser.add_argument("input_usd", help="Path to the new USD asset")
    parser.add_argument("--output", help="Path to write the recommendation JSON")
    parser.add_argument("--max-prims", type=int, default=0, help="Limit prim traversal for the query asset; 0 means unlimited")
    args = parser.parse_args(argv)

    reference = load_json(args.reference_json)
    inspected = inspect_asset(args.input_usd, max_prims=max(0, args.max_prims))
    recommendation = recommend_from_reference(reference, inspected["report"], inspected["knowledge"])

    output_path = args.output or _default_output_path(os.path.abspath(args.input_usd))
    save_json(output_path, recommendation, pretty=True)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

