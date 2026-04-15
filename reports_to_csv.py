#!/usr/bin/env python3
"""Flatten inspector reports and knowledge candidates into review-friendly CSVs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

_WEAK_DEFAULT_PRIM_NAMES = {"rootnode", "world", "asset", "root"}


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


def _compact_candidates(candidates: List[Dict[str, Any]], label_key: str) -> str:
    items = []
    for item in candidates or []:
        label = item.get(label_key)
        if not label:
            continue
        confidence = item.get("confidence")
        if confidence is None:
            items.append(str(label))
        else:
            try:
                items.append(f"{label}({float(confidence):.2f})")
            except Exception:
                items.append(str(label))
    return "; ".join(items)


def _first_candidate(candidates: List[Dict[str, Any]], label_key: str) -> Tuple[str, str]:
    if not candidates:
        return "", ""
    item = candidates[0]
    label = str(item.get(label_key) or "")
    confidence = item.get("confidence")
    if confidence is None:
        return label, ""
    try:
        return label, f"{float(confidence):.2f}"
    except Exception:
        return label, ""


def _world_bbox_triplet(report: Dict[str, Any], knowledge: Optional[Dict[str, Any]]) -> Tuple[Any, Any, Any]:
    if knowledge:
        size = _safe_get(knowledge, "geometry_features", "world_bbox_size", default=None)
        if isinstance(size, list) and len(size) == 3:
            return size[0], size[1], size[2]
    size = _safe_get(report, "geometry", "bbox", "world", "size", default=None)
    if isinstance(size, list) and len(size) == 3:
        return size[0], size[1], size[2]
    return "", "", ""


def _resolve_asset_identity(report: Dict[str, Any], knowledge: Optional[Dict[str, Any]]) -> Tuple[str, str]:
    if knowledge and knowledge.get("asset_id"):
        return str(knowledge.get("asset_id")), str(knowledge.get("asset_name_source") or "")

    metadata = report.get("metadata", {}) or {}
    identifier = _safe_get(metadata, "asset_info", "identifier", default="")
    if identifier:
        return str(identifier), "asset_info.identifier"

    for item in metadata.get("model_metadata", []) or []:
        asset_name = item.get("asset_name")
        if asset_name:
            return str(asset_name), "model_metadata.asset_name"

    basename = Path(report.get("file", "")).stem
    if basename:
        return basename, "file.basename"

    default_prim = str(_safe_get(report, "stage", "default_prim", default="")).strip("/")
    candidate = default_prim.split("/")[-1] if default_prim else ""
    if candidate and candidate.lower() not in _WEAK_DEFAULT_PRIM_NAMES:
        return candidate, "default_prim"
    return candidate or "unknown_asset", "default_prim" if candidate else "unknown"


def _shape_flags(report: Dict[str, Any], knowledge: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    shape_hints = {}
    if knowledge:
        shape_hints = _safe_get(knowledge, "geometry_features", "shape_hints", default={}) or {}
    if not shape_hints:
        shape_hints = _safe_get(report, "geometry", "shape_hints", default={}) or {}
    return {
        "shape_is_box_like": shape_hints.get("is_box_like", ""),
        "shape_is_flat": shape_hints.get("is_flat", ""),
        "shape_is_tall": shape_hints.get("is_tall", ""),
        "shape_is_elongated": shape_hints.get("is_elongated", ""),
    }


def _fallback_component_map(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    geometry = report.get("geometry", {}) or {}
    materials = report.get("materials", {}) or {}
    physics = report.get("physics", {}) or {}
    bindings = materials.get("bindings", []) or []
    subsets = materials.get("subsets", []) or []
    colliders = physics.get("colliders", []) or []
    physics_material_bindings = materials.get("physics_material_bindings", []) or []

    rows: List[Dict[str, Any]] = []
    for mesh in geometry.get("mesh_prims", []) or []:
        mesh_path = mesh.get("path")
        if not mesh_path:
            continue
        normalized = str(mesh_path).replace("\\", "/").lower()
        if "/tagging/" in normalized:
            mesh_role = "aux_tagging"
        elif "/thumbrig/" in normalized:
            mesh_role = "aux_icon" if "/icon" in normalized else "aux_tagging"
        elif "/icon/" in normalized or normalized.endswith("_icon"):
            mesh_role = "aux_icon"
        elif "/preview/" in normalized:
            mesh_role = "aux_preview"
        elif "/guide/" in normalized:
            mesh_role = "aux_guide"
        elif "/proxy/" in normalized:
            mesh_role = "aux_proxy"
        else:
            mesh_role = "primary"
        is_auxiliary_mesh = mesh_role != "primary"
        render_materials = sorted(
            {
                item.get("material_path")
                for item in bindings
                if item.get("target_prim") == mesh_path and not item.get("whether_on_subset")
            }
        )
        subset_materials = sorted(
            {
                item.get("bound_material")
                for item in subsets
                if str(item.get("subset_path") or "").startswith(f"{mesh_path}/") and item.get("bound_material")
            }
        )
        collider_matches = [
            item for item in colliders
            if item.get("path") == mesh_path or str(item.get("path") or "").startswith(f"{mesh_path}/")
        ]
        physics_material_matches = [
            item for item in physics_material_bindings
            if item.get("target_prim") == mesh_path or str(item.get("target_prim") or "").startswith(f"{mesh_path}/")
        ]
        bbox_world = mesh.get("bbox_world") or {}
        bbox_size = bbox_world.get("size") if isinstance(bbox_world, dict) else None
        rows.append(
            {
                "mesh_path": mesh_path,
                "is_auxiliary_mesh": is_auxiliary_mesh,
                "mesh_role": mesh_role,
                "render_materials": render_materials,
                "subset_materials": subset_materials,
                "has_collider": bool(collider_matches),
                "collider_schema": collider_matches[0].get("schema") if collider_matches else "",
                "collider_approximation": collider_matches[0].get("approximation") if collider_matches else "",
                "collider_authored_approximation": collider_matches[0].get("approximation") if collider_matches else "",
                "collision_enabled": collider_matches[0].get("collision_enabled") if collider_matches else "",
                "has_physics_material": bool(physics_material_matches),
                "physics_materials": sorted(
                    {
                        item.get("material_path")
                        for item in physics_material_matches
                        if item.get("material_path")
                    }
                ),
                "points_count": mesh.get("points_count", ""),
                "face_count": mesh.get("face_vertex_counts_count", ""),
                "bbox_x": bbox_size[0] if isinstance(bbox_size, list) and len(bbox_size) == 3 else "",
                "bbox_y": bbox_size[1] if isinstance(bbox_size, list) and len(bbox_size) == 3 else "",
                "bbox_z": bbox_size[2] if isinstance(bbox_size, list) and len(bbox_size) == 3 else "",
                "physics_material_param_summary": "",
            }
        )
    return rows


def _find_knowledge_path(report_path: Path) -> Optional[Path]:
    candidates = []
    if report_path.name.endswith(".report.json"):
        candidates.append(report_path.with_name(report_path.name.replace(".report.json", ".knowledge_candidate.json")))
    candidates.append(report_path.with_name(report_path.stem + ".knowledge_candidate.json"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_reports(input_dir: str, recursive: bool = False) -> List[Dict[str, Any]]:
    """Load report JSON files and opportunistically attach matching knowledge JSON."""
    base = Path(input_dir)
    pattern = "**/*.report.json" if recursive else "*.report.json"
    entries: List[Dict[str, Any]] = []
    for report_path in sorted(base.glob(pattern)):
        if not report_path.is_file():
            continue
        with report_path.open("r", encoding="utf-8") as handle:
            report = json.load(handle)
        knowledge_path = _find_knowledge_path(report_path)
        knowledge = None
        if knowledge_path:
            with knowledge_path.open("r", encoding="utf-8") as handle:
                knowledge = json.load(handle)
        entries.append(
            {
                "report_path": str(report_path),
                "knowledge_path": str(knowledge_path) if knowledge_path else "",
                "report": report,
                "knowledge": knowledge,
            }
        )
    return entries


def flatten_asset_summary(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create one row per asset with report + candidate fields merged for review."""
    rows: List[Dict[str, Any]] = []
    for entry in entries:
        report = entry["report"]
        knowledge = entry.get("knowledge") or {}
        asset_id, asset_name_source = _resolve_asset_identity(report, knowledge or None)
        metadata = report.get("metadata", {}) or {}
        summary = report.get("summary", {}) or {}
        structure = knowledge.get("structure_pattern", {}) or {}
        physics_values = knowledge.get("physics_values", {}) or {}
        completeness = knowledge.get("simready_completeness", {}) or {}
        semantic_candidates = knowledge.get("semantic_candidates", []) or []
        material_candidates = knowledge.get("material_family_candidates", []) or []
        profile_candidates = knowledge.get("physics_profile_candidates", []) or []
        world_x, world_y, world_z = _world_bbox_triplet(report, knowledge or None)
        top_semantic, top_semantic_confidence = _first_candidate(semantic_candidates, "label")
        top_material, top_material_confidence = _first_candidate(material_candidates, "label")
        top_profile, top_profile_confidence = _first_candidate(profile_candidates, "profile")
        shape_flags = _shape_flags(report, knowledge or None)

        display_names = metadata.get("display_names", []) or []
        display_name = display_names[0].get("display_name", "") if display_names else ""
        identifier = _safe_get(metadata, "asset_info", "identifier", default="")
        model_metadata = metadata.get("model_metadata", []) or []
        kind = metadata.get("kinds", [""])[0] if metadata.get("kinds") else ""
        bbox_volume = _safe_get(knowledge, "geometry_features", "volume_estimate_bbox", default="")
        if bbox_volume == "":
            try:
                bbox_volume = float(world_x) * float(world_y) * float(world_z)
            except Exception:
                bbox_volume = ""

        rows.append(
            {
                "asset_id": asset_id,
                "asset_name_source": asset_name_source,
                "file": report.get("file", ""),
                "asset_variant_role": _safe_get(knowledge, "asset_variant_role", "value", default=""),
                "default_prim": _safe_get(report, "stage", "default_prim", default=""),
                "kind": kind,
                "display_name": display_name,
                "identifier": identifier,
                "mesh_count": summary.get("mesh_count", ""),
                "material_count": summary.get("material_count", ""),
                "subset_count": summary.get("subset_count", ""),
                "has_any_physics": summary.get("has_any_physics", ""),
                "has_any_material_binding": summary.get("has_any_material_binding", ""),
                "has_rigid_body": physics_values.get("has_rigid_body", ""),
                "has_collision": physics_values.get("has_collision", ""),
                "has_mass": physics_values.get("has_mass", ""),
                "has_physics_material": physics_values.get("has_physics_material", ""),
                "physics_root": structure.get("physics_root", ""),
                "mass_owner": structure.get("mass_owner", ""),
                "collision_prim_count": len(physics_values.get("collision_paths", []) or []),
                "mass_value_count": len(physics_values.get("mass_values", []) or []),
                "density_value_count": len(physics_values.get("density_values", []) or []),
                "collision_approximation_count": len(physics_values.get("collision_approximations", []) or []),
                "physics_material_param_count": len(physics_values.get("physics_material_params", []) or []),
                "world_bbox_x": world_x,
                "world_bbox_y": world_y,
                "world_bbox_z": world_z,
                "bbox_volume_estimate": bbox_volume,
                "is_multi_mesh": _safe_get(knowledge, "geometry_features", "is_multi_mesh", default=summary.get("mesh_count", 0) > 1),
                "primary_mesh_count": _safe_get(knowledge, "geometry_features", "primary_mesh_count", default=""),
                "auxiliary_mesh_count": _safe_get(knowledge, "geometry_features", "auxiliary_mesh_count", default=""),
                "bbox_may_include_auxiliary_mesh": _safe_get(knowledge, "geometry_features", "bbox_may_include_auxiliary_mesh", default=""),
                "shape_is_box_like": shape_flags["shape_is_box_like"],
                "shape_is_flat": shape_flags["shape_is_flat"],
                "shape_is_tall": shape_flags["shape_is_tall"],
                "shape_is_elongated": shape_flags["shape_is_elongated"],
                "collider_recommendation": _safe_get(knowledge, "collider_recommendation", "recommended", default=_safe_get(report, "collider_recommendation", "recommended", default="")),
                "structure_pattern": structure.get("pattern_class", ""),
                "top_semantic_candidate": top_semantic,
                "top_semantic_confidence": top_semantic_confidence,
                "top_material_family_candidate": top_material,
                "top_material_family_confidence": top_material_confidence,
                "top_physics_profile_candidate": top_profile,
                "top_profile_confidence": top_profile_confidence,
                "simready_geometry": completeness.get("geometry", ""),
                "simready_render_material": completeness.get("render_material", ""),
                "simready_physics_schema": completeness.get("physics_schema", ""),
                "simready_physics_values": completeness.get("physics_values", ""),
                "simready_physics_material": completeness.get("physics_material", ""),
                "simready_semantic_label": completeness.get("semantic_label", ""),
                "simready_overall": completeness.get("overall", ""),
                "review_flags": _join_list(knowledge.get("review_flags", [])),
                "issues_count": len(report.get("issues", []) or []),
                "notes_count": len(report.get("notes", []) or []),
                "source_report_file": entry.get("report_path", ""),
                "source_knowledge_candidate_file": entry.get("knowledge_path", ""),
            }
        )
    return rows


