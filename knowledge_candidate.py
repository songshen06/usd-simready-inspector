#!/usr/bin/env python3
"""Rule-driven knowledge candidate generator built on inspector reports.

This module intentionally consumes the detailed inspector report dict instead of
re-opening USD assets. It keeps raw facts and inferred candidates separate and
attaches explicit basis/source/confidence metadata to every candidate output.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

_WEAK_DEFAULT_PRIM_NAMES = {"rootnode", "world", "asset", "root"}


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _basename_without_ext(file_path: str) -> str:
    name = os.path.basename(file_path or "")
    for suffix in (".usdz", ".usdc", ".usda", ".usd", ".json"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return os.path.splitext(name)[0]


def _dedupe_candidates(
    candidates: List[Dict[str, Any]],
    key_field: str,
    confidence_field: str = "confidence",
) -> List[Dict[str, Any]]:
    best: Dict[str, Dict[str, Any]] = {}
    for item in candidates:
        key = str(item.get(key_field) or "").strip()
        if not key:
            continue
        existing = best.get(key)
        if existing is None or float(item.get(confidence_field, 0.0)) > float(existing.get(confidence_field, 0.0)):
            best[key] = item
    return sorted(best.values(), key=lambda item: float(item.get(confidence_field, 0.0)), reverse=True)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _collect_name_signals(report: Dict[str, Any]) -> List[Tuple[str, str]]:
    metadata = report.get("metadata", {}) or {}
    file_path = report.get("file", "")
    signals: List[Tuple[str, str]] = []

    asset_info = metadata.get("asset_info", {}) or {}
    identifier = asset_info.get("identifier")
    if identifier:
        signals.append((str(identifier), "metadata.asset_info.identifier"))

    for item in metadata.get("display_names", []) or []:
        value = item.get("display_name")
        if value:
            signals.append((str(value), "metadata.display_names"))

    for item in metadata.get("model_metadata", []) or []:
        value = item.get("asset_name")
        if value:
            signals.append((str(value), "metadata.model_metadata.asset_name"))

    basename = _basename_without_ext(file_path)
    if basename:
        signals.append((basename, "file.basename"))
    return signals


def _extract_asset_identity(report: Dict[str, Any]) -> Tuple[str, str]:
    """Choose a stable asset id and record which source won.

    The ordering intentionally prefers authored identifiers and file basename
    over generic default prim names like RootNode/World.
    """
    metadata = report.get("metadata", {}) or {}
    asset_info = metadata.get("asset_info", {}) or {}
    identifier = asset_info.get("identifier")
    if identifier:
        return str(identifier), "asset_info.identifier"

    for item in metadata.get("model_metadata", []) or []:
        asset_name = item.get("asset_name")
        if asset_name:
            return str(asset_name), "model_metadata.asset_name"

    basename = _basename_without_ext(report.get("file", ""))
    if basename:
        return basename, "file.basename"

    default_prim = ((report.get("stage", {}) or {}).get("default_prim") or "").strip("/")
    if default_prim:
        candidate = default_prim.split("/")[-1]
        if candidate.lower() not in _WEAK_DEFAULT_PRIM_NAMES:
            return candidate, "default_prim"
        if basename:
            return basename, "file.basename"
        return candidate, "default_prim"

    return "unknown_asset", "unknown"


def infer_asset_variant_role(file_path: str) -> Dict[str, Any]:
    """Infer asset role from filename using conservative naming heuristics."""
    basename = _basename_without_ext(file_path).lower()
    if basename.endswith("_inst_base"):
        return {
            "value": "inst_base",
            "basis": "basename endswith _inst_base",
            "confidence": 0.99,
        }
    if basename.endswith("_inst"):
        return {
            "value": "inst",
            "basis": "basename endswith _inst",
            "confidence": 0.97,
        }
    if basename.endswith("_base"):
        return {
            "value": "base",
            "basis": "basename endswith _base",
            "confidence": 0.97,
        }
    if basename:
        return {
            "value": "main",
            "basis": "basename does not match base/inst suffix conventions",
            "confidence": 0.72,
        }
    return {
        "value": "unknown",
        "basis": "file path missing or basename unavailable",
        "confidence": 0.1,
    }


def _classify_mesh_role(mesh_path: str) -> Tuple[bool, str]:
    """Classify likely auxiliary meshes from path-only signals.

    This is intentionally conservative: obvious tagging/preview paths are
    classified as auxiliary, everything else remains primary.
    """
    normalized = mesh_path.replace("\\", "/").lower()
    if "/tagging/" in normalized:
        return True, "aux_tagging"
    if "/thumbrig/" in normalized:
        return True, "aux_icon" if "/icon" in normalized else "aux_tagging"
    if "/icon/" in normalized or normalized.endswith("_icon"):
        return True, "aux_icon"
    if "/preview/" in normalized:
        return True, "aux_preview"
    if "/guide/" in normalized:
        return True, "aux_guide"
    if "/proxy/" in normalized:
        return True, "aux_proxy"
    return False, "primary"


def extract_structure_pattern(report: Dict[str, Any]) -> Dict[str, Any]:
    """Classify high-level asset structure from existing raw report sections."""
    geometry = report.get("geometry", {}) or {}
    materials = report.get("materials", {}) or {}
    physics = report.get("physics", {}) or {}
    summary = report.get("summary", {}) or {}

    rigid_body_paths = [item.get("path") for item in physics.get("rigid_bodies", []) or [] if item.get("path")]
    mass_paths = [item.get("path") for item in physics.get("mass_api", []) or [] if item.get("path")]
    collision_paths = [item.get("path") for item in physics.get("colliders", []) or [] if item.get("path")]
    render_mesh_prims = []
    for item in geometry.get("mesh_prims", []) or []:
        mesh_path = item.get("path")
        if not mesh_path:
            continue
        is_auxiliary_mesh, _ = _classify_mesh_role(mesh_path)
        if not is_auxiliary_mesh:
            render_mesh_prims.append(mesh_path)
    material_bound_prims = sorted({item.get("target_prim") for item in materials.get("bindings", []) or [] if item.get("target_prim")})

    basis: List[str] = []
    pattern_class = "unknown"
    confidence = 0.35

    mesh_count = len(render_mesh_prims)
    rigid_count = len(rigid_body_paths)
    collision_count = len(collision_paths)
    has_materials = bool(material_bound_prims)

    if mesh_count == 0 and (rigid_count or collision_count or mass_paths):
        pattern_class = "physics_present_but_geometry_missing"
        basis.append("physics schemas detected while geometry.mesh_prims is empty")
        confidence = 0.88
    elif mesh_count == 0 and has_materials:
        pattern_class = "materialized_visual_asset_without_physics"
        basis.append("material bindings found without mesh prims in geometry section")
        confidence = 0.52
    elif mesh_count == 1 and rigid_count == 1:
        pattern_class = "single_mesh_single_body"
        basis.append("one render mesh and one rigid body path found")
        confidence = 0.9
    elif mesh_count > 1 and rigid_count <= 1 and collision_count >= 1:
        pattern_class = "multi_mesh_shared_body"
        basis.append("multiple render meshes share zero or one rigid body path")
        confidence = 0.82
    elif mesh_count >= 1 and rigid_count >= 1 and collision_count >= 1:
        pattern_class = "root_body_with_mesh_colliders"
        basis.append("render meshes, rigid bodies, and collider paths are all present")
        confidence = 0.78
    elif mesh_count >= 1 and has_materials and rigid_count == 0 and collision_count == 0:
        pattern_class = "materialized_visual_asset_without_physics"
        basis.append("render meshes and material bindings found without physics schemas")
        confidence = 0.9
    elif mesh_count >= 1 and not has_materials and rigid_count == 0:
        pattern_class = "mesh_only_visual_asset"
        basis.append("render meshes found without materials and without physics schemas")
        confidence = 0.78
    else:
        basis.append("structure pattern does not match a stronger heuristic bucket")

    physics_root = rigid_body_paths[0] if rigid_body_paths else None
    mass_owner = mass_paths[0] if mass_paths else None
    if physics_root:
        basis.append(f"physics_root selected from first rigid body path: {physics_root}")
    if mass_owner:
        basis.append(f"mass_owner selected from first mass path: {mass_owner}")
    if summary.get("mesh_count") != mesh_count:
        basis.append("geometry.mesh_prims count differs from summary.mesh_count")

    return {
        "pattern_class": pattern_class,
        "physics_root": physics_root,
        "mass_owner": mass_owner,
        "collision_prims": collision_paths,
        "render_mesh_prims": render_mesh_prims,
        "material_bound_prims": material_bound_prims,
        "basis": basis,
        "confidence": confidence,
    }


def build_component_map(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build a mesh-centric flattened view for storage and downstream queries."""
    geometry = report.get("geometry", {}) or {}
    materials = report.get("materials", {}) or {}
    physics = report.get("physics", {}) or {}

    subsets = materials.get("subsets", []) or []
    bindings = materials.get("bindings", []) or []
    collider_entries = physics.get("colliders", []) or []
    physics_material_bindings = materials.get("physics_material_bindings", []) or []

    result: List[Dict[str, Any]] = []
    for mesh in geometry.get("mesh_prims", []) or []:
        mesh_path = mesh.get("path")
        if not mesh_path:
            continue
        is_auxiliary_mesh, mesh_role = _classify_mesh_role(mesh_path)

        direct_materials = sorted(
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
                if (item.get("subset_path") or "").startswith(f"{mesh_path}/") and item.get("bound_material")
            }
        )

        matching_colliders = [
            item for item in collider_entries
            if item.get("path") == mesh_path or str(item.get("path") or "").startswith(f"{mesh_path}/")
        ]
        matching_physics_materials = [
            item for item in physics_material_bindings
            if item.get("target_prim") == mesh_path or str(item.get("target_prim") or "").startswith(f"{mesh_path}/")
        ]
        collider_schema = matching_colliders[0].get("schema") if matching_colliders else None
        collider_approximation = None
        collision_enabled = None
        for item in matching_colliders:
            if item.get("approximation") is not None:
                collider_approximation = item.get("approximation")
            if item.get("collision_enabled") is not None:
                collision_enabled = item.get("collision_enabled")

        physics_material_param_summary = []
        for item in matching_physics_materials:
            parts = []
            for key in ("static_friction", "dynamic_friction", "restitution", "density"):
                if item.get(key) is not None:
                    parts.append(f"{key}={item.get(key)}")
            if parts:
                physics_material_param_summary.append(",".join(parts))
        result.append(
            {
                "mesh_path": mesh_path,
                "is_auxiliary_mesh": is_auxiliary_mesh,
                "mesh_role": mesh_role,
                "render_materials": direct_materials,
                "subset_materials": subset_materials,
                "has_collider": bool(matching_colliders),
                "collider_schema": collider_schema,
                "collider_approximation": collider_approximation,
                "collider_authored_approximation": collider_approximation,
                "collision_enabled": collision_enabled,
                "has_physics_material": bool(matching_physics_materials),
                "physics_materials": sorted(
                    {
                        item.get("material_path")
                        for item in matching_physics_materials
                        if item.get("material_path")
                    }
                ),
                "physics_material_param_summary": ";".join(physics_material_param_summary),
            }
        )
    return result


