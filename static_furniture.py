#!/usr/bin/env python3
"""Static furniture reference and recommendation helpers."""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from knowledge_candidate import build_knowledge_candidate
from usd_inspector import build_detailed_report, open_stage


FURNITURE_CLASSES = {
    "chair",
    "sofa",
    "stool",
    "bench",
    "ottoman",
    "table",
    "desk",
    "cabinet",
    "shelf",
    "storage",
    "decor",
}

USD_SUFFIXES = (".usd", ".usda", ".usdc", ".usdz")


def inspect_asset(input_usd: str, max_prims: int = 0) -> Dict[str, Any]:
    stage = open_stage(input_usd)
    report = build_detailed_report(stage, input_usd, max_prims=max_prims)
    knowledge = build_knowledge_candidate(report)
    return {"report": report, "knowledge": knowledge}


def _replace_variant_suffix(file_path: str, old_suffix: str, new_suffix: str) -> str:
    root, ext = os.path.splitext(file_path)
    if not old_suffix:
        return root + new_suffix + ext
    if root.endswith(old_suffix):
        return root[: -len(old_suffix)] + new_suffix + ext
    return file_path


def suggest_authoring_source_path(file_path: str, variant_role: str, primary_mesh_count: int) -> str:
    if primary_mesh_count > 0:
        return file_path
    candidate = file_path
    if variant_role == "main":
        candidate = _replace_variant_suffix(file_path, "", "_inst")
    elif variant_role == "base":
        candidate = _replace_variant_suffix(file_path, "_base", "_inst_base")
    if candidate != file_path and os.path.exists(candidate):
        return candidate
    return file_path


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in ("", None):
            return None
        return float(value)
    except Exception:
        return None


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _top_candidate_label(candidates: Iterable[Dict[str, Any]], key: str) -> str:
    for item in candidates:
        label = str(item.get(key) or "").strip()
        if label:
            return label
    return ""


def _collect_name_tokens(report: Dict[str, Any], knowledge: Dict[str, Any]) -> str:
    parts = [
        knowledge.get("asset_id", ""),
        ((knowledge.get("asset_variant_role", {}) or {}).get("value") or ""),
        _top_candidate_label(knowledge.get("semantic_candidates", []) or [], "label"),
        ((report.get("stage", {}) or {}).get("default_prim") or ""),
    ]
    for item in knowledge.get("semantic_candidates", []) or []:
        label = item.get("label")
        if label:
            parts.append(str(label))
    semantic_metadata = knowledge.get("semantic_metadata", {}) or {}
    for key in ("classes", "hierarchies", "label_tags", "anchor_tags"):
        for value in semantic_metadata.get(key, []) or []:
            parts.append(str(value))
    metadata = report.get("metadata", {}) or {}
    for item in metadata.get("display_names", []) or []:
        parts.append(item.get("display_name", ""))
    return " ".join(_normalize_text(part) for part in parts if part)


def classify_furniture_class(report: Dict[str, Any], knowledge: Dict[str, Any]) -> Tuple[str, bool, List[str]]:
    token_blob = _collect_name_tokens(report, knowledge)
    basis: List[str] = []

    keyword_map = [
        ("tableware", "decor"),
        ("kitchenware", "decor"),
        ("utensil", "decor"),
        ("vase", "decor"),
        ("bowl", "decor"),
        ("armchair", "chair"),
        ("diningchair", "chair"),
        ("chair", "chair"),
        ("loveseat", "sofa"),
        ("sofa", "sofa"),
        ("couch", "sofa"),
        ("ottoman", "ottoman"),
        ("bar_stool", "stool"),
        ("stool", "stool"),
        ("bench", "bench"),
        ("coffeetable", "table"),
        ("endtable", "table"),
        ("sofatable", "table"),
        ("table", "table"),
        ("desk", "desk"),
        ("cabinet", "cabinet"),
        ("locker", "cabinet"),
        ("storage", "storage"),
        ("shelf", "shelf"),
        ("bookcase", "shelf"),
        ("decor", "decor"),
        ("skull", "decor"),
    ]

    for keyword, label in keyword_map:
        if keyword in token_blob:
            basis.append(f'keyword "{keyword}" matched asset naming signals')
            return label, label in FURNITURE_CLASSES, basis

    top_semantic = _top_candidate_label(knowledge.get("semantic_candidates", []) or [], "label")
    if top_semantic in {"furniture_seating"}:
        basis.append("semantic candidate matched furniture seating")
        return "chair", True, basis
    if top_semantic in {"furniture_surface"}:
        basis.append("semantic candidate matched furniture surface")
        return "table", True, basis
    if top_semantic in {"storage_furniture"}:
        basis.append("semantic candidate matched storage furniture")
        return "storage", True, basis
    if top_semantic in {"decor_container"}:
        basis.append("semantic candidate matched decor container")
        return "decor", True, basis

    basis.append("no furniture keyword or semantic candidate matched")
    return "non_furniture", False, basis


