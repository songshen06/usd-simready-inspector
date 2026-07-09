#!/usr/bin/env python3
"""Build a compact SimReady asset feature database from USD assets."""

from __future__ import annotations

import argparse
import csv
import json
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from knowledge_candidate import build_knowledge_candidate
from usd_inspector import build_detailed_report, open_stage


USD_SUFFIXES = (".usd", ".usda", ".usdc", ".usdz")
DEFAULT_INPUT_ROOT = "/home/horde/Downloads/Assets/simready_content"
DEFAULT_INCLUDE_ROOT = "common_assets"


ASSET_FEATURE_FIELDS = [
    "asset_id",
    "asset_variant_role",
    "file",
    "asset_group_path",
    "asset_family",
    "default_prim",
    "kind",
    "up_axis",
    "meters_per_unit",
    "mesh_count",
    "primary_mesh_count",
    "auxiliary_mesh_count",
    "points_count_total",
    "face_count_total",
    "bbox_x",
    "bbox_y",
    "bbox_z",
    "bbox_volume",
    "max_dimension",
    "min_dimension",
    "aspect_ratio_hint",
    "is_multi_mesh",
    "shape_is_box_like",
    "shape_is_flat",
    "shape_is_tall",
    "shape_is_elongated",
    "bbox_may_include_auxiliary_mesh",
    "render_material_count",
    "pbr_material_count",
    "top_pbr_diffuse_color",
    "top_pbr_roughness",
    "top_pbr_metallic",
    "top_pbr_opacity",
    "has_texture",
    "shader_ids",
    "physics_material_count",
    "physics_binding_count",
    "top_static_friction",
    "top_dynamic_friction",
    "top_restitution",
    "top_physics_density",
    "has_collision",
    "collision_prim_count",
    "collision_approximations",
    "static_collider_count",
    "dynamic_collider_count",
    "has_rigid_body",
    "rigid_body_count",
    "has_mass",
    "mass_value_count",
    "density_value_count",
    "center_of_mass_value_count",
    "has_physics_material",
    "semantic_classes",
    "semantic_qcodes",
    "semantic_label_source",
    "simready_geometry",
    "simready_render_material",
    "simready_physics_schema",
    "simready_physics_values",
    "simready_physics_material",
    "simready_semantic_label",
    "simready_overall",
    "review_flags",
    "source_report_file",
    "source_knowledge_candidate_file",
]


def _safe_get(mapping: Dict[str, Any], *keys: str, default: Any = "") -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _join_list(values: Any) -> str:
    if values is None:
        return ""
    if isinstance(values, list):
        return ";".join(str(item) for item in values if item not in (None, ""))
    return str(values)


def _json_compact(value: Any) -> str:
    if value in (None, ""):
        return ""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _first_number(values: Any) -> Any:
    if not isinstance(values, list):
        return ""
    for value in values:
        if value not in (None, ""):
            return value
    return ""


def _first_physics_material_param(params: List[Dict[str, Any]], key: str) -> Any:
    for item in params or []:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return ""


def _first_pbr(material_features: Dict[str, Any]) -> Dict[str, Any]:
    for item in material_features.get("visual_materials", []) or []:
        pbr = item.get("pbr") or item.get("preview_surface") or {}
        if pbr:
            return pbr
    return {}


def _has_texture(material_features: Dict[str, Any]) -> bool:
    for item in material_features.get("visual_materials", []) or []:
        if item.get("has_texture") or item.get("texture_paths"):
            return True
        pbr = item.get("pbr", {}) or {}
        if pbr.get("base_color_texture") or pbr.get("metallic_roughness_texture"):
            return True
    return False


def _shader_ids(material_features: Dict[str, Any]) -> str:
    values = set()
    for item in material_features.get("visual_materials", []) or []:
        for shader_id in item.get("shader_ids", []) or []:
            if shader_id:
                values.add(str(shader_id))
    return ";".join(sorted(values))


def _relative_output_path(root: Path, asset_path: Path, suffix: str) -> Path:
    relative = asset_path.relative_to(root)
    return relative.with_suffix(suffix)


def _iter_usd_assets(input_root: Path, include_roots: Iterable[str], limit: int = 0) -> List[Path]:
    assets: List[Path] = []
    for include_root in include_roots:
        base = input_root / include_root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix.lower() in USD_SUFFIXES:
                assets.append(path)
                if limit > 0 and len(assets) >= limit:
                    return assets
    return assets


def _asset_group(root: Path, asset_path: Path) -> Tuple[str, str]:
    relative_parent = asset_path.relative_to(root).parent
    family = relative_parent.name
    return relative_parent.as_posix(), family


