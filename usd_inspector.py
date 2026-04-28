#!/usr/bin/env python3
"""USD asset inspector CLI.

This script extracts a structured JSON report from USD/USDZ/USDA/USDC assets
using the official pxr Python bindings only. It intentionally avoids Omniverse
Kit, Isaac Sim, and any GUI/runtime dependencies.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional, Tuple

from knowledge_candidate import build_knowledge_candidate

try:
    from pxr import Gf, Kind, Sdf, Tf, Usd, UsdGeom, UsdShade
except ImportError as exc:
    print(
        "Failed to import pxr USD Python bindings. Install a USD Python "
        "environment that provides pxr.",
        file=sys.stderr,
    )
    raise

try:
    from pxr import UsdPhysics  # type: ignore
except ImportError:
    UsdPhysics = None  # type: ignore

try:
    from pxr import PhysxSchema  # type: ignore
except ImportError:
    PhysxSchema = None  # type: ignore


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        result = float(value)
        if math.isfinite(result):
            return result
    except Exception:
        return None
    return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def _to_serializable(value: Any) -> Any:
    """Convert pxr and Python objects into JSON-safe values."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, (list, tuple, set)):
        return [_to_serializable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_serializable(val) for key, val in value.items()}

    if hasattr(value, "pathString"):
        return getattr(value, "pathString")
    if hasattr(value, "GetString"):
        try:
            return value.GetString()
        except Exception:
            pass
    if hasattr(value, "GetText"):
        try:
            return value.GetText()
        except Exception:
            pass
    if hasattr(value, "real") and hasattr(value, "imag"):
        return str(value)

    class_name = value.__class__.__name__
    if class_name in {"Vec2f", "Vec2d", "Vec2h", "Vec2i"}:
        return [value[0], value[1]]
    if class_name in {"Vec3f", "Vec3d", "Vec3h", "Vec3i"}:
        return [value[0], value[1], value[2]]
    if class_name in {"Vec4f", "Vec4d", "Vec4h", "Vec4i"}:
        return [value[0], value[1], value[2], value[3]]

    if isinstance(value, Sdf.AssetPath):
        return value.path or value.resolvedPath
    tf_token_type = getattr(Tf, "Token", None)
    if tf_token_type is not None and isinstance(value, tf_token_type):
        return str(value)

    return str(value)


def _range_to_dict(range3d: Any) -> Optional[Dict[str, Any]]:
    if range3d is None:
        return None
    try:
        if hasattr(range3d, "IsEmpty") and range3d.IsEmpty():
            return None
        minimum = range3d.GetMin()
        maximum = range3d.GetMax()
        size = maximum - minimum
        center = (minimum + maximum) * 0.5
        return {
            "min": _to_serializable(minimum),
            "max": _to_serializable(maximum),
            "size": _to_serializable(size),
            "center": _to_serializable(center),
        }
    except Exception:
        return None


def _bbox_to_dict(bbox: Any) -> Optional[Dict[str, Any]]:
    if bbox is None:
        return None
    try:
        aligned = bbox.ComputeAlignedRange()
        return _range_to_dict(aligned)
    except Exception:
        return None


def _union_ranges(ranges: Iterable[Any]) -> Optional[Dict[str, Any]]:
    min_values = None
    max_values = None

    for item in ranges:
        current = _range_to_dict(item)
        if not current:
            continue
        current_min = current["min"]
        current_max = current["max"]
        if min_values is None:
            min_values = list(current_min)
            max_values = list(current_max)
            continue
        min_values = [min(a, b) for a, b in zip(min_values, current_min)]
        max_values = [max(a, b) for a, b in zip(max_values, current_max)]

    if min_values is None or max_values is None:
        return None

    size = [max_v - min_v for min_v, max_v in zip(min_values, max_values)]
    center = [(min_v + max_v) * 0.5 for min_v, max_v in zip(min_values, max_values)]
    return {
        "min": min_values,
        "max": max_values,
        "size": size,
        "center": center,
    }


def _get_attr_value(attr: Any) -> Any:
    try:
        if attr and attr.IsValid() and attr.HasValue():
            return _to_serializable(attr.Get())
    except Exception:
        return None
    return None


def _asset_path_value_to_string(value: Any) -> Optional[str]:
    if isinstance(value, Sdf.AssetPath):
        return value.path or value.resolvedPath or None
    if isinstance(value, str):
        return value or None
    return None


def _collect_asset_path_strings(value: Any) -> List[str]:
    path = _asset_path_value_to_string(value)
    if path:
        return [path]
    if isinstance(value, (list, tuple)):
        paths: List[str] = []
        for item in value:
            item_path = _asset_path_value_to_string(item)
            if item_path:
                paths.append(item_path)
        return paths
    return []


def _is_external_or_absolute_asset_path(path: str) -> bool:
    if "://" in path:
        return True
    if os.path.isabs(path):
        return True
    return bool(re.match(r"^[A-Za-z]:[\\/]", path))


def _resolve_relative_asset_path(asset_path: str, anchor_dir: str) -> str:
    return os.path.abspath(os.path.normpath(os.path.join(anchor_dir, asset_path)))