def derive_size_features(knowledge: Dict[str, Any]) -> Dict[str, Any]:
    geometry = knowledge.get("geometry_features", {}) or {}
    bbox = geometry.get("world_bbox_size")
    bbox_x = bbox[0] if isinstance(bbox, list) and len(bbox) == 3 else None
    bbox_y = bbox[1] if isinstance(bbox, list) and len(bbox) == 3 else None
    bbox_z = bbox[2] if isinstance(bbox, list) and len(bbox) == 3 else None

    volume = _safe_float(geometry.get("volume_estimate_bbox"))
    max_dimension = _safe_float(geometry.get("max_dimension"))
    height = _safe_float(bbox_z)

    if volume is None:
        size_bucket = "unknown"
    elif volume < 0.1:
        size_bucket = "small"
    elif volume < 1.5:
        size_bucket = "medium"
    else:
        size_bucket = "large"

    if height is None:
        height_band = "unknown"
    elif height < 0.5:
        height_band = "low"
    elif height < 1.2:
        height_band = "mid"
    else:
        height_band = "tall"

    return {
        "bbox": bbox if isinstance(bbox, list) else None,
        "footprint": [bbox_x, bbox_y] if bbox_x is not None and bbox_y is not None else None,
        "height_band": height_band,
        "size_bucket": size_bucket,
        "max_dimension": max_dimension,
        "aspect_ratio_hint": geometry.get("aspect_ratio_hint"),
        "volume_estimate_bbox": volume,
    }


def derive_support_structure(
    report: Dict[str, Any],
    knowledge: Dict[str, Any],
    furniture_class: str,
) -> Dict[str, Any]:
    geometry = knowledge.get("geometry_features", {}) or {}
    token_blob = _collect_name_tokens(report, knowledge)
    seat_like = furniture_class in {"chair", "sofa", "stool", "bench", "ottoman"}
    support_surface_likely = furniture_class in {"table", "desk", "cabinet", "shelf", "storage"}
    storage_like = furniture_class in {"cabinet", "shelf", "storage"}
    backrest_likely = any(token in token_blob for token in ("chair", "sofa", "loveseat", "armchair"))
    legged_likely = furniture_class in {"chair", "table", "desk", "bench", "stool"} or int(geometry.get("primary_mesh_count") or 0) >= 3
    narrow_tall_likely = geometry.get("aspect_ratio_hint") == "tall"

    return {
        "ground_contact_likely": geometry.get("is_ground_contact_likely"),
        "support_surface_likely": support_surface_likely,
        "seat_like": seat_like,
        "storage_like": storage_like,
        "backrest_likely": backrest_likely,
        "legged_likely": legged_likely,
        "narrow_tall_likely": narrow_tall_likely,
    }