def _flatten_asset_feature(
    input_root: Path,
    asset_path: Path,
    report_path: Path,
    knowledge_path: Path,
    report: Dict[str, Any],
    knowledge: Dict[str, Any],
) -> Dict[str, Any]:
    geometry = knowledge.get("geometry_features", {}) or {}
    physics = knowledge.get("physics_values", {}) or {}
    material_features = knowledge.get("material_features", {}) or {}
    semantic = knowledge.get("semantic_metadata", {}) or {}
    completeness = knowledge.get("simready_completeness", {}) or {}
    stage = report.get("stage", {}) or {}
    metadata = report.get("metadata", {}) or {}
    pbr = _first_pbr(material_features)
    physics_params = physics.get("physics_material_params", []) or []
    world_bbox = geometry.get("world_bbox_size") or []
    shape_hints = geometry.get("shape_hints", {}) or {}
    asset_group_path, asset_family = _asset_group(input_root, asset_path)

    return {
        "asset_id": knowledge.get("asset_id", ""),
        "asset_variant_role": _safe_get(knowledge, "asset_variant_role", "value", default=""),
        "file": str(asset_path),
        "asset_group_path": asset_group_path,
        "asset_family": asset_family,
        "default_prim": stage.get("default_prim", ""),
        "kind": _join_list(metadata.get("kinds", []) or []),
        "up_axis": stage.get("up_axis", ""),
        "meters_per_unit": stage.get("meters_per_unit", ""),
        "mesh_count": geometry.get("mesh_count", ""),
        "primary_mesh_count": geometry.get("primary_mesh_count", ""),
        "auxiliary_mesh_count": geometry.get("auxiliary_mesh_count", ""),
        "points_count_total": geometry.get("points_count_total", ""),
        "face_count_total": geometry.get("face_count_total", ""),
        "bbox_x": world_bbox[0] if isinstance(world_bbox, list) and len(world_bbox) == 3 else "",
        "bbox_y": world_bbox[1] if isinstance(world_bbox, list) and len(world_bbox) == 3 else "",
        "bbox_z": world_bbox[2] if isinstance(world_bbox, list) and len(world_bbox) == 3 else "",
        "bbox_volume": geometry.get("volume_estimate_bbox", ""),
        "max_dimension": geometry.get("max_dimension", ""),
        "min_dimension": geometry.get("min_dimension", ""),
        "aspect_ratio_hint": geometry.get("aspect_ratio_hint", ""),
        "is_multi_mesh": geometry.get("is_multi_mesh", ""),
        "shape_is_box_like": shape_hints.get("is_box_like", ""),
        "shape_is_flat": shape_hints.get("is_flat", ""),
        "shape_is_tall": shape_hints.get("is_tall", ""),
        "shape_is_elongated": shape_hints.get("is_elongated", ""),
        "bbox_may_include_auxiliary_mesh": geometry.get("bbox_may_include_auxiliary_mesh", ""),
        "render_material_count": material_features.get("render_material_count", ""),
        "pbr_material_count": sum(1 for item in material_features.get("visual_materials", []) or [] if item.get("pbr")),
        "top_pbr_diffuse_color": _json_compact(pbr.get("diffuse_color")),
        "top_pbr_roughness": pbr.get("roughness", ""),
        "top_pbr_metallic": pbr.get("metallic", ""),
        "top_pbr_opacity": pbr.get("opacity", ""),
        "has_texture": _has_texture(material_features),
        "shader_ids": _shader_ids(material_features),
        "physics_material_count": material_features.get("physics_material_count", ""),
        "physics_binding_count": material_features.get("physics_binding_count", ""),
        "top_static_friction": _first_physics_material_param(physics_params, "static_friction"),
        "top_dynamic_friction": _first_physics_material_param(physics_params, "dynamic_friction"),
        "top_restitution": _first_physics_material_param(physics_params, "restitution"),
        "top_physics_density": _first_physics_material_param(physics_params, "density"),
        "has_collision": physics.get("has_collision", ""),
        "collision_prim_count": len(physics.get("collision_paths", []) or []),
        "collision_approximations": _join_list(sorted(set(physics.get("collision_approximations", []) or []))),
        "static_collider_count": physics.get("static_collider_count", ""),
        "dynamic_collider_count": physics.get("dynamic_collider_count", ""),
        "has_rigid_body": physics.get("has_rigid_body", ""),
        "rigid_body_count": len(physics.get("rigid_body_paths", []) or []),
        "has_mass": physics.get("has_mass", ""),
        "mass_value_count": len(physics.get("mass_values", []) or []),
        "density_value_count": len(physics.get("density_values", []) or []),
        "center_of_mass_value_count": len(physics.get("center_of_mass_values", []) or []),
        "has_physics_material": physics.get("has_physics_material", ""),
        "semantic_classes": _join_list(semantic.get("classes", []) or []),
        "semantic_qcodes": _join_list(semantic.get("qcodes", []) or []),
        "semantic_label_source": "usd_semantic_metadata" if semantic.get("entries") else "none",
        "simready_geometry": completeness.get("geometry", ""),
        "simready_render_material": completeness.get("render_material", ""),
        "simready_physics_schema": completeness.get("physics_schema", ""),
        "simready_physics_values": completeness.get("physics_values", ""),
        "simready_physics_material": completeness.get("physics_material", ""),
        "simready_semantic_label": completeness.get("semantic_label", ""),
        "simready_overall": completeness.get("overall", ""),
        "review_flags": _join_list(knowledge.get("review_flags", []) or []),
        "source_report_file": str(report_path),
        "source_knowledge_candidate_file": str(knowledge_path),
    }