def _get_kind(prim: Usd.Prim) -> Optional[str]:
    try:
        kind = Usd.ModelAPI(prim).GetKind()
        if kind:
            return str(kind)
    except Exception:
        return None
    return None


def _get_display_name(prim: Usd.Prim) -> Optional[str]:
    try:
        return prim.GetDisplayName() or None
    except Exception:
        return None


def _get_documentation(prim: Usd.Prim) -> Optional[str]:
    try:
        return prim.GetDocumentation() or None
    except Exception:
        return None


def _get_variant_sets(prim: Usd.Prim) -> List[Dict[str, Any]]:
    variants: List[Dict[str, Any]] = []
    try:
        variant_sets = prim.GetVariantSets()
        for name in variant_sets.GetNames():
            variant_set = variant_sets.GetVariantSet(name)
            variants.append(
                {
                    "prim": prim.GetPath().pathString,
                    "set_name": str(name),
                    "selection": variant_set.GetVariantSelection(),
                    "options": [str(item) for item in variant_set.GetVariantNames()],
                }
            )
    except Exception:
        pass
    return variants


def _collect_semantic_attrs(prim: Usd.Prim) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    grouped: Dict[str, Dict[str, Any]] = {}
    for attr in prim.GetAttributes():
        try:
            name = str(attr.GetName())
        except Exception:
            continue
        if not name.startswith("semantic:"):
            continue
        parts = name.split(":")
        if len(parts) < 4:
            continue
        key = ":".join(parts[:-1])
        field_name = parts[-1]
        entry = grouped.setdefault(
            key,
            {
                "path": prim.GetPath().pathString,
                "semantic_key": parts[1],
                "namespace": key,
                "semantic_data": None,
                "semantic_type": None,
            },
        )
        if field_name == "semanticData":
            entry["semantic_data"] = _get_attr_value(attr)
        elif field_name == "semanticType":
            entry["semantic_type"] = _get_attr_value(attr)

    for item in grouped.values():
        if item.get("semantic_data") is None and item.get("semantic_type") is None:
            continue
        entries.append(item)
    return entries


def _get_applied_schema_names(prim: Usd.Prim) -> List[str]:
    try:
        return [str(name) for name in prim.GetAppliedSchemas()]
    except Exception:
        return []


def _iter_prims(stage: Usd.Stage, max_prims: int = 0) -> List[Usd.Prim]:
    prims: List[Usd.Prim] = []
    for index, prim in enumerate(stage.Traverse()):
        if max_prims and index >= max_prims:
            break
        prims.append(prim)
    return prims


def open_stage(input_path: str) -> Usd.Stage:
    """Open the input asset as a USD stage or raise a clear error."""
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input file does not exist: {input_path}")

    try:
        stage = Usd.Stage.Open(input_path)
    except Exception as exc:
        raise RuntimeError(f"Failed to open stage: {exc}") from exc

    if stage is None:
        raise RuntimeError("Usd.Stage.Open returned None")
    return stage


def inspect_stage(stage: Usd.Stage, input_path: str, max_prims: int = 0) -> Dict[str, Any]:
    """Inspect stage-wide metadata and prim statistics."""
    prims = _iter_prims(stage, max_prims=max_prims)
    root_prims = [prim.GetPath().pathString for prim in stage.GetPseudoRoot().GetChildren()]
    default_prim = stage.GetDefaultPrim()
    traversal_limited = False
    if max_prims > 0:
        try:
            traversal_limited = len(prims) >= max_prims
        except Exception:
            traversal_limited = False

    try:
        up_axis = UsdGeom.GetStageUpAxis(stage)
        up_axis_value = str(up_axis) if up_axis else None
    except Exception:
        up_axis_value = None

    try:
        meters_per_unit = _safe_float(UsdGeom.GetStageMetersPerUnit(stage))
    except Exception:
        meters_per_unit = None

    kilograms_per_unit = None
    if UsdPhysics is not None:
        try:
            kilograms_per_unit = _safe_float(UsdPhysics.GetStageKilogramsPerUnit(stage))
        except Exception:
            kilograms_per_unit = None

    schema_counts: Dict[str, int] = {}
    kinds = set()
    prim_records: List[Dict[str, Any]] = []
    mesh_count = 0
    xform_count = 0
    material_count = 0
    subset_count = 0

    for prim in prims:
        type_name = prim.GetTypeName() or "<untyped>"
        schema_counts[type_name] = schema_counts.get(type_name, 0) + 1
        if prim.IsA(UsdGeom.Mesh):
            mesh_count += 1
        if prim.IsA(UsdGeom.Xform):
            xform_count += 1
        if prim.IsA(UsdShade.Material):
            material_count += 1
        if prim.IsA(UsdGeom.Subset):
            subset_count += 1

        kind_value = _get_kind(prim)
        if kind_value:
            kinds.add(kind_value)

        prim_records.append(
            {
                "path": prim.GetPath().pathString,
                "type_name": str(type_name),
                "active": bool(prim.IsActive()),
                "instanceable": bool(prim.IsInstanceable()),
                "kind": kind_value,
            }
        )

    return {
        "file": os.path.abspath(input_path),
        "stage": {
            "opened": True,
            "default_prim": default_prim.GetPath().pathString if default_prim else None,
            "up_axis": up_axis_value,
            "meters_per_unit": meters_per_unit,
            "kilograms_per_unit": kilograms_per_unit,
            "start_time_code": _safe_float(stage.GetStartTimeCode()),
            "end_time_code": _safe_float(stage.GetEndTimeCode()),
            "frames_per_second": _safe_float(stage.GetFramesPerSecond()),
        },
        "summary": {
            "prim_count": len(prims),
            "mesh_count": mesh_count,
            "xform_count": xform_count,
            "material_count": material_count,
            "subset_count": subset_count,
            "traversal_limited": traversal_limited,
            "schema_counts": dict(sorted(schema_counts.items())),
            "has_any_physics": False,
            "has_any_material_binding": False,
        },
        "root_prims": root_prims,
        "prims": prim_records,
        "metadata": {
            "asset_info": {},
            "kinds": sorted(kinds),
            "custom_data_hits": [],
            "variant_sets": [],
        },
    }


