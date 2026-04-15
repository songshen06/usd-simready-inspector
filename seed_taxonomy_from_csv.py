#!/usr/bin/env python3
"""Seed review-friendly taxonomy groupings from existing CSV outputs."""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Size bucket thresholds are intentionally simple and easy to tune later.
SIZE_BUCKET_THRESHOLDS = {
    "tiny": 0.05,
    "small": 0.5,
    "medium": 3.0,
    "large": 15.0,
}

PROFILE_ORDER = [
    "decorative_prop",
    "rigid_prop",
    "container_prop",
    "static_furniture",
    "storage_furniture",
    "traffic_marker",
    "vehicle",
    "structural_element",
    "unknown",
]


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


def _safe_bool(value: Any) -> Optional[bool]:
    if value in ("", None):
        return None
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _split_semicolon(value: Any) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in str(value).split(";") if item.strip()]


def _first_compact_candidate(value: str) -> str:
    if not value:
        return ""
    first = _split_semicolon(value)[0] if _split_semicolon(value) else ""
    if "(" in first:
        return first.split("(", 1)[0].strip()
    return first.strip()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def load_inputs(
    asset_summary_path: str,
    candidate_review_path: str,
    component_map_path: Optional[str] = None,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[Dict[str, str]]]:
    """Load the three pipeline CSVs; component_map is optional."""
    asset_summary_rows = _read_csv(asset_summary_path)
    candidate_review_rows = _read_csv(candidate_review_path)
    component_map_rows = _read_csv(component_map_path) if component_map_path else []
    return asset_summary_rows, candidate_review_rows, component_map_rows


def normalize_semantic_bucket(row: Dict[str, str]) -> str:
    """Collapse diverse asset labels into a small set of review-friendly buckets."""
    candidates = [
        row.get("top_semantic_candidate", ""),
        _first_compact_candidate(row.get("semantic_candidates", "")),
        row.get("asset_id", ""),
        row.get("display_name", ""),
    ]
    normalized = " ".join(_normalize_text(item) for item in candidates if item)

    keyword_groups = {
        "decor": ["vase", "bowl", "sculpture", "decor", "statue", "ornament", "skull"],
        "seating": ["chair", "stool", "loveseat", "sofa", "seat", "bench", "ottoman"],
        "table_surface": ["table", "desk", "coffeetable", "endtable", "sofatable"],
        "storage_furniture": ["cabinet", "shelf", "storage", "locker", "file"],
        "container": ["box", "crate", "bin", "container", "basket"],
        "marker": ["cone", "trafficcone", "marker", "tape"],
        "vehicle": ["boat", "forklift", "cart", "trolley", "vehicle"],
        "structure": ["rail", "frame", "support", "shelfrail"],
    }
    for bucket, keywords in keyword_groups.items():
        if any(keyword in normalized for keyword in keywords):
            return bucket
    return "generic"


def derive_physics_bucket(row: Dict[str, str]) -> str:
    """Reduce authored physics into a compact review bucket."""
    has_rigid_body = _safe_bool(row.get("has_rigid_body"))
    has_collision = _safe_bool(row.get("has_collision"))
    has_mass = _safe_bool(row.get("has_mass"))

    if has_rigid_body and has_collision and has_mass:
        return "dyn_full"
    if any(value is True for value in (has_rigid_body, has_collision, has_mass)):
        if has_collision and not has_rigid_body and not has_mass:
            return "static_collision"
        return "dyn_partial"
    if all(value is False for value in (has_rigid_body, has_collision, has_mass)):
        return "static_visual"
    return "unknown"


def derive_size_bucket(row: Dict[str, str]) -> str:
    """Bucket approximate scale using bbox volume or bbox dimensions."""
    volume = _safe_float(row.get("bbox_volume_estimate"))
    if volume is None:
        x = _safe_float(row.get("world_bbox_x")) or 0.0
        y = _safe_float(row.get("world_bbox_y")) or 0.0
        z = _safe_float(row.get("world_bbox_z")) or 0.0
        if x > 0 and y > 0 and z > 0:
            volume = x * y * z
    if volume is None:
        return "unknown"
    if volume < SIZE_BUCKET_THRESHOLDS["tiny"]:
        return "tiny"
    if volume < SIZE_BUCKET_THRESHOLDS["small"]:
        return "small"
    if volume < SIZE_BUCKET_THRESHOLDS["medium"]:
        return "medium"
    if volume < SIZE_BUCKET_THRESHOLDS["large"]:
        return "large"
    return "xlarge"


def build_auto_group_key(
    semantic_bucket: str,
    material_bucket: str,
    physics_bucket: str,
    size_bucket: str,
    structure_bucket: str,
) -> str:
    return "__".join([semantic_bucket, material_bucket, physics_bucket, size_bucket, structure_bucket])