def recommend_static_collider(
    knowledge: Dict[str, Any],
    furniture_class: str,
    support_structure: Dict[str, Any],
) -> Dict[str, Any]:
    geometry = knowledge.get("geometry_features", {}) or {}
    physics = knowledge.get("physics_values", {}) or {}
    shape_hints = (geometry.get("shape_hints", {}) or {})
    primary_mesh_count = int(geometry.get("primary_mesh_count") or geometry.get("mesh_count") or 0)
    face_count_total = int(geometry.get("face_count_total") or 0)
    aspect_ratio_hint = geometry.get("aspect_ratio_hint")
    basis: List[str] = []

    if primary_mesh_count == 0:
        return {
            "approximation": "none",
            "scope": "none",
            "confidence": 0.98,
            "basis": ["no primary mesh detected"],
        }

    if physics.get("guide_collider_paths") and "none" in set(physics.get("collision_approximations") or []):
        return {
            "approximation": "none",
            "scope": "per_component",
            "confidence": 0.97,
            "basis": [
                "guide-purpose collider mesh detected",
                "existing collider uses no approximation",
            ],
        }

    if furniture_class in {"cabinet", "storage", "shelf"} and shape_hints.get("is_box_like"):
        return {
            "approximation": "convexHull",
            "scope": "whole_asset",
            "confidence": 0.84,
            "basis": [
                "box-like storage furniture detected",
                "convex hull is safer than a coarse bounding cube for furniture silhouettes",
            ],
        }

    if furniture_class in {"table", "desk"} and support_structure.get("support_surface_likely"):
        basis.append("surface furniture should preserve top silhouette")
        if primary_mesh_count > 1:
            basis.append("multiple primary meshes detected")
            return {
                "approximation": "convexDecomposition",
                "scope": "per_component",
                "confidence": 0.87,
                "basis": basis,
            }
        return {
            "approximation": "convexHull",
            "scope": "whole_asset",
            "confidence": 0.78,
            "basis": basis,
        }

    if furniture_class in {"chair", "sofa", "bench", "stool", "ottoman"}:
        basis.append("seating furniture usually needs leg and seat coverage")
        if primary_mesh_count > 1 or face_count_total > 12000:
            basis.append("geometry is multi-part or moderately complex")
            return {
                "approximation": "convexDecomposition",
                "scope": "per_component",
                "confidence": 0.9,
                "basis": basis,
            }
        return {
            "approximation": "convexHull",
            "scope": "whole_asset",
            "confidence": 0.8,
            "basis": basis,
        }

    if aspect_ratio_hint == "flat":
        return {
            "approximation": "meshSimplified",
            "scope": "whole_asset",
            "confidence": 0.7,
            "basis": ["geometry appears flat", "flat static surfaces can preserve support area with simplified mesh"],
        }

    if primary_mesh_count > 1:
        return {
            "approximation": "convexDecomposition",
            "scope": "per_component",
            "confidence": 0.82,
            "basis": ["fallback multi-mesh collider recommendation"],
        }

    return {
        "approximation": "convexHull",
        "scope": "whole_asset",
        "confidence": 0.64,
        "basis": ["fallback single-mesh collider recommendation"],
    }


def build_static_furniture_asset_reference(
    report: Dict[str, Any],
    knowledge: Dict[str, Any],
) -> Dict[str, Any]:
    furniture_class, is_furniture, furniture_basis = classify_furniture_class(report, knowledge)
    size_features = derive_size_features(knowledge)
    support_structure = derive_support_structure(report, knowledge, furniture_class)
    local_collider = recommend_static_collider(knowledge, furniture_class, support_structure)
    component_map = knowledge.get("component_map", []) or []
    primary_mesh_paths = [
        item.get("mesh_path")
        for item in component_map
        if item.get("mesh_path") and not item.get("is_auxiliary_mesh")
    ]
    top_material = _top_candidate_label(knowledge.get("material_family_candidates", []) or [], "label") or "unknown"
    primary_mesh_count = int(knowledge.get("geometry_features", {}).get("primary_mesh_count") or 0)
    variant_role = ((knowledge.get("asset_variant_role", {}) or {}).get("value") or "")
    authoring_source_file = suggest_authoring_source_path(report.get("file", ""), variant_role, primary_mesh_count)

    return {
        "asset_id": knowledge.get("asset_id"),
        "file": report.get("file"),
        "variant_role": variant_role,
        "authoring_source_file": authoring_source_file,
        "is_furniture": is_furniture,
        "furniture_class": furniture_class,
        "furniture_basis": furniture_basis,
        "semantic_metadata": knowledge.get("semantic_metadata", {}) or {},
        "material_family": top_material,
        "size": size_features,
        "support_structure": support_structure,
        "geometry": {
            "mesh_count": knowledge.get("geometry_features", {}).get("mesh_count"),
            "primary_mesh_count": knowledge.get("geometry_features", {}).get("primary_mesh_count"),
            "auxiliary_mesh_count": knowledge.get("geometry_features", {}).get("auxiliary_mesh_count"),
            "points_count_total": knowledge.get("geometry_features", {}).get("points_count_total"),
            "face_count_total": knowledge.get("geometry_features", {}).get("face_count_total"),
            "is_multi_mesh": knowledge.get("geometry_features", {}).get("is_multi_mesh"),
            "target_mesh_paths": primary_mesh_paths,
        },
        "static_collider": local_collider,
        "review_flags": knowledge.get("review_flags", []) or [],
    }


def _counter_distribution(counter: Counter[str]) -> Dict[str, float]:
    total = sum(counter.values())
    if total <= 0:
        return {}
    return {key: round(value / total, 4) for key, value in sorted(counter.items()) if key}


def _group_key(asset: Dict[str, Any]) -> str:
    support = asset.get("support_structure", {}) or {}
    size = asset.get("size", {}) or {}
    return "__".join(
        [
            asset.get("furniture_class", "unknown"),
            asset.get("material_family", "unknown"),
            size.get("size_bucket", "unknown"),
            "seat" if support.get("seat_like") else "nonseat",
            "storage" if support.get("storage_like") else "nonstorage",
        ]
    )