def _get_primvar_names(prim: Usd.Prim) -> List[str]:
    try:
        primvars_api = UsdGeom.PrimvarsAPI(prim)
        return [pv.GetPrimvarName() for pv in primvars_api.GetPrimvars()]
    except Exception:
        return []


def _detect_shape_hints(world_bbox: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    hints = {
        "is_box_like": False,
        "is_flat": False,
        "is_tall": False,
        "is_elongated": False,
    }
    if not world_bbox:
        return hints

    size = world_bbox.get("size") or []
    if len(size) != 3:
        return hints

    sx, sy, sz = [abs(_safe_float(item) or 0.0) for item in size]
    sorted_sizes = sorted([sx, sy, sz])
    smallest = sorted_sizes[0]
    middle = sorted_sizes[1]
    largest = sorted_sizes[2]
    if largest <= 0.0:
        return hints

    hints["is_flat"] = smallest > 0.0 and (smallest / largest) < 0.1
    hints["is_tall"] = sz > 0.0 and sz > max(sx, sy) * 1.5
    hints["is_elongated"] = middle > 0.0 and (largest / middle) > 2.0
    hints["is_box_like"] = largest > 0.0 and smallest > 0.0 and (smallest / largest) > 0.5
    return hints


def inspect_geometry(stage: Usd.Stage, max_prims: int = 0) -> Dict[str, Any]:
    """Inspect mesh geometry, extents, and aggregate bounding boxes."""
    included_purposes = [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy]
    bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), included_purposes, useExtentsHint=True)
    mesh_prims: List[Dict[str, Any]] = []
    extent_info: List[Dict[str, Any]] = []
    local_ranges = []
    world_ranges = []
    bbox_failures = []

    for prim in _iter_prims(stage, max_prims=max_prims):
        if not prim.IsA(UsdGeom.Mesh):
            continue

        mesh = UsdGeom.Mesh(prim)
        mesh_path = prim.GetPath().pathString
        points_count = None
        face_vertex_counts_count = None
        face_vertex_indices_count = None
        local_bbox = None
        world_bbox = None
        purpose = None
        visibility = None
        subdivision_scheme = None
        orientation = None

        try:
            points = mesh.GetPointsAttr().Get()
            points_count = len(points) if points is not None else None
        except Exception:
            points_count = None

        try:
            face_vertex_counts = mesh.GetFaceVertexCountsAttr().Get()
            face_vertex_counts_count = len(face_vertex_counts) if face_vertex_counts is not None else None
        except Exception:
            face_vertex_counts_count = None

        try:
            face_vertex_indices = mesh.GetFaceVertexIndicesAttr().Get()
            face_vertex_indices_count = len(face_vertex_indices) if face_vertex_indices is not None else None
        except Exception:
            face_vertex_indices_count = None

        extent_attr = mesh.GetExtentAttr()
        has_extent = bool(extent_attr and extent_attr.HasValue())
        has_normals = False
        try:
            normals_attr = mesh.GetNormalsAttr()
            has_normals = bool(normals_attr and normals_attr.HasValue())
        except Exception:
            has_normals = False

        primvar_names = _get_primvar_names(prim)
        has_st = "st" in primvar_names

        try:
            purpose = _to_serializable(UsdGeom.Imageable(prim).GetPurposeAttr().Get())
        except Exception:
            purpose = None

        try:
            visibility = _to_serializable(UsdGeom.Imageable(prim).GetVisibilityAttr().Get())
        except Exception:
            visibility = None

        try:
            subdivision_scheme = _to_serializable(mesh.GetSubdivisionSchemeAttr().Get())
        except Exception:
            subdivision_scheme = None

        try:
            orientation = _to_serializable(mesh.GetOrientationAttr().Get())
        except Exception:
            orientation = None

        try:
            local_bound = bbox_cache.ComputeLocalBound(prim)
            local_bbox = _bbox_to_dict(local_bound)
            if local_bound:
                local_ranges.append(local_bound.ComputeAlignedRange())
        except Exception as exc:
            bbox_failures.append({"prim": mesh_path, "space": "local", "error": str(exc)})

        try:
            world_bound = bbox_cache.ComputeWorldBound(prim)
            world_bbox = _bbox_to_dict(world_bound)
            if world_bound:
                world_ranges.append(world_bound.ComputeAlignedRange())
        except Exception as exc:
            bbox_failures.append({"prim": mesh_path, "space": "world", "error": str(exc)})

        mesh_record = {
            "path": mesh_path,
            "points_count": points_count,
            "face_vertex_counts_count": face_vertex_counts_count,
            "face_vertex_indices_count": face_vertex_indices_count,
            "has_extent": has_extent,
            "has_normals": has_normals,
            "has_st": has_st,
            "primvars": primvar_names,
            "purpose": purpose,
            "visibility": visibility,
            "subdivision_scheme": subdivision_scheme,
            "orientation": orientation,
            "bbox_local": local_bbox,
            "bbox_world": world_bbox,
        }
        mesh_prims.append(mesh_record)
        extent_info.append({
            "path": mesh_path,
            "has_extent": has_extent,
            "extent": _get_attr_value(extent_attr),
        })

    aggregate_local_bbox = _union_ranges(local_ranges)
    aggregate_world_bbox = _union_ranges(world_ranges)

    return {
        "mesh_prims": mesh_prims,
        "bbox": {
            "local": aggregate_local_bbox,
            "world": aggregate_world_bbox,
        },
        "extent_info": extent_info,
        "shape_hints": _detect_shape_hints(aggregate_world_bbox),
        "semantic_candidates": [],
        "collider_recommendation": {
            "recommended": None,
            "basis": "placeholder_for_pipeline_inference",
        },
        "bbox_failures": bbox_failures,
    }