def suggest_profile(row: Dict[str, str]) -> Tuple[str, float, str]:
    """Produce a first-pass profile suggestion from merged CSV facts."""
    semantic_bucket = row.get("semantic_bucket", "generic") or "generic"
    material_bucket = row.get("material_bucket", "unknown") or "unknown"
    physics_bucket = row.get("physics_bucket", "unknown") or "unknown"
    size_bucket = row.get("size_bucket", "unknown") or "unknown"
    basis = [
        f"semantic_bucket={semantic_bucket}",
        f"material_bucket={material_bucket}",
        f"physics_bucket={physics_bucket}",
        f"size_bucket={size_bucket}",
    ]

    if semantic_bucket == "decor" and physics_bucket in {"static_visual", "unknown"} and size_bucket in {"tiny", "small", "medium"}:
        return "decorative_prop", 0.9, "; ".join(basis)
    if semantic_bucket in {"seating", "table_surface"} and size_bucket in {"small", "medium", "large"} and physics_bucket != "dyn_full":
        return "static_furniture", 0.84, "; ".join(basis)
    if semantic_bucket == "storage_furniture":
        return "storage_furniture", 0.9, "; ".join(basis)
    if semantic_bucket == "container":
        return "container_prop", 0.88, "; ".join(basis)
    if semantic_bucket == "marker":
        return "traffic_marker", 0.92, "; ".join(basis)
    if semantic_bucket == "vehicle" and physics_bucket in {"dyn_full", "dyn_partial"} and size_bucket in {"medium", "large", "xlarge"}:
        return "vehicle", 0.9, "; ".join(basis)
    if semantic_bucket == "structure":
        return "structural_element", 0.84, "; ".join(basis)
    if physics_bucket in {"dyn_full", "dyn_partial"} and semantic_bucket not in {"vehicle", "seating", "table_surface", "storage_furniture", "marker", "container"}:
        return "rigid_prop", 0.72, "; ".join(basis)
    return "unknown", 0.3, "; ".join(basis)


def enrich_candidate_review(
    asset_summary_rows: List[Dict[str, str]],
    candidate_review_rows: List[Dict[str, str]],
) -> List[Dict[str, Any]]:
    """Merge stable asset facts into candidate review and add grouping fields."""
    asset_by_file = {row.get("file", ""): row for row in asset_summary_rows}
    enriched_rows: List[Dict[str, Any]] = []

    for review_row in candidate_review_rows:
        merged = dict(review_row)
        asset_row = asset_by_file.get(review_row.get("file", ""), {})

        merged["asset_name_source"] = asset_row.get("asset_name_source", merged.get("asset_name_source", ""))
        merged["display_name"] = asset_row.get("display_name", "")
        merged["top_semantic_candidate"] = asset_row.get("top_semantic_candidate", "")
        merged["top_material_family_candidate"] = asset_row.get("top_material_family_candidate", "")
        merged["top_physics_profile_candidate"] = asset_row.get("top_physics_profile_candidate", "")
        merged["world_bbox_x"] = asset_row.get("world_bbox_x", "")
        merged["world_bbox_y"] = asset_row.get("world_bbox_y", "")
        merged["world_bbox_z"] = asset_row.get("world_bbox_z", "")
        merged["bbox_volume_estimate"] = asset_row.get("bbox_volume_estimate", "")
        merged["has_rigid_body"] = asset_row.get("has_rigid_body", "")
        merged["has_collision"] = asset_row.get("has_collision", "")
        merged["has_mass"] = asset_row.get("has_mass", "")
        merged["simready_overall"] = asset_row.get("simready_overall", merged.get("simready_overall", ""))
        merged["structure_pattern"] = asset_row.get("structure_pattern", merged.get("structure_pattern", ""))
        merged["review_flags"] = asset_row.get("review_flags", merged.get("review_flags", ""))
        merged["auxiliary_mesh_present"] = asset_row.get("auxiliary_mesh_count", merged.get("auxiliary_mesh_present", ""))

        semantic_bucket = normalize_semantic_bucket(merged)
        material_bucket = asset_row.get("top_material_family_candidate", "") or "unknown"
        physics_bucket = derive_physics_bucket(asset_row)
        size_bucket = derive_size_bucket(asset_row)
        structure_bucket = asset_row.get("structure_pattern", "") or "unknown"
        auto_group_key = build_auto_group_key(
            semantic_bucket,
            material_bucket,
            physics_bucket,
            size_bucket,
            structure_bucket,
        )
        suggestion, confidence, basis = suggest_profile(
            {
                "semantic_bucket": semantic_bucket,
                "material_bucket": material_bucket,
                "physics_bucket": physics_bucket,
                "size_bucket": size_bucket,
            }
        )

        merged["semantic_bucket"] = semantic_bucket
        merged["material_bucket"] = material_bucket
        merged["physics_bucket"] = physics_bucket
        merged["size_bucket"] = size_bucket
        merged["structure_bucket"] = structure_bucket
        merged["auto_group_key"] = auto_group_key
        merged["auto_profile_suggestion"] = suggestion
        merged["auto_profile_confidence"] = f"{confidence:.2f}"
        merged["auto_profile_basis"] = basis

        for column in (
            "human_semantic_label",
            "human_material_family",
            "human_profile",
            "human_notes",
            "approved",
            "human_structure_pattern",
            "human_collider_choice",
            "human_variant_keep",
            "review_status",
        ):
            merged.setdefault(column, "")

        enriched_rows.append(merged)

    return enriched_rows