def _write_json(path: Path, payload: Dict[str, Any], pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2 if pretty else None)
        handle.write("\n")


def _write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def build_database(args: argparse.Namespace) -> int:
    input_root = Path(args.input_root).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    include_roots = [item.strip().strip("/") for item in args.include_root if item.strip()]
    assets = _iter_usd_assets(input_root, include_roots, limit=max(0, args.limit))

    reports_root = output_dir / "reports"
    knowledge_root = output_dir / "knowledge"
    rows: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    jsonl_path = output_dir / "asset_features.jsonl"
    output_dir.mkdir(parents=True, exist_ok=True)

    with jsonl_path.open("w", encoding="utf-8") as jsonl_handle:
        for index, asset_path in enumerate(assets, start=1):
            report_path = reports_root / _relative_output_path(input_root, asset_path, ".report.json")
            knowledge_path = knowledge_root / _relative_output_path(input_root, asset_path, ".knowledge_candidate.json")
            try:
                stage = open_stage(str(asset_path))
                report = build_detailed_report(stage, str(asset_path), max_prims=max(0, args.max_prims))
                knowledge = build_knowledge_candidate(report)
                _write_json(report_path, report, pretty=args.pretty)
                _write_json(knowledge_path, knowledge, pretty=args.pretty)
                jsonl_handle.write(json.dumps(knowledge, ensure_ascii=False, separators=(",", ":")))
                jsonl_handle.write("\n")
                rows.append(_flatten_asset_feature(input_root, asset_path, report_path, knowledge_path, report, knowledge))
            except Exception as exc:
                failures.append(
                    {
                        "file": str(asset_path),
                        "error_type": exc.__class__.__name__,
                        "error_message": str(exc),
                        "traceback": traceback.format_exc() if args.include_tracebacks else "",
                    }
                )
            if args.progress and (index == len(assets) or index % args.progress == 0):
                print(f"processed {index}/{len(assets)} assets; success={len(rows)} failures={len(failures)}")

    _write_csv(output_dir / "asset_features.csv", rows, ASSET_FEATURE_FIELDS)
    _write_csv(output_dir / "failures.csv", failures, ["file", "error_type", "error_message", "traceback"])

    summary = {
        "input_root": str(input_root),
        "include_roots": include_roots,
        "asset_count": len(assets),
        "success_count": len(rows),
        "failure_count": len(failures),
        "asset_features_csv": str(output_dir / "asset_features.csv"),
        "asset_features_jsonl": str(jsonl_path),
        "failures_csv": str(output_dir / "failures.csv"),
    }
    _write_json(output_dir / "summary.json", summary, pretty=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failures and args.fail_on_error else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build a compact SimReady asset feature database.")
    parser.add_argument("--input-root", default=DEFAULT_INPUT_ROOT, help="Root containing simready_content")
    parser.add_argument("--include-root", action="append", default=None, help="Relative subtree to scan; repeatable")
    parser.add_argument("--output-dir", required=True, help="Directory for reports, knowledge JSON, JSONL, CSV, and failures")
    parser.add_argument("--limit", type=int, default=0, help="Optional max USD files for smoke tests; 0 means all")
    parser.add_argument("--max-prims", type=int, default=0, help="Limit prim traversal per asset; 0 means unlimited")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print per-asset report and knowledge JSON")
    parser.add_argument("--progress", type=int, default=50, help="Print progress every N assets; 0 disables progress")
    parser.add_argument("--include-tracebacks", action="store_true", help="Include Python tracebacks in failures.csv")
    parser.add_argument("--fail-on-error", action="store_true", help="Return nonzero if any asset fails")
    args = parser.parse_args(argv)
    if args.include_root is None:
        args.include_root = [DEFAULT_INCLUDE_ROOT]
    return build_database(args)


if __name__ == "__main__":
    raise SystemExit(main())