def _binding_rel_to_material_path(rel: Any) -> Optional[str]:
    try:
        targets = rel.GetTargets()
        if targets:
            return targets[0].pathString
    except Exception:
        return None
    return None


def _binding_purpose_from_rel_name(name: str) -> Optional[str]:
    if not name:
        return None
    prefix = "material:binding:"
    if name == "material:binding":
        return "allPurpose"
    if name.startswith(prefix):
        return name[len(prefix):]
    return None


def _collect_material_relationship_bindings(prim: Usd.Prim, whether_on_subset: bool) -> List[Dict[str, Any]]:
    bindings: List[Dict[str, Any]] = []
    for rel in prim.GetRelationships():
        try:
            rel_name = rel.GetName()
        except Exception:
            continue
        if not str(rel_name).startswith("material:binding"):
            continue
        bindings.append(
            {
                "target_prim": prim.GetPath().pathString,
                "material_path": _binding_rel_to_material_path(rel),
                "binding_purpose": _binding_purpose_from_rel_name(str(rel_name)),
                "relationship_name": str(rel_name),
                "whether_on_subset": whether_on_subset,
            }
        )
    return bindings


def inspect_materials(stage: Usd.Stage, max_prims: int = 0) -> Dict[str, Any]:
    """Inspect UsdShade materials, bindings, and geom subset assignments."""
    material_prims: List[Dict[str, Any]] = []
    bindings: List[Dict[str, Any]] = []
    subsets: List[Dict[str, Any]] = []
    render_materials: List[Dict[str, Any]] = []
    physics_material_bindings: List[Dict[str, Any]] = []

    for prim in _iter_prims(stage, max_prims=max_prims):
        try:
            UsdShade.MaterialBindingAPI(prim)
        except Exception:
            pass

        if prim.IsA(UsdShade.Material):
            material = UsdShade.Material(prim)
            outputs = []
            try:
                outputs = [output.GetBaseName() for output in material.GetOutputs()]
            except Exception:
                outputs = []

            material_info = {
                "path": prim.GetPath().pathString,
                "name": prim.GetName(),
                "outputs": outputs,
                "base_material": None,
            }
            try:
                base_material, _ = material.GetBaseMaterial()
                if base_material:
                    material_info["base_material"] = base_material.GetPath().pathString
            except Exception:
                pass

            material_prims.append(material_info)
            render_materials.append(material_info)

        bindings.extend(_collect_material_relationship_bindings(prim, whether_on_subset=prim.IsA(UsdGeom.Subset)))

        if prim.IsA(UsdGeom.Subset):
            subset = UsdGeom.Subset(prim)
            family_name = None
            element_type = None
            indices_count = None
            try:
                family_name = subset.GetFamilyNameAttr().Get()
            except Exception:
                family_name = None
            try:
                element_type = subset.GetElementTypeAttr().Get()
            except Exception:
                element_type = None
            try:
                indices = subset.GetIndicesAttr().Get()
                indices_count = len(indices) if indices is not None else None
            except Exception:
                indices_count = None

            subset_bindings = _collect_material_relationship_bindings(prim, whether_on_subset=True)
            bound_material = subset_bindings[0]["material_path"] if subset_bindings else None
            subsets.append(
                {
                    "subset_path": prim.GetPath().pathString,
                    "familyName": _to_serializable(family_name),
                    "elementType": _to_serializable(element_type),
                    "indices_count": indices_count,
                    "bound_material": bound_material,
                }
            )

    for binding in bindings:
        purpose = binding.get("binding_purpose")
        if purpose and "physics" in purpose.lower():
            physics_material_bindings.append(binding)

    return {
        "material_prims": material_prims,
        "bindings": bindings,
        "subsets": subsets,
        "render_materials": render_materials,
        "physics_material_bindings": physics_material_bindings,
        "material_family_candidates": [],
    }