def build_reference_library(asset_references: List[Dict[str, Any]], source_root: str = "") -> Dict[str, Any]:
    furniture_assets = [item for item in asset_references if item.get("is_furniture")]
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for asset in furniture_assets:
        groups[_group_key(asset)].append(asset)

    group_entries: List[Dict[str, Any]] = []
    for key, items in sorted(groups.items()):
        collider_counter = Counter(item.get("static_collider", {}).get("approximation") or "" for item in items)
        scope_counter = Counter(item.get("static_collider", {}).get("scope") or "" for item in items)
        group_entries.append(
            {
                "group_key": key,
                "asset_count": len(items),
                "furniture_class": items[0].get("furniture_class", ""),
                "material_family": items[0].get("material_family", ""),
                "size_bucket": ((items[0].get("size", {}) or {}).get("size_bucket") or ""),
                "seat_like": bool((items[0].get("support_structure", {}) or {}).get("seat_like")),
                "storage_like": bool((items[0].get("support_structure", {}) or {}).get("storage_like")),
                "collider_distribution": _counter_distribution(collider_counter),
                "scope_distribution": _counter_distribution(scope_counter),
                "sample_asset_ids": [item.get("asset_id") for item in items[:8]],
            }
        )

    return {
        "reference_type": "static_furniture_reference_library",
        "reference_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": source_root,
        "asset_count": len(asset_references),
        "furniture_asset_count": len(furniture_assets),
        "assets": asset_references,
        "groups": group_entries,
    }


def find_usd_files(input_path: str, recursive: bool = False) -> List[str]:
    path = Path(input_path)
    if path.is_file():
        return [str(path)]
    pattern = "**/*" if recursive else "*"
    return [
        str(candidate)
        for candidate in sorted(path.glob(pattern))
        if candidate.is_file() and candidate.suffix.lower() in USD_SUFFIXES
    ]


def build_reference_library_from_usd_paths(usd_paths: List[str], max_prims: int = 0) -> Dict[str, Any]:
    assets: List[Dict[str, Any]] = []
    for usd_path in usd_paths:
        inspected = inspect_asset(usd_path, max_prims=max_prims)
        assets.append(build_static_furniture_asset_reference(inspected["report"], inspected["knowledge"]))
    return build_reference_library(assets)


def _match_group_score(query: Dict[str, Any], group: Dict[str, Any]) -> float:
    score = 0.0
    if query.get("furniture_class") == group.get("furniture_class"):
        score += 5.0
    if query.get("material_family") == group.get("material_family"):
        score += 2.0
    if ((query.get("size", {}) or {}).get("size_bucket")) == group.get("size_bucket"):
        score += 1.5
    query_support = query.get("support_structure", {}) or {}
    if bool(query_support.get("seat_like")) == bool(group.get("seat_like")):
        score += 1.0
    if bool(query_support.get("storage_like")) == bool(group.get("storage_like")):
        score += 1.0
    return score


def _top_distribution_label(distribution: Dict[str, Any]) -> str:
    if not distribution:
        return ""
    return max(distribution.items(), key=lambda item: float(item[1]))[0]


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) * 0.5


def build_size_recommendation(
    reference_library: Dict[str, Any],
    query: Dict[str, Any],
    best_group: Dict[str, Any],
) -> Dict[str, Any]:
    query_size = (query.get("size", {}) or {})
    query_bbox = query_size.get("bbox")
    if not isinstance(query_bbox, list) or len(query_bbox) != 3:
        return {
            "status": "unavailable",
            "basis": ["query asset bbox missing"],
        }

    group_key = best_group.get("group_key", "")
    reference_assets = [
        asset
        for asset in (reference_library.get("assets", []) or [])
        if asset.get("is_furniture") and _group_key(asset) == group_key
    ]
    if not reference_assets:
        return {
            "status": "unavailable",
            "basis": ["no reference assets found for matched group"],
        }

    ref_bboxes = []
    for asset in reference_assets:
        bbox = ((asset.get("size", {}) or {}).get("bbox"))
        if isinstance(bbox, list) and len(bbox) == 3 and all(_safe_float(item) not in (None, 0.0) for item in bbox):
            ref_bboxes.append([float(bbox[0]), float(bbox[1]), float(bbox[2])])

    if not ref_bboxes:
        return {
            "status": "unavailable",
            "basis": ["matched group has no valid bbox samples"],
        }

    median_bbox = [
        _median([bbox[index] for bbox in ref_bboxes]) or 0.0
        for index in range(3)
    ]
    axis_scale = []
    for index in range(3):
        query_value = _safe_float(query_bbox[index])
        target_value = _safe_float(median_bbox[index])
        if query_value in (None, 0.0) or target_value is None:
            axis_scale.append(None)
        else:
            axis_scale.append(target_value / query_value)

    valid_scales = [value for value in axis_scale if value is not None]
    suggested_uniform_scale = min(valid_scales) if valid_scales else None
    max_axis_ratio = None
    if valid_scales:
        max_axis_ratio = max(valid_scales) / min(valid_scales) if min(valid_scales) > 0 else None

    size_warning = ""
    status = "ok"
    if suggested_uniform_scale is not None and (suggested_uniform_scale < 0.1 or suggested_uniform_scale > 10.0):
        size_warning = "bbox_outlier_vs_reference"
        status = "review"
    elif max_axis_ratio is not None and max_axis_ratio > 1.5:
        size_warning = "axis_ratio_mismatch_vs_reference"
        status = "review"

    basis = [
        f'matched reference group "{group_key}"',
        f"reference sample_count={len(ref_bboxes)}",
        "target bbox derived from median bbox of matched reference assets",
    ]
    if size_warning:
        basis.append(f"warning={size_warning}")

    return {
        "status": status,
        "reference_target_bbox": median_bbox,
        "axis_scale_to_target_bbox": axis_scale,
        "suggested_uniform_scale": suggested_uniform_scale,
        "size_warning": size_warning,
        "basis": basis,
    }