def extract_geometry_features(report: Dict[str, Any]) -> Dict[str, Any]:
    """Derive coarse geometry features from the already-computed bbox and meshes."""
    geometry = report.get("geometry", {}) or {}
    mesh_prims = geometry.get("mesh_prims", []) or []
    mesh_roles = []
    primary_mesh_prims = []
    auxiliary_mesh_prims = []
    for mesh in mesh_prims:
        mesh_path = mesh.get("path") or ""
        is_auxiliary_mesh, mesh_role = _classify_mesh_role(mesh_path)
        mesh_copy = dict(mesh)
        mesh_copy["is_auxiliary_mesh"] = is_auxiliary_mesh
        mesh_copy["mesh_role"] = mesh_role
        mesh_roles.append(mesh_copy)
        if is_auxiliary_mesh:
            auxiliary_mesh_prims.append(mesh_copy)
        else:
            primary_mesh_prims.append(mesh_copy)

    analysis_meshes = primary_mesh_prims or mesh_roles
    world_bbox = (((geometry.get("bbox", {}) or {}).get("world", {})) or {})
    world_size = world_bbox.get("size")
    world_center = world_bbox.get("center")
    world_min = world_bbox.get("min")

    mesh_count = len(mesh_prims)
    primary_mesh_count = len(primary_mesh_prims)
    auxiliary_mesh_count = len(auxiliary_mesh_prims)
    points_count_total = sum(int(item.get("points_count") or 0) for item in analysis_meshes)
    face_count_total = sum(int(item.get("face_vertex_counts_count") or 0) for item in analysis_meshes)

    max_dimension = None
    min_dimension = None
    aspect_ratio_hint = "unknown"
    dimension_order = None
    volume_estimate_bbox = None
    is_ground_contact_likely = None

    if isinstance(world_size, list) and len(world_size) == 3:
        size_values = [_safe_float(item) or 0.0 for item in world_size]
        max_dimension = max(size_values)
        min_dimension = min(size_values)
        axis_names = ["length", "width", "height"]
        dimension_order = [axis_names[index] for index in sorted(range(3), key=lambda idx: size_values[idx], reverse=True)]
        volume_estimate_bbox = size_values[0] * size_values[1] * size_values[2]
        if max_dimension > 0:
            if min_dimension / max_dimension < 0.1:
                aspect_ratio_hint = "flat"
            elif size_values[2] > max(size_values[0], size_values[1]) * 1.5:
                aspect_ratio_hint = "tall"
            elif sorted(size_values)[1] > 0 and max_dimension / sorted(size_values)[1] > 2.0:
                aspect_ratio_hint = "elongated"
            else:
                aspect_ratio_hint = "compact"

        if isinstance(world_min, list) and len(world_min) == 3:
            min_z = _safe_float(world_min[2])
            if min_z is not None:
                tolerance = max(max_dimension or 0.0, 1.0) * 0.05
                is_ground_contact_likely = abs(min_z) <= max(1e-3, tolerance)

    return {
        "world_bbox_size": world_size if isinstance(world_size, list) else None,
        "world_bbox_center": world_center if isinstance(world_center, list) else None,
        "dimension_order": dimension_order,
        "max_dimension": max_dimension,
        "min_dimension": min_dimension,
        "aspect_ratio_hint": aspect_ratio_hint,
        "volume_estimate_bbox": volume_estimate_bbox,
        "is_ground_contact_likely": is_ground_contact_likely,
        "is_multi_mesh": len(analysis_meshes) > 1,
        "mesh_count": mesh_count,
        "primary_mesh_count": primary_mesh_count,
        "auxiliary_mesh_count": auxiliary_mesh_count,
        "points_count_total": points_count_total,
        "face_count_total": face_count_total,
        "bbox_may_include_auxiliary_mesh": auxiliary_mesh_count > 0,
        "shape_hints": geometry.get("shape_hints", {}) or {},
    }