def build_taxonomy_seed(enriched_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Aggregate enriched rows by auto_group_key for group-level review."""
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in enriched_rows:
        grouped[row.get("auto_group_key", "unknown_group")].append(row)

    output_rows: List[Dict[str, Any]] = []
    for group_key, rows in sorted(grouped.items()):
        suggestion_counter = Counter(row.get("auto_profile_suggestion", "unknown") for row in rows)
        top_suggestion = suggestion_counter.most_common(1)[0][0] if suggestion_counter else "unknown"
        confidences = [_safe_float(row.get("auto_profile_confidence")) for row in rows]
        valid_confidences = [value for value in confidences if value is not None]
        avg_confidence = sum(valid_confidences) / len(valid_confidences) if valid_confidences else 0.0

        output_rows.append(
            {
                "auto_group_key": group_key,
                "semantic_bucket": rows[0].get("semantic_bucket", ""),
                "material_bucket": rows[0].get("material_bucket", ""),
                "physics_bucket": rows[0].get("physics_bucket", ""),
                "size_bucket": rows[0].get("size_bucket", ""),
                "structure_bucket": rows[0].get("structure_bucket", ""),
                "asset_count": len(rows),
                "sample_asset_ids": ";".join(row.get("asset_id", "") for row in rows[:5] if row.get("asset_id")),
                "sample_files": ";".join(row.get("file", "") for row in rows[:3] if row.get("file")),
                "top_auto_profile_suggestion": top_suggestion,
                "avg_auto_profile_confidence": f"{avg_confidence:.2f}",
                "unique_semantic_candidates": ";".join(sorted({row.get("top_semantic_candidate", "") for row in rows if row.get("top_semantic_candidate")})),
                "unique_material_candidates": ";".join(sorted({row.get("top_material_family_candidate", "") for row in rows if row.get("top_material_family_candidate")})),
                "unique_structure_patterns": ";".join(sorted({row.get("structure_pattern", "") for row in rows if row.get("structure_pattern")})),
                "unique_review_flags": ";".join(sorted({flag for row in rows for flag in _split_semicolon(row.get("review_flags", ""))})),
            }
        )
    return output_rows


def build_group_samples(enriched_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create a group-expanded sample table for Excel-based browsing."""
    output_rows: List[Dict[str, Any]] = []
    for row in sorted(enriched_rows, key=lambda item: (item.get("auto_group_key", ""), item.get("asset_id", ""))):
        output_rows.append(
            {
                "auto_group_key": row.get("auto_group_key", ""),
                "asset_id": row.get("asset_id", ""),
                "file": row.get("file", ""),
                "auto_profile_suggestion": row.get("auto_profile_suggestion", ""),
                "auto_profile_confidence": row.get("auto_profile_confidence", ""),
                "top_semantic_candidate": row.get("top_semantic_candidate", ""),
                "top_material_family_candidate": row.get("top_material_family_candidate", ""),
                "structure_pattern": row.get("structure_pattern", ""),
                "simready_overall": row.get("simready_overall", ""),
                "review_flags": row.get("review_flags", ""),
            }
        )
    return output_rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Seed taxonomy grouping and profile suggestions from existing CSV exports.")
    parser.add_argument("--asset-summary", required=True, help="Path to asset_summary.csv")
    parser.add_argument("--candidate-review", required=True, help="Path to candidate_review.csv")
    parser.add_argument("--component-map", help="Optional path to component_map.csv")
    parser.add_argument("--output-dir", required=True, help="Directory to write enriched CSV outputs into")
    args = parser.parse_args(argv)

    asset_summary_rows, candidate_review_rows, _ = load_inputs(
        args.asset_summary,
        args.candidate_review,
        args.component_map,
    )
    enriched_rows = enrich_candidate_review(asset_summary_rows, candidate_review_rows)
    taxonomy_seed_rows = build_taxonomy_seed(enriched_rows)
    group_sample_rows = build_group_samples(enriched_rows)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "candidate_review_enriched.csv", enriched_rows)
    _write_csv(output_dir / "taxonomy_seed.csv", taxonomy_seed_rows)
    _write_csv(output_dir / "group_samples.csv", group_sample_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