def flatten_component_map(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create one row per mesh/component for review-friendly CSV output."""
    rows: List[Dict[str, Any]] = []
    for entry in entries:
        report = entry["report"]
        knowledge = entry.get("knowledge") or {}
        asset_id, _ = _resolve_asset_identity(report, knowledge or None)
        components = knowledge.get("component_map") or _fallback_component_map(report)
        points_lookup = {
            item.get("path"): item for item in (_safe_get(report, "geometry", "mesh_prims", default=[]) or [])
            if item.get("path")
        }

        for component in components:
            mesh_path = component.get("mesh_path", "")
            mesh_detail = points_lookup.get(mesh_path, {})
            bbox_world = mesh_detail.get("bbox_world", {}) if isinstance(mesh_detail, dict) else {}
            bbox_size = bbox_world.get("size") if isinstance(bbox_world, dict) else None
            rows.append(
                {
                    "asset_id": asset_id,
                    "file": report.get("file", ""),
                    "mesh_path": mesh_path,
                    "render_materials": _join_list(component.get("render_materials", [])),
                    "subset_materials": _join_list(component.get("subset_materials", [])),
                    "is_auxiliary_mesh": component.get("is_auxiliary_mesh", ""),
                    "mesh_role": component.get("mesh_role", ""),
                    "has_collider": component.get("has_collider", ""),
                    "collider_schema": component.get("collider_schema", ""),
                    "collider_approximation": component.get("collider_approximation", ""),
                    "collider_authored_approximation": component.get("collider_authored_approximation", component.get("collider_approximation", "")),
                    "collision_enabled": component.get("collision_enabled", ""),
                    "has_physics_material": component.get("has_physics_material", ""),
                    "physics_materials": _join_list(component.get("physics_materials", [])),
                    "physics_material_param_summary": component.get("physics_material_param_summary", ""),
                    "points_count": component.get("points_count", mesh_detail.get("points_count", "")),
                    "face_count": component.get("face_count", mesh_detail.get("face_vertex_counts_count", "")),
                    "bbox_x": component.get("bbox_x", bbox_size[0] if isinstance(bbox_size, list) and len(bbox_size) == 3 else ""),
                    "bbox_y": component.get("bbox_y", bbox_size[1] if isinstance(bbox_size, list) and len(bbox_size) == 3 else ""),
                    "bbox_z": component.get("bbox_z", bbox_size[2] if isinstance(bbox_size, list) and len(bbox_size) == 3 else ""),
                }
            )
    return rows


def flatten_candidate_review(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create one row per asset for human review and approval workflows."""
    rows: List[Dict[str, Any]] = []
    for entry in entries:
        report = entry["report"]
        knowledge = entry.get("knowledge") or {}
        asset_id, asset_name_source = _resolve_asset_identity(report, knowledge or None)
        rows.append(
            {
                "asset_id": asset_id,
                "asset_name_source": asset_name_source,
                "file": report.get("file", ""),
                "asset_variant_role": _safe_get(knowledge, "asset_variant_role", "value", default=""),
                "structure_pattern": _safe_get(knowledge, "structure_pattern", "pattern_class", default=""),
                "semantic_candidates": _compact_candidates(knowledge.get("semantic_candidates", []) or [], "label"),
                "material_family_candidates": _compact_candidates(knowledge.get("material_family_candidates", []) or [], "label"),
                "physics_profile_candidates": _compact_candidates(knowledge.get("physics_profile_candidates", []) or [], "profile"),
                "collider_recommendation": _safe_get(knowledge, "collider_recommendation", "recommended", default=""),
                "simready_overall": _safe_get(knowledge, "simready_completeness", "overall", default=""),
                "review_flags": _join_list(knowledge.get("review_flags", [])),
                "auxiliary_mesh_present": bool(_safe_get(knowledge, "geometry_features", "auxiliary_mesh_count", default=0)),
                "human_semantic_label": "",
                "human_material_family": "",
                "human_profile": "",
                "human_structure_pattern": "",
                "human_collider_choice": "",
                "human_variant_keep": "",
                "human_notes": "",
                "approved": "",
                "review_status": "",
            }
        )
    return rows


def write_csvs(
    output_dir: str,
    asset_summary_rows: List[Dict[str, Any]],
    component_map_rows: List[Dict[str, Any]],
    candidate_review_rows: List[Dict[str, Any]],
) -> None:
    """Write all flattened row sets to CSV files."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    def _write(path: Path, rows: List[Dict[str, Any]]) -> None:
        fieldnames = list(rows[0].keys()) if rows else []
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    _write(output / "asset_summary.csv", asset_summary_rows)
    _write(output / "component_map.csv", component_map_rows)
    _write(output / "candidate_review.csv", candidate_review_rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Flatten inspector report JSON files into review CSV files.")
    parser.add_argument("--input-dir", required=True, help="Directory containing *.report.json files")
    parser.add_argument("--output-dir", required=True, help="Directory to write CSV files into")
    parser.add_argument("--recursive", action="store_true", help="Recursively search for *.report.json files")
    parser.add_argument("--include-component-map", action="store_true", help="Emit component_map.csv")
    parser.add_argument("--include-candidate-review", action="store_true", help="Emit candidate_review.csv")
    args = parser.parse_args(argv)

    entries = load_reports(args.input_dir, recursive=args.recursive)
    asset_summary_rows = flatten_asset_summary(entries)
    component_map_rows = flatten_component_map(entries) if args.include_component_map else []
    candidate_review_rows = flatten_candidate_review(entries) if args.include_candidate_review else []
    write_csvs(args.output_dir, asset_summary_rows, component_map_rows, candidate_review_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