def inspect_asset_dependencies(stage: Usd.Stage, input_path: str, max_prims: int = 0) -> Dict[str, Any]:
    """Collect authored asset-path dependencies and flag missing relative files."""
    anchor_dir = os.path.dirname(os.path.abspath(input_path))
    dependencies: List[Dict[str, Any]] = []
    relative_dependencies: List[Dict[str, Any]] = []
    missing_relative_dependencies: List[Dict[str, Any]] = []
    seen = set()

    for prim in _iter_prims(stage, max_prims=max_prims):
        for attr in prim.GetAttributes():
            try:
                type_name = attr.GetTypeName()
                value = attr.Get()
            except Exception:
                continue
            is_asset_typed = type_name in {Sdf.ValueTypeNames.Asset, Sdf.ValueTypeNames.AssetArray}
            if not is_asset_typed and not isinstance(value, Sdf.AssetPath):
                continue
            asset_paths = _collect_asset_path_strings(value)
            if not asset_paths:
                continue
            for asset_path in asset_paths:
                is_relative = not _is_external_or_absolute_asset_path(asset_path)
                resolved_path = _resolve_relative_asset_path(asset_path, anchor_dir) if is_relative else None
                exists = bool(resolved_path and os.path.exists(resolved_path)) if is_relative else None
                record = {
                    "prim": prim.GetPath().pathString,
                    "attribute": attr.GetName(),
                    "asset_path": asset_path,
                    "is_relative": is_relative,
                    "resolved_path": resolved_path,
                    "exists": exists,
                }
                key = (record["prim"], record["attribute"], record["asset_path"])
                if key in seen:
                    continue
                seen.add(key)
                dependencies.append(record)
                if is_relative:
                    relative_dependencies.append(record)
                    if not exists:
                        missing_relative_dependencies.append(record)

    return {
        "all": dependencies,
        "relative": relative_dependencies,
        "missing_relative": missing_relative_dependencies,
        "relative_count": len(relative_dependencies),
        "missing_relative_count": len(missing_relative_dependencies),
    }


def _schema_applied(prim: Usd.Prim, schema_cls: Any) -> bool:
    try:
        api = schema_cls(prim)
        if hasattr(api, "GetPrim"):
            return bool(api.GetPrim()) and api.GetPrim().IsValid()
    except Exception:
        return False
    return False


def _append_if_schema(result_list: List[Dict[str, Any]], prim: Usd.Prim, schema_cls: Any, label: str) -> bool:
    if schema_cls is None:
        return False
    if not _schema_applied(prim, schema_cls):
        return False
    result_list.append({"path": prim.GetPath().pathString, "schema": label})
    return True


def _path_has_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _nearest_rigid_body_ancestor(prim: Usd.Prim, rigid_body_paths: List[str]) -> Optional[str]:
    current = prim
    while current and current.IsValid():
        current_path = current.GetPath().pathString
        if current_path in rigid_body_paths:
            return current_path
        current = current.GetParent()
    return None


def _nearest_physics_scene(stage: Usd.Stage, prim: Usd.Prim) -> Optional[str]:
    if UsdPhysics is None:
        return None
    try:
        current = prim
        while current and current.IsValid():
            if current.IsA(UsdPhysics.Scene):
                return current.GetPath().pathString
            current = current.GetParent()
    except Exception:
        return None

    try:
        scenes = [item for item in stage.Traverse() if item.IsA(UsdPhysics.Scene)]
        if len(scenes) == 1:
            return scenes[0].GetPath().pathString
    except Exception:
        return None
    return None