def recommend_from_reference(
    reference_library: Dict[str, Any],
    report: Dict[str, Any],
    knowledge: Dict[str, Any],
) -> Dict[str, Any]:
    query = build_static_furniture_asset_reference(report, knowledge)
    authoring_source_file = query.get("authoring_source_file") or query.get("file")
    authoring_mesh_paths = ((query.get("geometry", {}) or {}).get("target_mesh_paths") or [])
    if authoring_source_file and authoring_source_file != query.get("file"):
        try:
            authoring_inspected = inspect_asset(authoring_source_file)
            authoring_query = build_static_furniture_asset_reference(
                authoring_inspected["report"],
                authoring_inspected["knowledge"],
            )
            authoring_mesh_paths = ((authoring_query.get("geometry", {}) or {}).get("target_mesh_paths") or [])
        except Exception:
            authoring_mesh_paths = authoring_mesh_paths

    groups = reference_library.get("groups", []) or []
    sorted_groups = sorted(groups, key=lambda item: _match_group_score(query, item), reverse=True)
    best_group = sorted_groups[0] if sorted_groups else {}

    recommended_approximation = _top_distribution_label(best_group.get("collider_distribution", {}) or {})
    recommended_scope = _top_distribution_label(best_group.get("scope_distribution", {}) or {})
    if not recommended_approximation:
        recommended_approximation = query.get("static_collider", {}).get("approximation", "convexHull")
    if not recommended_scope:
        recommended_scope = query.get("static_collider", {}).get("scope", "whole_asset")
    size_recommendation = build_size_recommendation(reference_library, query, best_group)

    similar_assets = []
    target_group_key = best_group.get("group_key")
    for asset in reference_library.get("assets", []) or []:
        if asset.get("is_furniture") and _group_key(asset) == target_group_key:
            similar_assets.append(
                {
                    "asset_id": asset.get("asset_id"),
                    "file": asset.get("file"),
                    "static_collider": asset.get("static_collider", {}),
                }
            )
        if len(similar_assets) >= 5:
            break

    basis = []
    if best_group:
        basis.append(f'matched reference group "{best_group.get("group_key")}"')
        basis.append(f'group asset_count={best_group.get("asset_count")}')
    else:
        basis.append("no matching reference group found; using local heuristic")

    recommendation = {
        "asset": query,
        "recommendation": {
            "recommended_collider": {
                "approximation": recommended_approximation,
                "scope": recommended_scope,
                "confidence": round(min(0.95, 0.55 + (0.05 * len(similar_assets))), 2),
                "basis": basis + (query.get("static_collider", {}).get("basis") or []),
            },
            "size_recommendation": size_recommendation,
            "reference_group_key": best_group.get("group_key", ""),
            "reference_group_asset_count": best_group.get("asset_count", 0),
            "authoring": {
                "collision_enabled": True,
                "approximation": recommended_approximation,
                "collider_scope": recommended_scope,
                "source_usd_for_authoring": authoring_source_file,
                "target_mesh_paths": authoring_mesh_paths,
                "author_rigid_body": False,
                "kinematic_mode": "static",
            },
        },
        "similar_reference_assets": similar_assets,
        "review_flags": query.get("review_flags", []) or [],
    }
    return recommendation


def save_json(path: str, data: Dict[str, Any], pretty: bool = True) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2 if pretty else None)


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