def extract_physics_values(report: Dict[str, Any]) -> Dict[str, Any]:
    """Collect raw physics-related values into a stable container structure."""
    physics = report.get("physics", {}) or {}
    materials = report.get("materials", {}) or {}

    def _paths(items: List[Dict[str, Any]]) -> List[str]:
        return [item.get("path") for item in items if item.get("path")]

    def _collect_value_arrays(items: List[Dict[str, Any]], key: str) -> List[Any]:
        values = []
        for item in items:
            value = item.get(key)
            if value is not None:
                values.append(value)
        return values

    rigid_bodies = physics.get("rigid_bodies", []) or []
    colliders = physics.get("colliders", []) or []
    mass_api = physics.get("mass_api", []) or []
    physics_material_bindings = materials.get("physics_material_bindings", []) or []

    collision_approximations: List[Any] = []
    for item in colliders:
        approximation = item.get("approximation")
        if approximation is not None:
            collision_approximations.append(approximation)

    physics_material_params: List[Dict[str, Any]] = []
    for item in physics_material_bindings:
        params = {
            "target_prim": item.get("target_prim"),
            "material_path": item.get("material_path"),
            "binding_purpose": item.get("binding_purpose"),
            "static_friction": item.get("static_friction"),
            "dynamic_friction": item.get("dynamic_friction"),
            "restitution": item.get("restitution"),
            "density": item.get("density"),
        }
        physics_material_params.append(params)

    collision_enabled_values = _collect_value_arrays(colliders, "collision_enabled")

    return {
        "has_rigid_body": bool(rigid_bodies),
        "has_collision": bool(colliders),
        "has_mass": bool(mass_api),
        "has_physics_material": bool(physics_material_bindings),
        "rigid_body_paths": _paths(rigid_bodies),
        "collision_paths": _paths(colliders),
        "mass_paths": _paths(mass_api),
        "mass_values": _collect_value_arrays(mass_api, "mass"),
        "density_values": _collect_value_arrays(mass_api, "density"),
        "center_of_mass_values": _collect_value_arrays(mass_api, "center_of_mass"),
        "diagonal_inertia_values": _collect_value_arrays(mass_api, "diagonal_inertia"),
        "principal_axes_values": _collect_value_arrays(mass_api, "principal_axes"),
        "collision_approximations": collision_approximations,
        "collision_enabled_values": collision_enabled_values,
        "physics_material_params": physics_material_params,
    }