def inspect_physics(stage: Usd.Stage, max_prims: int = 0) -> Dict[str, Any]:
    """Inspect USD physics APIs and common joint schemas."""
    rigid_bodies: List[Dict[str, Any]] = []
    colliders: List[Dict[str, Any]] = []
    mass_api: List[Dict[str, Any]] = []
    articulations: List[Dict[str, Any]] = []
    joints: List[Dict[str, Any]] = []
    scenes: List[Dict[str, Any]] = []
    physics_schemas_detected = set()
    physx_entries: List[Dict[str, Any]] = []
    prims = _iter_prims(stage, max_prims=max_prims)

    joint_schema_names = [
        "Joint",
        "FixedJoint",
        "RevoluteJoint",
        "PrismaticJoint",
        "DistanceJoint",
        "SphericalJoint",
        "D6Joint",
    ]

    if UsdPhysics is not None:
        for prim in prims:
            if prim.IsA(UsdPhysics.Scene):
                scene = UsdPhysics.Scene(prim)
                gravity_direction = None
                gravity_magnitude = None
                try:
                    gravity_direction = _to_serializable(scene.GetGravityDirectionAttr().Get())
                except Exception:
                    gravity_direction = None
                try:
                    gravity_magnitude = _safe_float(scene.GetGravityMagnitudeAttr().Get())
                except Exception:
                    gravity_magnitude = None
                scenes.append(
                    {
                        "path": prim.GetPath().pathString,
                        "gravity_direction": gravity_direction,
                        "gravity_magnitude": gravity_magnitude,
                    }
                )
                physics_schemas_detected.add("UsdPhysics.Scene")

    for prim in prims:
        applied_names = _get_applied_schema_names(prim)
        for name in applied_names:
            lowered = name.lower()
            if "physics" in lowered or "physx" in lowered:
                physics_schemas_detected.add(name)

        if UsdPhysics is not None:
            rigid_body_applied = _schema_applied(prim, getattr(UsdPhysics, "RigidBodyAPI", None))
            if rigid_body_applied:
                rigid_body_api = UsdPhysics.RigidBodyAPI(prim)
                simulation_owner = None
                try:
                    targets = rigid_body_api.GetSimulationOwnerRel().GetTargets()
                    if targets:
                        simulation_owner = targets[0].pathString
                except Exception:
                    simulation_owner = None
                rigid_bodies.append(
                    {
                        "path": prim.GetPath().pathString,
                        "schema": "UsdPhysics.RigidBodyAPI",
                        "rigid_body_enabled": _get_attr_value(rigid_body_api.GetRigidBodyEnabledAttr()),
                        "kinematic_enabled": _get_attr_value(rigid_body_api.GetKinematicEnabledAttr()),
                        "starts_asleep": _get_attr_value(rigid_body_api.GetStartsAsleepAttr()),
                        "simulation_owner": simulation_owner,
                    }
                )
                physics_schemas_detected.add("UsdPhysics.RigidBodyAPI")
            collision_applied = _schema_applied(prim, getattr(UsdPhysics, "CollisionAPI", None))
            if collision_applied:
                collision_api = UsdPhysics.CollisionAPI(prim)
                mesh_collision_api = None
                approximation = None
                if prim.IsA(UsdGeom.Mesh) and _schema_applied(prim, getattr(UsdPhysics, "MeshCollisionAPI", None)):
                    mesh_collision_api = UsdPhysics.MeshCollisionAPI(prim)
                    try:
                        approximation = _to_serializable(mesh_collision_api.GetApproximationAttr().Get())
                    except Exception:
                        approximation = None
                colliders.append(
                    {
                        "path": prim.GetPath().pathString,
                        "schema": "UsdPhysics.CollisionAPI",
                        "collision_enabled": _get_attr_value(collision_api.GetCollisionEnabledAttr()),
                        "approximation": approximation,
                        "has_mesh_collision_api": bool(mesh_collision_api),
                    }
                )
                physics_schemas_detected.add("UsdPhysics.CollisionAPI")
            mass_applied = _schema_applied(prim, getattr(UsdPhysics, "MassAPI", None))
            if mass_applied:
                mass = UsdPhysics.MassAPI(prim)
                mass_api.append(
                    {
                        "path": prim.GetPath().pathString,
                        "schema": "UsdPhysics.MassAPI",
                        "mass": _get_attr_value(mass.GetMassAttr()),
                        "density": _get_attr_value(mass.GetDensityAttr()),
                        "center_of_mass": _get_attr_value(mass.GetCenterOfMassAttr()),
                        "diagonal_inertia": _get_attr_value(mass.GetDiagonalInertiaAttr()),
                        "principal_axes": _get_attr_value(mass.GetPrincipalAxesAttr()),
                    }
                )
                physics_schemas_detected.add("UsdPhysics.MassAPI")
            if _append_if_schema(articulations, prim, getattr(UsdPhysics, "ArticulationRootAPI", None), "UsdPhysics.ArticulationRootAPI"):
                physics_schemas_detected.add("UsdPhysics.ArticulationRootAPI")

            for joint_name in joint_schema_names:
                schema_cls = getattr(UsdPhysics, joint_name, None)
                if schema_cls is None:
                    continue
                try:
                    schema_obj = schema_cls(prim)
                    prim_obj = schema_obj.GetPrim() if hasattr(schema_obj, "GetPrim") else None
                    if prim_obj and prim_obj.IsValid() and prim.IsA(schema_cls):
                        joints.append({
                            "path": prim.GetPath().pathString,
                            "joint_type": f"UsdPhysics.{joint_name}",
                        })
                        physics_schemas_detected.add(f"UsdPhysics.{joint_name}")
                except Exception:
                    continue

        if PhysxSchema is not None:
            for attr_name in dir(PhysxSchema):
                if not attr_name.endswith("API"):
                    continue
                schema_cls = getattr(PhysxSchema, attr_name, None)
                if schema_cls is None:
                    continue
                if _schema_applied(prim, schema_cls):
                    entry = {"path": prim.GetPath().pathString, "schema": f"PhysxSchema.{attr_name}"}
                    physx_entries.append(entry)
                    physics_schemas_detected.add(f"PhysxSchema.{attr_name}")

    rigid_body_paths = [item.get("path") for item in rigid_bodies if item.get("path")]
    rigid_body_paths_set = set(rigid_body_paths)
    dynamic_collider_count = 0
    static_collider_count = 0
    for collider in colliders:
        collider_path = collider.get("path") or ""
        prim = stage.GetPrimAtPath(collider_path)
        rigid_body_ancestor = _nearest_rigid_body_ancestor(prim, rigid_body_paths) if prim and prim.IsValid() else None
        subtree_root = rigid_body_ancestor
        nested_under_other = False
        if rigid_body_ancestor:
            for other_path in rigid_body_paths_set:
                if other_path == rigid_body_ancestor:
                    continue
                if _path_has_prefix(rigid_body_ancestor, other_path):
                    nested_under_other = True
                    break
        body_type = "dynamic_or_kinematic" if rigid_body_ancestor else "static"
        if body_type == "static":
            static_collider_count += 1
        else:
            dynamic_collider_count += 1
        collider["rigid_body_ancestor"] = rigid_body_ancestor
        collider["body_type"] = body_type
        collider["is_static_collider"] = body_type == "static"
        collider["is_rigid_body_root"] = collider_path in rigid_body_paths_set
        collider["nested_rigid_body_root"] = nested_under_other
        collider["simulation_owner"] = _nearest_physics_scene(stage, prim) if prim and prim.IsValid() else None

    return {
        "scenes": scenes,
        "rigid_bodies": rigid_bodies,
        "colliders": colliders,
        "mass_api": mass_api,
        "articulations": articulations,
        "joints": joints,
        "physx": physx_entries,
        "static_collider_count": static_collider_count,
        "dynamic_collider_count": dynamic_collider_count,
        "physics_schemas_detected": sorted(physics_schemas_detected),
        "physics_profile_candidates": [],
    }


