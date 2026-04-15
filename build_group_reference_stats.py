#!/usr/bin/env python3
"""Build group-level reference stats from enriched review CSVs."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional


def _read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in ("", None):
            return None
        return float(value)
    except Exception:
        return None


def _split_semicolon(value: Any) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split(";") if item.strip()]


def _normalize_distribution(counter: Counter, total: int) -> Dict[str, float]:
    if total <= 0:
        return {}
    return {key: round(count / total, 4) for key, count in sorted(counter.items()) if key}


def _stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"mean": None, "min": None, "max": None, "count": 0}
    return {
        "mean": round(sum(values) / len(values), 6),
        "min": min(values),
        "max": max(values),
        "count": len(values),
    }


def load_inputs(candidate_review_enriched_path: str, component_map_path: Optional[str]) -> tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    candidate_rows = _read_csv(candidate_review_enriched_path)
    component_rows = _read_csv(component_map_path) if component_map_path else []
    return candidate_rows, component_rows


def build_group_reference_stats(
    candidate_rows: List[Dict[str, str]],
    component_rows: List[Dict[str, str]],
) -> List[Dict[str, Any]]:
    """Aggregate per-group collider, material, mesh complexity, and authored values."""
    group_assets: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    file_to_group: Dict[str, str] = {}
    asset_to_group: Dict[str, str] = {}
    for row in candidate_rows:
        group_key = row.get("auto_group_key", "") or "unknown_group"
        group_assets[group_key].append(row)
        if row.get("file"):
            file_to_group[row["file"]] = group_key
        if row.get("asset_id"):
            asset_to_group[row["asset_id"]] = group_key

    component_by_group: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in component_rows:
        group_key = file_to_group.get(row.get("file", "")) or asset_to_group.get(row.get("asset_id", ""))
        if group_key:
            component_by_group[group_key].append(row)

    output: List[Dict[str, Any]] = []
    for group_key, assets in sorted(group_assets.items()):
        components = component_by_group.get(group_key, [])
        collider_counter: Counter = Counter()
        material_counter: Counter = Counter()
        mass_values: List[float] = []
        density_values: List[float] = []
        asset_mesh_counts: List[int] = []
        component_points: List[float] = []

        for asset in assets:
            # Fallback collider signal from asset-level suggestion when authored approx is absent.
            collider_suggestion = asset.get("human_collider_choice") or asset.get("collider_recommendation")
            if collider_suggestion:
                collider_counter[str(collider_suggestion)] += 1

            material_bucket = asset.get("top_material_family_candidate") or asset.get("material_bucket")
            if material_bucket and material_bucket != "unknown":
                material_counter[str(material_bucket)] += 1

        asset_mesh_counter: Dict[str, int] = defaultdict(int)
        asset_points_counter: Dict[str, float] = defaultdict(float)

        for component in components:
            asset_id = component.get("asset_id", "")
            approximation = component.get("collider_authored_approximation") or component.get("collider_approximation")
            if approximation:
                collider_counter[str(approximation)] += 1

            for material_name in _split_semicolon(component.get("physics_materials", "")):
                lowered = material_name.lower()
                if "wood" in lowered:
                    material_counter["wood"] += 1
                elif any(token in lowered for token in ("metal", "steel", "iron", "brass", "aluminum", "aluminium", "chrome")):
                    material_counter["metal"] += 1
                elif any(token in lowered for token in ("plastic", "pvc", "acrylic", "poly")):
                    material_counter["plastic"] += 1

            points_count = _safe_float(component.get("points_count"))
            if points_count is not None:
                component_points.append(points_count)
                if asset_id:
                    asset_points_counter[asset_id] += points_count
            if asset_id:
                asset_mesh_counter[asset_id] += 1

            # Future-proof authored parameter parsing; current pipeline may not populate these yet.
            param_summary = component.get("physics_material_param_summary", "")
            for item in _split_semicolon(param_summary):
                for part in item.split(","):
                    key, _, raw = part.partition("=")
                    value = _safe_float(raw)
                    if value is None:
                        continue
                    if key == "density":
                        density_values.append(value)

        asset_mesh_counts.extend(asset_mesh_counter.values())

        output.append(
            {
                "group": group_key,
                "asset_count": len(assets),
                "collider_distribution": _normalize_distribution(collider_counter, sum(collider_counter.values())),
                "mass_stats": _stats(mass_values),
                "density_stats": _stats(density_values),
                "physics_material_distribution": _normalize_distribution(material_counter, sum(material_counter.values())),
                "mesh_complexity": {
                    "avg_mesh_count": round(sum(asset_mesh_counts) / len(asset_mesh_counts), 6) if asset_mesh_counts else None,
                    "avg_points_count": round(sum(component_points) / len(component_points), 6) if component_points else None,
                },
            }
        )
    return output


def flatten_group_reference_stats(group_stats: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten nested group stats into a CSV-friendly representation."""
    rows: List[Dict[str, Any]] = []
    for item in group_stats:
        rows.append(
            {
                "group": item.get("group", ""),
                "asset_count": item.get("asset_count", 0),
                "collider_distribution": json.dumps(item.get("collider_distribution", {}), ensure_ascii=False),
                "mass_mean": item.get("mass_stats", {}).get("mean"),
                "mass_min": item.get("mass_stats", {}).get("min"),
                "mass_max": item.get("mass_stats", {}).get("max"),
                "mass_count": item.get("mass_stats", {}).get("count"),
                "density_mean": item.get("density_stats", {}).get("mean"),
                "density_min": item.get("density_stats", {}).get("min"),
                "density_max": item.get("density_stats", {}).get("max"),
                "density_count": item.get("density_stats", {}).get("count"),
                "physics_material_distribution": json.dumps(item.get("physics_material_distribution", {}), ensure_ascii=False),
                "avg_mesh_count": item.get("mesh_complexity", {}).get("avg_mesh_count"),
                "avg_points_count": item.get("mesh_complexity", {}).get("avg_points_count"),
            }
        )
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build group-level reference stats from enriched review CSVs.")
    parser.add_argument("--candidate-review-enriched", required=True, help="Path to candidate_review_enriched.csv")
    parser.add_argument("--component-map", help="Optional path to component_map.csv")
    parser.add_argument("--output-dir", required=True, help="Directory to write group reference stats into")
    args = parser.parse_args(argv)

    candidate_rows, component_rows = load_inputs(args.candidate_review_enriched, args.component_map)
    group_stats = build_group_reference_stats(candidate_rows, component_rows)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with (output_dir / "group_reference_stats.json").open("w", encoding="utf-8") as handle:
        json.dump(group_stats, handle, ensure_ascii=False, indent=2)
    _write_csv(output_dir / "group_reference_stats.csv", flatten_group_reference_stats(group_stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