def infer_semantic_candidates(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Infer coarse semantic labels from identifier, display names, and filename."""
    keyword_map = {
        "forklift": "industrial_vehicle",
        "chair": "furniture_seating",
        "stool": "furniture_seating",
        "bench": "furniture_seating",
        "table": "furniture_surface",
        "desk": "furniture_surface",
        "cabinet": "storage_furniture",
        "shelf": "storage_furniture",
        "cone": "traffic_marker",
        "boat": "vehicle_marine",
        "vase": "decor_container",
        "bowl": "decor_container",
        "bin": "container_prop",
        "box": "container_prop",
    }

    candidates: List[Dict[str, Any]] = []
    signals = _collect_name_signals(report)
    for raw_value, source in signals:
        normalized = _normalize_text(raw_value)
        if not normalized:
            continue

        candidates.append(
            {
                "label": normalized,
                "source": source,
                "basis": [f"normalized candidate derived from {source}: {raw_value}"],
                "confidence": 0.92 if source == "metadata.asset_info.identifier" else 0.78,
            }
        )
        for keyword, mapped_label in keyword_map.items():
            if keyword in normalized:
                confidence = 0.86 if source == "metadata.asset_info.identifier" else 0.68
                candidates.append(
                    {
                        "label": mapped_label,
                        "source": "keyword_heuristic",
                        "basis": [f'keyword "{keyword}" matched normalized value "{normalized}" from {source}'],
                        "confidence": confidence,
                    }
                )

    return _dedupe_candidates(candidates, "label")


def infer_material_family_candidates(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Infer material family buckets from material names and binding targets."""
    materials = report.get("materials", {}) or {}
    component_map = build_component_map(report)
    signals: List[Tuple[str, str, float]] = []
    for entry in materials.get("material_prims", []) or []:
        if entry.get("name"):
            signals.append((str(entry.get("name")), "materials.material_prims.name", 0.72))
        if entry.get("path"):
            signals.append((str(entry.get("path")), "materials.material_prims.path", 0.46))
    for entry in materials.get("render_materials", []) or []:
        if entry.get("name"):
            signals.append((str(entry.get("name")), "materials.render_materials.name", 0.78))
        if entry.get("path"):
            signals.append((str(entry.get("path")), "materials.render_materials.path", 0.52))
    for entry in materials.get("subsets", []) or []:
        if entry.get("bound_material"):
            signals.append((str(entry.get("bound_material")), "materials.subsets.bound_material", 0.9))
    for entry in materials.get("bindings", []) or []:
        if entry.get("material_path"):
            signals.append((str(entry.get("material_path")), "materials.bindings.material_path", 0.68))
    for component in component_map:
        for material_name in (component.get("render_materials") or []) + (component.get("subset_materials") or []):
            if material_name:
                source = "component_map.subset_materials" if material_name in (component.get("subset_materials") or []) else "component_map.render_materials"
                base_confidence = 0.88 if source == "component_map.subset_materials" else 0.74
                signals.append((str(material_name), source, base_confidence))

    keyword_map = {
        "paint": "painted_surface",
        "painted": "painted_surface",
        "rubber": "rubber",
        "wood": "wood",
        "oak": "wood",
        "birch": "wood",
        "walnut": "wood",
        "pine": "wood",
        "steel": "metal",
        "metal": "metal",
        "iron": "metal",
        "brass": "metal",
        "aluminum": "metal",
        "aluminium": "metal",
        "chrome": "metal",
        "plastic": "plastic",
        "pvc": "plastic",
        "acrylic": "plastic",
        "poly": "plastic",
        "glass": "glass",
        "fabric": "fabric",
        "cloth": "fabric",
        "textile": "fabric",
        "linen": "fabric",
        "wool": "fabric",
        "leather": "fabric",
        "ceramic": "ceramic",
        "porcelain": "ceramic",
        "stone": "stone",
        "marble": "stone",
        "granite": "stone",
        "concrete": "stone",
        "paper": "paper_fiber",
        "cardboard": "paper_fiber",
    }

    candidates: List[Dict[str, Any]] = []
    for signal, source, base_confidence in signals:
        normalized = _normalize_text(signal)
        if not normalized:
            continue
        for keyword, label in keyword_map.items():
            if keyword in normalized:
                confidence = base_confidence
                if source == "materials.subsets.bound_material":
                    confidence = max(confidence, 0.9)
                elif source == "materials.render_materials.name":
                    confidence = max(confidence, 0.8)
                elif source.endswith(".path"):
                    confidence = min(confidence, 0.6)
                candidates.append(
                    {
                        "label": label,
                        "source": source,
                        "basis": [f'keyword "{keyword}" matched material signal "{signal}" from {source}'],
                        "confidence": confidence,
                    }
                )
    return _dedupe_candidates(candidates, "label")


def infer_collider_recommendation(report: Dict[str, Any], geometry_features: Dict[str, Any]) -> Dict[str, Any]:
    """Recommend collider strategy using only report-driven heuristics."""
    mesh_count = int(geometry_features.get("mesh_count") or 0)
    shape_hints = geometry_features.get("shape_hints", {}) or {}
    aspect_ratio_hint = geometry_features.get("aspect_ratio_hint")
    basis: List[str] = []

    if mesh_count == 0:
        return {
            "recommended": "none",
            "basis": ["geometry_features.mesh_count is 0"],
            "confidence": 0.98,
        }
    if mesh_count == 1 and shape_hints.get("is_box_like"):
        return {
            "recommended": "convexHull",
            "basis": ["single mesh detected", "shape_hints.is_box_like is true"],
            "confidence": 0.92,
        }
    if aspect_ratio_hint == "flat" or shape_hints.get("is_flat"):
        return {
            "recommended": "meshSimplified",
            "basis": ["geometry appears flat or plane-like"],
            "confidence": 0.8,
        }
    if mesh_count > 1:
        return {
            "recommended": "convexDecomposition",
            "basis": ["multiple mesh components detected"],
            "confidence": 0.86,
        }
    basis.append("fallback recommendation for mesh-bearing asset")
    return {
        "recommended": "convexHull",
        "basis": basis,
        "confidence": 0.62,
    }


def infer_physics_profile_candidates(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Infer coarse physics profiles from semantic, structure, and physics facts."""
    semantics = infer_semantic_candidates(report)
    material_families = infer_material_family_candidates(report)
    structure = extract_structure_pattern(report)
    geometry_features = extract_geometry_features(report)
    physics_values = extract_physics_values(report)

    semantic_labels = {item.get("label") for item in semantics}
    material_labels = {item.get("label") for item in material_families}
    candidates: List[Dict[str, Any]] = []

    def add(profile: str, basis: List[str], confidence: float, source: str = "rule_heuristic") -> None:
        candidates.append(
            {
                "profile": profile,
                "source": source,
                "basis": basis,
                "confidence": confidence,
            }
        )

    if ("forklift" in semantic_labels or "industrial_vehicle" in semantic_labels) and physics_values["has_rigid_body"] and physics_values["has_collision"] and physics_values["has_mass"]:
        add("wheeled_rigid_vehicle", ["semantic candidate indicates forklift/industrial_vehicle", "rigid body, collision, and mass are present"], 0.93)
    if {"furniture_seating", "furniture_surface", "storage_furniture"} & semantic_labels:
        if physics_values["has_rigid_body"] or physics_values["has_collision"]:
            add("static_furniture", ["semantic candidate maps to furniture", "physics schemas present"], 0.82)
        else:
            add("rigid_prop", ["semantic candidate maps to furniture", "physics schemas missing or weak"], 0.55)
    if ("traffic_marker" in semantic_labels) or ("cone" in semantic_labels):
        add("traffic_marker", ["semantic candidate indicates cone/traffic_marker"], 0.9)
    if ("decor_container" in semantic_labels) and not physics_values["has_collision"]:
        add("decorative_prop", ["semantic candidate indicates decor/container", "collision not detected"], 0.84)
    if ("container_prop" in semantic_labels):
        add("container_prop", ["semantic candidate indicates container or bin"], 0.86)
    if not candidates:
        base_basis = [f'structure pattern is {structure.get("pattern_class")}', f'aspect ratio hint is {geometry_features.get("aspect_ratio_hint")}']
        if physics_values["has_rigid_body"] or physics_values["has_collision"]:
            add("rigid_prop", base_basis + ["physics schemas present"], 0.6)
        else:
            add("unknown_generic_asset", base_basis + ["no stronger semantic/profile rule matched"], 0.4)
    if "metal" in material_labels and any(item.get("profile") == "rigid_prop" for item in candidates):
        add("rigid_prop", ["metal material family supports rigid prop interpretation"], 0.64)

    return _dedupe_candidates(candidates, "profile")


def build_simready_completeness(report: Dict[str, Any]) -> Dict[str, Any]:
    """Estimate completeness of the asset report for SimReady-style downstream use."""
    geometry_features = extract_geometry_features(report)
    physics_values = extract_physics_values(report)
    semantics = infer_semantic_candidates(report)
    materials = report.get("materials", {}) or {}
    basis: List[str] = []

    geometry_mesh_count = int(geometry_features.get("primary_mesh_count") or geometry_features.get("mesh_count") or 0)
    geometry_state = "present" if geometry_mesh_count > 0 and geometry_features.get("world_bbox_size") else ("partial" if geometry_mesh_count > 0 else "missing")
    render_material_state = "present" if materials.get("bindings") else ("partial" if materials.get("material_prims") else "missing")

    present_schema_count = sum(
        1 for value in (physics_values["has_rigid_body"], physics_values["has_collision"], physics_values["has_mass"]) if value
    )
    if present_schema_count >= 2:
        physics_schema_state = "present"
    elif present_schema_count == 1:
        physics_schema_state = "partial"
    else:
        physics_schema_state = "missing"

    if physics_values["mass_values"] or physics_values["density_values"] or physics_values["center_of_mass_values"] or physics_values["diagonal_inertia_values"]:
        physics_values_state = "present"
    elif physics_schema_state in {"present", "partial"}:
        physics_values_state = "partial"
    else:
        physics_values_state = "missing"

    physics_material_state = "present" if physics_values["has_physics_material"] else "missing"
    semantic_state = "present" if semantics else "missing"

    present_count = sum(
        1
        for state in (
            geometry_state,
            render_material_state,
            physics_schema_state,
            physics_values_state,
            physics_material_state,
            semantic_state,
        )
        if state == "present"
    )
    if present_count >= 4 and geometry_state == "present":
        overall = "high"
    elif present_count >= 2:
        overall = "medium"
    else:
        overall = "low"

    basis.extend(
        [
            f"geometry={geometry_state}",
            f"render_material={render_material_state}",
            f"physics_schema={physics_schema_state}",
            f"physics_values={physics_values_state}",
            f"physics_material={physics_material_state}",
            f"semantic_label={semantic_state}",
        ]
    )

    return {
        "geometry": geometry_state,
        "render_material": render_material_state,
        "physics_schema": physics_schema_state,
        "physics_values": physics_values_state,
        "physics_material": physics_material_state,
        "semantic_label": semantic_state,
        "overall": overall,
        "basis": basis,
    }


def build_review_flags(report: Dict[str, Any], knowledge: Dict[str, Any]) -> List[str]:
    """Produce concise review flags for manual triage and DB filtering."""
    flags: List[str] = []
    geometry_features = knowledge.get("geometry_features", {}) or {}
    physics_values = knowledge.get("physics_values", {}) or {}
    completeness = knowledge.get("simready_completeness", {}) or {}
    variant_role = ((knowledge.get("asset_variant_role", {}) or {}).get("value"))
    structure_pattern = knowledge.get("structure_pattern", {}) or {}

    if not geometry_features.get("world_bbox_size"):
        flags.append("bbox_missing")
    if not knowledge.get("semantic_candidates"):
        flags.append("missing_semantic_candidate")
    if completeness.get("physics_material") == "missing":
        flags.append("physics_material_binding_missing")
    if physics_values.get("has_mass") and not physics_values.get("mass_values"):
        flags.append("mass_schema_without_value")
    if physics_values.get("has_collision") and not physics_values.get("collision_approximations"):
        flags.append("collision_present_without_approximation")
    if physics_values.get("has_physics_material") and not any(
        any(param.get(key) is not None for key in ("static_friction", "dynamic_friction", "restitution", "density"))
        for param in (physics_values.get("physics_material_params") or [])
    ):
        flags.append("physics_material_params_missing")
    if variant_role == "base" and int(geometry_features.get("primary_mesh_count") or geometry_features.get("mesh_count") or 0) == 0:
        flags.append("base_variant_no_mesh_expected")
    if int(geometry_features.get("primary_mesh_count") or geometry_features.get("mesh_count") or 0) > 0 and not physics_values.get("has_collision") and not physics_values.get("has_rigid_body"):
        flags.append("visual_asset_without_physics")
    if structure_pattern.get("pattern_class") == "multi_mesh_shared_body":
        flags.append("multi_mesh_shared_rigidbody_review_needed")
    if geometry_features.get("bbox_may_include_auxiliary_mesh"):
        flags.append("bbox_may_include_auxiliary_mesh")

    return sorted(set(flags))


def build_knowledge_candidate(report: Dict[str, Any], variant_role_override: Optional[str] = None) -> Dict[str, Any]:
    """Build the final storage-friendly knowledge candidate document."""
    file_path = report.get("file", "")
    variant_role = (
        {
            "value": variant_role_override,
            "basis": "CLI override via --variant-role",
            "confidence": 1.0,
        }
        if variant_role_override and variant_role_override != "auto"
        else infer_asset_variant_role(file_path)
    )

    semantic_candidates = infer_semantic_candidates(report)
    material_family_candidates = infer_material_family_candidates(report)
    structure_pattern = extract_structure_pattern(report)
    component_map = build_component_map(report)
    geometry_features = extract_geometry_features(report)
    physics_values = extract_physics_values(report)
    collider_recommendation = infer_collider_recommendation(report, geometry_features)
    physics_profile_candidates = infer_physics_profile_candidates(report)
    simready_completeness = build_simready_completeness(report)

    knowledge = {
        "asset_id": _extract_asset_identity(report)[0],
        "asset_name_source": _extract_asset_identity(report)[1],
        "file": file_path,
        "asset_variant_role": variant_role,
        "semantic_candidates": semantic_candidates,
        "material_family_candidates": material_family_candidates,
        "structure_pattern": structure_pattern,
        "component_map": component_map,
        "geometry_features": geometry_features,
        "physics_values": physics_values,
        "collider_recommendation": collider_recommendation,
        "physics_profile_candidates": physics_profile_candidates,
        "simready_completeness": simready_completeness,
        "top_material_family_candidate": material_family_candidates[0]["label"] if material_family_candidates else "",
        "top_material_family_confidence": material_family_candidates[0]["confidence"] if material_family_candidates else "",
        "review_flags": [],
    }
    knowledge["review_flags"] = build_review_flags(report, knowledge)
    return knowledge