def inspect_metadata(stage: Usd.Stage, max_prims: int = 0) -> Dict[str, Any]:
    """Collect stable metadata and semantic hints for downstream analysis."""
    asset_info: Dict[str, Any] = {}
    kinds = set()
    custom_data_hits: List[Dict[str, Any]] = []
    variant_sets: List[Dict[str, Any]] = []
    docs: List[Dict[str, Any]] = []
    display_names: List[Dict[str, Any]] = []
    model_metadata: List[Dict[str, Any]] = []
    semantic_entries: List[Dict[str, Any]] = []

    default_prim = stage.GetDefaultPrim()
    if default_prim:
        try:
            asset_info = _to_serializable(default_prim.GetAssetInfo()) or {}
        except Exception:
            asset_info = {}

    for prim in _iter_prims(stage, max_prims=max_prims):
        kind_value = _get_kind(prim)
        if kind_value:
            kinds.add(kind_value)

        custom_data = None
        try:
            custom_data = prim.GetCustomData()
        except Exception:
            custom_data = None
        if custom_data:
            custom_data_hits.append(
                {
                    "path": prim.GetPath().pathString,
                    "keys": sorted([str(key) for key in custom_data.keys()]),
                    "data": _to_serializable(custom_data),
                }
            )

        documentation = _get_documentation(prim)
        if documentation:
            docs.append({"path": prim.GetPath().pathString, "documentation": documentation})

        display_name = _get_display_name(prim)
        if display_name:
            display_names.append({"path": prim.GetPath().pathString, "display_name": display_name})

        variant_sets.extend(_get_variant_sets(prim))
        semantic_entries.extend(_collect_semantic_attrs(prim))

        try:
            model_api = Usd.ModelAPI(prim)
            if model_api:
                model_metadata.append(
                    {
                        "path": prim.GetPath().pathString,
                        "kind": kind_value,
                        "asset_name": _to_serializable(model_api.GetAssetName()),
                        "asset_identifier": _to_serializable(model_api.GetAssetIdentifier()),
                    }
                )
        except Exception:
            pass

    return {
        "asset_info": asset_info,
        "kinds": sorted(kinds),
        "custom_data_hits": custom_data_hits,
        "variant_sets": variant_sets,
        "documentation": docs,
        "display_names": display_names,
        "model_metadata": model_metadata,
        "semantic_entries": semantic_entries,
        "semantic_candidates": [],
    }


