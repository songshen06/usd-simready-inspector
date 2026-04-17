#!/usr/bin/env python3
"""Compare recommendation outputs between two reference libraries."""

from __future__ import annotations

import argparse
import csv
import os
from typing import Any, Dict, List, Optional

from static_furniture import inspect_asset, load_json, recommend_from_reference, save_json


def _safe_get(data: Dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _flatten_comparison(asset_path: str, old_reco: Dict[str, Any], new_reco: Dict[str, Any]) -> Dict[str, Any]:
    old_asset = old_reco.get("asset", {}) or {}
    new_asset = new_reco.get("asset", {}) or {}
    old_collider = _safe_get(old_reco, "recommendation", "recommended_collider") or {}
    new_collider = _safe_get(new_reco, "recommendation", "recommended_collider") or {}
    old_size = _safe_get(old_reco, "recommendation", "size_recommendation") or {}
    new_size = _safe_get(new_reco, "recommendation", "size_recommendation") or {}

    return {
        "asset_path": asset_path,
        "asset_id_old": old_asset.get("asset_id"),
        "asset_id_new": new_asset.get("asset_id"),
        "furniture_class_old": old_asset.get("furniture_class"),
        "furniture_class_new": new_asset.get("furniture_class"),
        "is_furniture_old": old_asset.get("is_furniture"),
        "is_furniture_new": new_asset.get("is_furniture"),
        "reference_group_old": _safe_get(old_reco, "recommendation", "reference_group_key"),
        "reference_group_new": _safe_get(new_reco, "recommendation", "reference_group_key"),
        "reference_group_asset_count_old": _safe_get(old_reco, "recommendation", "reference_group_asset_count"),
        "reference_group_asset_count_new": _safe_get(new_reco, "recommendation", "reference_group_asset_count"),
        "approximation_old": old_collider.get("approximation"),
        "approximation_new": new_collider.get("approximation"),
        "scope_old": old_collider.get("scope"),
        "scope_new": new_collider.get("scope"),
        "confidence_old": old_collider.get("confidence"),
        "confidence_new": new_collider.get("confidence"),
        "size_status_old": old_size.get("status"),
        "size_status_new": new_size.get("status"),
        "size_warning_old": old_size.get("size_warning"),
        "size_warning_new": new_size.get("size_warning"),
        "uniform_scale_old": old_size.get("suggested_uniform_scale"),
        "uniform_scale_new": new_size.get("suggested_uniform_scale"),
        "changed_furniture_class": old_asset.get("furniture_class") != new_asset.get("furniture_class"),
        "changed_is_furniture": bool(old_asset.get("is_furniture")) != bool(new_asset.get("is_furniture")),
        "changed_approximation": old_collider.get("approximation") != new_collider.get("approximation"),
        "changed_scope": old_collider.get("scope") != new_collider.get("scope"),
        "changed_reference_group": _safe_get(old_reco, "recommendation", "reference_group_key")
        != _safe_get(new_reco, "recommendation", "reference_group_key"),
        "old_basis": " | ".join(old_collider.get("basis", []) or []),
        "new_basis": " | ".join(new_collider.get("basis", []) or []),
        "old_review_flags": ";".join(old_reco.get("review_flags", []) or []),
        "new_review_flags": ";".join(new_reco.get("review_flags", []) or []),
        "new_semantic_qcodes": ";".join(((new_asset.get("semantic_metadata", {}) or {}).get("qcodes") or [])),
        "new_semantic_classes": ";".join(((new_asset.get("semantic_metadata", {}) or {}).get("classes") or [])),
        "new_semantic_hierarchies": ";".join(((new_asset.get("semantic_metadata", {}) or {}).get("hierarchies") or [])),
    }


def _write_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    fieldnames = [
        "asset_path",
        "asset_id_old",
        "asset_id_new",
        "furniture_class_old",
        "furniture_class_new",
        "is_furniture_old",
        "is_furniture_new",
        "reference_group_old",
        "reference_group_new",
        "reference_group_asset_count_old",
        "reference_group_asset_count_new",
        "approximation_old",
        "approximation_new",
        "scope_old",
        "scope_new",
        "confidence_old",
        "confidence_new",
        "size_status_old",
        "size_status_new",
        "size_warning_old",
        "size_warning_new",
        "uniform_scale_old",
        "uniform_scale_new",
        "changed_furniture_class",
        "changed_is_furniture",
        "changed_approximation",
        "changed_scope",
        "changed_reference_group",
        "old_basis",
        "new_basis",
        "old_review_flags",
        "new_review_flags",
        "new_semantic_qcodes",
        "new_semantic_classes",
        "new_semantic_hierarchies",
    ]
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Compare recommendation outputs between two reference JSON files.")
    parser.add_argument("old_reference_json", help="Old reference JSON path")
    parser.add_argument("new_reference_json", help="New reference JSON path")
    parser.add_argument("asset_paths", nargs="+", help="USD asset paths to compare")
    parser.add_argument("--output-json", required=True, help="Path to write comparison JSON")
    parser.add_argument("--output-csv", required=True, help="Path to write comparison CSV")
    args = parser.parse_args(argv)

    old_reference = load_json(args.old_reference_json)
    new_reference = load_json(args.new_reference_json)

    comparisons = []
    csv_rows = []
    for asset_path in args.asset_paths:
        inspected = inspect_asset(asset_path)
        old_reco = recommend_from_reference(old_reference, inspected["report"], inspected["knowledge"])
        new_reco = recommend_from_reference(new_reference, inspected["report"], inspected["knowledge"])
        comparisons.append(
            {
                "asset_path": os.path.abspath(asset_path),
                "old_recommendation": old_reco,
                "new_recommendation": new_reco,
            }
        )
        csv_rows.append(_flatten_comparison(os.path.abspath(asset_path), old_reco, new_reco))

    save_json(
        args.output_json,
        {
            "old_reference_json": os.path.abspath(args.old_reference_json),
            "new_reference_json": os.path.abspath(args.new_reference_json),
            "asset_count": len(comparisons),
            "comparisons": comparisons,
        },
        pretty=True,
    )
    _write_csv(args.output_csv, csv_rows)
    print(args.output_json)
    print(args.output_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