def build_issues(report: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Build lightweight inspection warnings without interrupting execution."""
    issues: List[str] = []
    notes: List[str] = []

    stage_info = report.get("stage", {})
    summary = report.get("summary", {})
    geometry = report.get("geometry", {})
    materials = report.get("materials", {})
    physics = report.get("physics", {})
    asset_dependencies = report.get("asset_dependencies", {})

    if not stage_info.get("default_prim"):
        issues.append("Stage has no defaultPrim.")
    if (summary.get("mesh_count") or 0) == 0:
        issues.append("Stage contains no Mesh prims.")
    if (summary.get("mesh_count") or 0) > 0 and not materials.get("bindings"):
        issues.append("Stage has Mesh prims but no material bindings were found.")
    if (summary.get("mesh_count") or 0) > 0 and not physics.get("physics_schemas_detected"):
        issues.append("Stage has Mesh prims but no physics schemas were detected.")
    if physics.get("colliders") and not physics.get("mass_api"):
        issues.append("Collider prims detected but no MassAPI detected.")
    if (summary.get("material_count") or 0) > 1 and not materials.get("subsets"):
        issues.append("Multiple materials found but no GeomSubset-based assignments were detected.")
    if geometry.get("bbox_failures"):
        issues.append("One or more bbox computations failed; inspect geometry.bbox_failures.")
    if asset_dependencies.get("missing_relative"):
        issues.append("One or more relative asset dependencies are missing; inspect asset_dependencies.missing_relative.")

    if geometry.get("shape_hints"):
        notes.append("shape_hints are heuristic only and should be validated in downstream pipeline logic.")
    if physics.get("physx"):
        notes.append("PhysX schemas were detected; behavior may depend on environment-specific extensions.")
    if report.get("summary", {}).get("traversal_limited"):
        notes.append("Prim traversal may be truncated by --max-prims.")
    elif report.get("summary", {}).get("prim_count") and report.get("summary", {}).get("prim_count") == 0:
        notes.append("Stage traversal returned zero prims.")

    return issues, notes


def build_detailed_report(stage: Usd.Stage, input_path: str, max_prims: int = 0) -> Dict[str, Any]:
    """Build the existing detailed inspector report from a USD stage."""
    report = inspect_stage(stage, input_path, max_prims=max_prims)
    report["geometry"] = inspect_geometry(stage, max_prims=max_prims)
    report["materials"] = inspect_materials(stage, max_prims=max_prims)
    report["physics"] = inspect_physics(stage, max_prims=max_prims)
    report["metadata"] = inspect_metadata(stage, max_prims=max_prims)
    report["asset_dependencies"] = inspect_asset_dependencies(stage, input_path, max_prims=max_prims)
    report["semantic_candidates"] = []
    report["physics_profile_candidates"] = report["physics"].get("physics_profile_candidates", [])
    report["collider_recommendation"] = report["geometry"].get("collider_recommendation", {})
    report["material_family_candidates"] = report["materials"].get("material_family_candidates", [])

    report["summary"]["has_any_physics"] = bool(report["physics"].get("physics_schemas_detected"))
    report["summary"]["has_any_material_binding"] = bool(report["materials"].get("bindings"))

    issues, notes = build_issues(report)
    report["issues"] = issues
    report["notes"] = notes
    return report


def _default_knowledge_output_path(input_usd: str) -> str:
    for suffix in (".usdz", ".usdc", ".usda", ".usd"):
        if input_usd.lower().endswith(suffix):
            return input_usd[: -len(suffix)] + ".knowledge_candidate.json"
    return input_usd + ".knowledge_candidate.json"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect USD/USDZ/USDA/USDC assets and emit JSON.")
    parser.add_argument("input_usd", help="Path to input USD/USDZ/USDA/USDC file")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--output", help="Write JSON report to file")
    parser.add_argument("--max-prims", type=int, default=0, help="Limit prim traversal count; 0 means unlimited")
    parser.add_argument("--emit-knowledge-candidate", action="store_true", help="Generate a second-layer knowledge candidate JSON")
    parser.add_argument("--knowledge-output", help="Write knowledge candidate JSON to file")
    parser.add_argument("--inline-knowledge-candidate", action="store_true", help="Attach knowledge_candidate to the detailed report output")
    parser.add_argument(
        "--variant-role",
        default="auto",
        choices=["auto", "main", "base", "inst", "inst_base", "unknown"],
        help="Override asset variant role; default auto infers from filename",
    )
    args = parser.parse_args(argv)

    try:
        stage = open_stage(args.input_usd)
    except Exception as exc:
        error_report = {
            "file": os.path.abspath(args.input_usd),
            "stage": {"opened": False, "error": str(exc)},
            "issues": [str(exc)],
            "notes": [],
        }
        json_text = json.dumps(error_report, indent=2 if args.pretty else None, ensure_ascii=False)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(json_text)
        else:
            print(json_text)
        return 1

    report = build_detailed_report(stage, args.input_usd, max_prims=max(0, args.max_prims))

    knowledge_candidate = None
    if args.emit_knowledge_candidate or args.inline_knowledge_candidate or args.knowledge_output:
        knowledge_candidate = build_knowledge_candidate(
            report,
            variant_role_override=None if args.variant_role == "auto" else args.variant_role,
        )
        if args.inline_knowledge_candidate:
            report["knowledge_candidate"] = knowledge_candidate

    json_text = json.dumps(report, indent=2 if args.pretty else None, ensure_ascii=False)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(json_text)
    else:
        print(json_text)

    if knowledge_candidate is not None and (args.emit_knowledge_candidate or args.knowledge_output):
        knowledge_text = json.dumps(knowledge_candidate, indent=2 if args.pretty else None, ensure_ascii=False)
        knowledge_output = args.knowledge_output or _default_knowledge_output_path(args.input_usd)
        with open(knowledge_output, "w", encoding="utf-8") as handle:
            handle.write(knowledge_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
