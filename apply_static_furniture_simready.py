#!/usr/bin/env python3
"""Apply static furniture SimReady physics settings to a USD asset."""

from __future__ import annotations

import argparse
import os
import re
import shutil
from typing import Any, List, Optional, Sequence

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

from static_furniture import load_json
from usd_inspector import inspect_asset_dependencies, open_stage


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OMNIVERSE_BUILTIN_RELATIVE_ASSETS = {
    "gltf/pbr.mdl",
}
FALLBACK_BUNDLED_RELATIVE_ASSETS = {
    "gltf/pbr.mdl": os.path.join(SCRIPT_DIR, "gltf", "pbr.mdl"),
}
EXPORT_FORMATS = {"auto", "usda", "usdc"}


def _default_output_path(input_usd: str, output_format: str = "auto") -> str:
    basename = os.path.basename(input_usd)
    stem, _ = os.path.splitext(basename)
    suffix = ".usdc" if output_format == "usdc" else ".usda"
    return os.path.join(os.getcwd(), f"{stem}.simready_static{suffix}")


def _apply_collision_to_prim(prim, approximation: str) -> None:
    collision_api = UsdPhysics.CollisionAPI.Apply(prim)
    collision_api.CreateCollisionEnabledAttr(True)
    mesh_collision_api = UsdPhysics.MeshCollisionAPI.Apply(prim)
    mesh_collision_api.CreateApproximationAttr(approximation)


def _as_vec3f(values) -> Optional[Gf.Vec3f]:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        return None
    parsed = []
    for value in values:
        try:
            parsed.append(float(value))
        except Exception:
            return None
    return Gf.Vec3f(parsed[0], parsed[1], parsed[2])


def _safe_float(value) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        result = float(value)
    except Exception:
        return None
    return result if result > 0.0 else None


def _bool_value(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "0", "false", "no", "off", "none"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return bool(value)


def _apply_reference_scale(stage, scale: float) -> bool:
    default_prim = stage.GetDefaultPrim()
    if not default_prim or not default_prim.IsValid():
        return False
    xformable = UsdGeom.Xformable(default_prim)
    scale_op = None
    for op in xformable.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeScale:
            scale_op = op
            break
    if scale_op is None:
        scale_op = xformable.AddScaleOp()
        scale_op.Set((scale, scale, scale))
        return True
    current = scale_op.Get()
    if current is None:
        scale_op.Set((scale, scale, scale))
        return True
    scale_op.Set((float(current[0]) * scale, float(current[1]) * scale, float(current[2]) * scale))
    return True


def _axis_vector(axis: str):
    axis = str(axis or "").upper()
    if axis == "X":
        return Gf.Vec3d(1.0, 0.0, 0.0)
    if axis == "Y":
        return Gf.Vec3d(0.0, 1.0, 0.0)
    return Gf.Vec3d(0.0, 0.0, 1.0)


def _apply_orientation_correction(stage, correction: dict) -> bool:
    default_prim = stage.GetDefaultPrim()
    if not default_prim or not default_prim.IsValid():
        return False
    axis = correction.get("axis")
    degrees = _safe_float(correction.get("degrees"))
    if not axis or degrees is None:
        return False
    target_up_axis = str(correction.get("set_stage_up_axis") or "").upper()
    if target_up_axis in {"X", "Y", "Z"}:
        UsdGeom.SetStageUpAxis(stage, target_up_axis)
    xformable = UsdGeom.Xformable(default_prim)
    orient_op = None
    for op in xformable.GetOrderedXformOps():
        if op.GetOpName() == "xformOp:orient":
            orient_op = op
            break
    rotation = Gf.Rotation(_axis_vector(axis), degrees)
    correction_quat = Gf.Quatf(rotation.GetQuat())
    if orient_op is None:
        orient_op = xformable.AddOrientOp()
        orient_op.Set(correction_quat)
        return True
    current = orient_op.Get()
    if current is None:
        orient_op.Set(correction_quat)
        return True
    orient_op.Set(correction_quat * current)
    return True


def _stage_bbox_size_cm(stage) -> Optional[List[float]]:
    default_prim = stage.GetDefaultPrim()
    if not default_prim or not default_prim.IsValid():
        return None
    bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_], useExtentsHint=True)
    bbox = bbox_cache.ComputeWorldBound(default_prim).ComputeAlignedRange()
    if bbox.IsEmpty():
        return None
    meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
    scale_to_cm = float(meters_per_unit) * 100.0
    return [float(value) * scale_to_cm for value in bbox.GetSize()]


def _default_prim_local_bbox_center(stage) -> Optional[Gf.Vec3f]:
    default_prim = stage.GetDefaultPrim()
    if not default_prim or not default_prim.IsValid():
        return None
    bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_], useExtentsHint=True)
    bbox = bbox_cache.ComputeLocalBound(default_prim).ComputeAlignedRange()
    if bbox.IsEmpty():
        return None
    center = bbox.GetMidpoint()
    return Gf.Vec3f(float(center[0]), float(center[1]), float(center[2]))


def _apply_center_of_mass(stage, authoring: dict) -> Optional[List[float]]:
    default_prim = stage.GetDefaultPrim()
    if not default_prim or not default_prim.IsValid():
        return None

    policy = str(authoring.get("center_of_mass_policy") or "bbox_center").strip().lower()
    if policy in {"", "none", "unset"}:
        return None

    center = None
    if policy in {"explicit", "authored"}:
        center = _as_vec3f(authoring.get("center_of_mass"))
    elif policy == "bbox_center":
        center = _default_prim_local_bbox_center(stage)

    if center is None:
        return None

    mass_api = UsdPhysics.MassAPI.Apply(default_prim)
    mass_api.CreateCenterOfMassAttr(center)
    return [float(center[0]), float(center[1]), float(center[2])]


def _source_bbox_size_cm(recommendation: dict) -> Optional[List[float]]:
    for container in (recommendation.get("asset", {}) or {}, recommendation.get("recommendation", {}) or {}):
        size = container.get("size", {}) or {}
        bbox_size = size.get("bbox_size")
        if isinstance(bbox_size, list) and len(bbox_size) == 3:
            values = [_safe_float(item) for item in bbox_size]
            if all(value is not None for value in values):
                return [float(value) for value in values]
        bbox = size.get("bbox", {}) or {}
        candidate = bbox.get("size") if isinstance(bbox, dict) else None
        if isinstance(candidate, list) and len(candidate) == 3:
            values = [_safe_float(item) for item in candidate]
            if all(value is not None for value in values):
                return [float(value) for value in values]
    return None


def _apply_expected_orientation(size_cm: Sequence[float], correction: dict) -> List[float]:
    axis = str((correction or {}).get("axis") or "").upper()
    degrees = _safe_float((correction or {}).get("degrees"))
    if axis == "X" and degrees is not None and abs(abs(degrees) - 90.0) <= 1e-3:
        return [float(size_cm[0]), float(size_cm[2]), float(size_cm[1])]
    if axis == "Y" and degrees is not None and abs(abs(degrees) - 90.0) <= 1e-3:
        return [float(size_cm[2]), float(size_cm[1]), float(size_cm[0])]
    if axis == "Z" and degrees is not None and abs(abs(degrees) - 90.0) <= 1e-3:
        return [float(size_cm[1]), float(size_cm[0]), float(size_cm[2])]
    return [float(value) for value in size_cm]


def _expected_authored_bbox_size_cm(recommendation: dict, authoring: dict) -> Optional[List[float]]:
    source_size = _source_bbox_size_cm(recommendation)
    if not source_size:
        return None
    expected = source_size
    if authoring.get("apply_orientation_correction"):
        expected = _apply_expected_orientation(expected, authoring.get("orientation_correction") or {})
    scale = _safe_float(authoring.get("suggested_uniform_scale")) if authoring.get("apply_reference_scale") else None
    if scale is not None:
        expected = [value * scale for value in expected]
    return expected


def _recommendation_stage_size(recommendation: dict) -> dict:
    for container in (recommendation.get("asset", {}) or {}, recommendation.get("recommendation", {}) or {}):
        size = container.get("size", {}) or {}
        if isinstance(size, dict) and size:
            return size
    return {}


def _recommendation_stage_metadata(recommendation: dict) -> dict:
    size = _recommendation_stage_size(recommendation)
    bbox = size.get("bbox", {}) if isinstance(size, dict) else {}
    metadata = bbox if isinstance(bbox, dict) else {}
    return {
        "stage_meters_per_unit": size.get("stage_meters_per_unit") or metadata.get("stage_meters_per_unit"),
        "stage_up_axis": size.get("stage_up_axis") or metadata.get("stage_up_axis"),
    }


def build_simready_expectations(
    recommendation: dict,
    *,
    source_usd: Optional[str] = None,
    output_usd: Optional[str] = None,
    authoring_overrides: Optional[dict] = None,
) -> dict:
    recommendation_body = recommendation.get("recommendation", {}) or {}
    authoring = dict(recommendation_body.get("authoring", {}) or {})
    if authoring_overrides:
        authoring.update(authoring_overrides)

    size = _recommendation_stage_size(recommendation)
    stage_metadata = _recommendation_stage_metadata(recommendation)
    orientation = authoring.get("orientation_correction") or {}
    expected_up_axis = str(orientation.get("set_stage_up_axis") or stage_metadata.get("stage_up_axis") or "").upper() or None
    source_bbox_size_cm = _source_bbox_size_cm(recommendation)
    expected_bbox_size_cm = _expected_authored_bbox_size_cm(recommendation, authoring)
    target_bbox = (
        authoring.get("reference_target_bbox")
        or (recommendation_body.get("size_recommendation", {}) or {}).get("reference_target_bbox")
    )

    return {
        "schema_version": 1,
        "source_usd": source_usd or authoring.get("source_usd_for_authoring"),
        "output_usd": output_usd,
        "units": {
            "bbox_size": "cm",
            "stage_distance": "stage_units",
            "source_meters_per_unit": stage_metadata.get("stage_meters_per_unit"),
            "expected_output_meters_per_unit": stage_metadata.get("stage_meters_per_unit"),
            "source_up_axis": stage_metadata.get("stage_up_axis"),
            "expected_output_up_axis": expected_up_axis,
        },
        "authoring": {
            "apply_reference_scale": bool(authoring.get("apply_reference_scale")),
            "suggested_uniform_scale": _safe_float(authoring.get("suggested_uniform_scale")),
            "apply_orientation_correction": bool(authoring.get("apply_orientation_correction")),
            "orientation_correction": orientation,
            "author_center_of_mass": _bool_value(authoring.get("author_center_of_mass"), True),
            "center_of_mass_policy": authoring.get("center_of_mass_policy") or "bbox_center",
        },
        "source_bbox_size_cm": source_bbox_size_cm,
        "expected_authored_bbox_size_cm": expected_bbox_size_cm,
        "expected_authored_bbox_size_cm_order_invariant": sorted(expected_bbox_size_cm) if expected_bbox_size_cm else None,
        "reference_target_bbox_cm": target_bbox,
        "tolerance": {
            "bbox_relative": 0.05,
            "bbox_absolute_cm": 0.05,
        },
        "validation_points": [
            "recommendation_authoring",
            "exported_usd_default_prim",
            "runtime_referenced_asset",
            "runtime_drop_actor",
            "render_artifacts",
        ],
    }


def _validate_authored_bbox_size(output_path: str, recommendation: dict, authoring: dict) -> List[str]:
    expected = _expected_authored_bbox_size_cm(recommendation, authoring)
    if not expected:
        return []
    stage = open_stage(output_path)
    actual = _stage_bbox_size_cm(stage)
    if not actual:
        return ["could not compute exported default prim bbox"]

    expected_sorted = sorted(expected)
    actual_sorted = sorted(actual)
    failures = []
    for index, (actual_value, expected_value) in enumerate(zip(actual_sorted, expected_sorted)):
        tolerance = max(abs(expected_value) * 0.05, 0.05)
        if abs(actual_value - expected_value) > tolerance:
            failures.append(
                "axis{} actual_cm={} expected_cm={} tolerance_cm={}".format(
                    index,
                    round(actual_value, 6),
                    round(expected_value, 6),
                    round(tolerance, 6),
                )
            )
    return failures


def _normalize_asset_key(asset_path: str) -> str:
    normalized = asset_path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _validator_relative_asset_path(asset_path: str) -> str:
    normalized = asset_path.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith(("./", "../", "/"))
        or "://" in normalized
    ):
        return normalized
    return f"./{normalized}"


def _asset_target_relative_path(asset_path: str, source_path: str) -> str:
    if not os.path.isabs(asset_path):
        return _validator_relative_asset_path(_normalize_asset_key(asset_path))
    parent_name = os.path.basename(os.path.dirname(source_path))
    basename = os.path.basename(source_path)
    if parent_name:
        return _validator_relative_asset_path(os.path.join(parent_name, basename))
    return _validator_relative_asset_path(basename)


def _asset_path_string(value: Any) -> str:
    if isinstance(value, Sdf.AssetPath):
        return value.path or value.resolvedPath or ""
    if isinstance(value, str):
        return value
    return ""


def _asset_path_matches_dependency(
    current_path: str,
    asset_path: str,
    source_path: str,
    relative_path: str,
) -> bool:
    if not current_path:
        return False
    current_key = _normalize_asset_key(current_path)
    asset_key = _normalize_asset_key(asset_path)
    relative_key = _normalize_asset_key(relative_path)
    if current_path in {asset_path, relative_path}:
        return True
    if current_key in {asset_key, relative_key}:
        return True
    if not source_path or "://" in current_path or "://" in source_path:
        return False
    try:
        return os.path.abspath(current_path) == os.path.abspath(source_path)
    except (TypeError, ValueError):
        return False


def _rewrite_asset_attr_to_relative(
    attr: Any,
    asset_path: str,
    source_path: str,
    relative_path: str,
) -> bool:
    current_value = attr.Get()
    current_path = _asset_path_string(current_value)
    if not _asset_path_matches_dependency(current_path, asset_path, source_path, relative_path):
        return False
    if current_path == relative_path:
        return False
    attr.Set(Sdf.AssetPath(relative_path))
    return True


def _copy_asset_dependencies(asset_dependencies: dict, output_path: str) -> List[str]:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    copied_paths: List[str] = []
    seen = set()
    for item in asset_dependencies.get("all", []) or []:
        asset_path = item.get("asset_path")
        source_path = item.get("resolved_path")
        if not asset_path:
            continue
        if item.get("is_relative"):
            if not source_path or not item.get("exists"):
                continue
        else:
            source_path = asset_path
            if "://" in asset_path or not os.path.exists(source_path):
                continue
        relative_path = _asset_target_relative_path(asset_path, source_path)
        target_path = os.path.abspath(os.path.normpath(os.path.join(output_dir, asset_path)))
        if not item.get("is_relative"):
            target_path = os.path.abspath(os.path.normpath(os.path.join(output_dir, relative_path)))
        key = (source_path, target_path)
        if key in seen:
            continue
        seen.add(key)
        if os.path.abspath(source_path) == target_path:
            continue
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.copy2(source_path, target_path)
        copied_paths.append(target_path)
    return copied_paths


def _copy_bundled_asset_dependencies(asset_dependencies: dict, output_path: str) -> List[str]:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    copied_paths: List[str] = []
    seen = set()
    for item in asset_dependencies.get("missing_relative", []) or []:
        asset_path = item.get("asset_path")
        if not asset_path:
            continue
        asset_key = _normalize_asset_key(asset_path)
        bundled_source = FALLBACK_BUNDLED_RELATIVE_ASSETS.get(asset_key)
        if not bundled_source or not os.path.exists(bundled_source):
            continue
        target_path = os.path.abspath(os.path.normpath(os.path.join(output_dir, asset_path)))
        if target_path in seen:
            continue
        seen.add(target_path)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.copy2(bundled_source, target_path)
        copied_paths.append(target_path)
    return copied_paths


def _remaining_missing_assets(asset_dependencies: dict, preserve_builtin_render_assets: bool) -> List[dict]:
    remaining = []
    for item in asset_dependencies.get("missing_relative", []) or []:
        asset_path = item.get("asset_path")
        if not asset_path:
            remaining.append(item)
            continue
        asset_key = _normalize_asset_key(asset_path)
        if preserve_builtin_render_assets and asset_key in OMNIVERSE_BUILTIN_RELATIVE_ASSETS:
            continue
        if asset_key in FALLBACK_BUNDLED_RELATIVE_ASSETS:
            continue
        remaining.append(item)
    return remaining


def _repair_missing_environment_light_textures(stage, asset_dependencies: dict) -> List[dict]:
    repairs: List[dict] = []
    for item in asset_dependencies.get("missing_relative", []) or []:
        prim_path = item.get("prim")
        attr_name = item.get("attribute")
        asset_path = item.get("asset_path")
        if attr_name not in {"inputs:texture:file", "texture:file"}:
            continue
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid() or prim.GetTypeName() != "DomeLight":
            continue
        attr = prim.GetAttribute(attr_name)
        if not attr or not attr.IsValid() or not attr.HasAuthoredValue():
            continue
        attr.Clear()
        repairs.append(
            {
                "prim": str(prim_path),
                "attribute": str(attr_name),
                "asset_path": str(asset_path),
                "action": "cleared_missing_dome_light_texture",
            }
        )
    return repairs


def _rewrite_asset_paths_to_relative(stage, asset_dependencies: dict, preserve_builtin_render_assets: bool) -> int:
    rewrites = 0
    for item in asset_dependencies.get("all", []) or []:
        prim_path = item.get("prim")
        attr_name = item.get("attribute")
        asset_path = item.get("asset_path")
        asset_key = _normalize_asset_key(asset_path)
        source_path = item.get("resolved_path") if item.get("is_relative") else asset_path
        if not prim_path or not attr_name or not asset_path or not source_path:
            continue
        if "://" in asset_path:
            continue
        if preserve_builtin_render_assets and asset_key in OMNIVERSE_BUILTIN_RELATIVE_ASSETS:
            continue
        if os.path.exists(source_path):
            relative_path = _asset_target_relative_path(asset_path, source_path)
        elif item.get("is_relative") and asset_key in FALLBACK_BUNDLED_RELATIVE_ASSETS:
            relative_path = _validator_relative_asset_path(asset_key)
        elif item.get("is_relative"):
            relative_path = _validator_relative_asset_path(asset_key)
        else:
            continue
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            continue
        attr = prim.GetAttribute(attr_name)
        if not attr or not attr.IsValid():
            continue
        if _rewrite_asset_attr_to_relative(attr, asset_path, source_path, relative_path):
            rewrites += 1
    return rewrites


def _asset_path_rewrite_map(asset_dependencies: dict, preserve_builtin_render_assets: bool) -> dict:
    replacements = {}
    for item in asset_dependencies.get("all", []) or []:
        asset_path = item.get("asset_path")
        asset_key = _normalize_asset_key(asset_path or "")
        source_path = item.get("resolved_path") if item.get("is_relative") else asset_path
        if not asset_path or not source_path or "://" in asset_path:
            continue
        if preserve_builtin_render_assets and asset_key in OMNIVERSE_BUILTIN_RELATIVE_ASSETS:
            continue
        if os.path.exists(source_path):
            relative_path = _asset_target_relative_path(asset_path, source_path)
        elif item.get("is_relative") and asset_key in FALLBACK_BUNDLED_RELATIVE_ASSETS:
            relative_path = _validator_relative_asset_path(asset_key)
        elif item.get("is_relative"):
            relative_path = _validator_relative_asset_path(asset_key)
        else:
            continue
        replacements[asset_path] = relative_path
        replacements[asset_key] = relative_path
        replacements[os.path.abspath(source_path)] = relative_path
    return replacements


def _export_format_for_output(output_path: str, requested_format: str) -> str | None:
    if requested_format not in EXPORT_FORMATS:
        raise ValueError(f"Unsupported output format: {requested_format}")
    lower_path = output_path.lower()
    extension_format = None
    if lower_path.endswith(".usda"):
        extension_format = "usda"
    elif lower_path.endswith(".usdc"):
        extension_format = "usdc"

    if requested_format == "auto":
        return extension_format
    if extension_format and extension_format != requested_format:
        raise ValueError(
            f"--output-format {requested_format} conflicts with output extension {os.path.splitext(output_path)[1]}"
        )
    return requested_format


def _export_stage(stage, output_path: str, output_format: str) -> str | None:
    export_format = _export_format_for_output(output_path, output_format)
    if export_format:
        stage.Export(output_path, args={"format": export_format})
        return export_format
    stage.Export(output_path)
    return None


def _rewrite_exported_stage_asset_paths(output_path: str, asset_dependencies: dict, preserve_builtin_render_assets: bool) -> int:
    stage = open_stage(output_path)
    rewrite_count = _rewrite_asset_paths_to_relative(stage, asset_dependencies, preserve_builtin_render_assets)
    if rewrite_count:
        stage.GetRootLayer().Save()
    return rewrite_count


def _rewrite_exported_usda_asset_paths(output_path: str, asset_dependencies: dict, preserve_builtin_render_assets: bool) -> int:
    try:
        with open(output_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except UnicodeDecodeError:
        return 0

    replacements = _asset_path_rewrite_map(asset_dependencies, preserve_builtin_render_assets)
    rewrite_count = 0

    def replace_asset(match):
        nonlocal rewrite_count
        value = match.group(1)
        replacement = replacements.get(value)
        if replacement is None:
            replacement = replacements.get(_normalize_asset_key(value))
        if replacement is None:
            return match.group(0)
        rewrite_count += 1
        return f"@{replacement}@"

    rewritten = re.sub(r"@([^@\n]+)@", replace_asset, text)
    if rewritten != text:
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(rewritten)
    return rewrite_count


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply static furniture SimReady settings from recommendation JSON.")
    parser.add_argument("input_usd", help="Path to source USD asset")
    parser.add_argument("recommendation_json", help="Path to recommendation JSON from recommend_static_furniture_simready.py")
    parser.add_argument(
        "--output",
        help="Path to write the authored USD; default is ./<name>.simready_static.usda, or .usdc with --output-format usdc",
    )
    parser.add_argument(
        "--output-format",
        choices=sorted(EXPORT_FORMATS),
        default="auto",
        help="USD export format. auto follows the output extension; usdc writes compact binary USD.",
    )
    parser.add_argument(
        "--allow-missing-assets",
        action="store_true",
        help="Export even when relative asset dependencies are missing in the source asset directory",
    )
    parser.add_argument(
        "--no-copy-relative-assets",
        action="store_true",
        help="Do not copy resolvable relative asset dependencies next to the output USD",
    )
    parser.add_argument(
        "--bundle-omniverse-builtin-mdl",
        action="store_true",
        help=(
            "Bundle fallback MDL files for Omniverse built-in modules such as gltf/pbr.mdl. "
            "By default these paths are preserved so Omniverse/Isaac Sim can use its native glTF PBR shader."
        ),
    )
    parser.add_argument(
        "--no-apply-reference-scale",
        action="store_true",
        help="Do not apply recommendation.authoring.suggested_uniform_scale to the default prim",
    )
    parser.add_argument(
        "--skip-size-validation",
        action="store_true",
        help="Skip post-export bbox validation against the recommendation scale/orientation",
    )
    parser.add_argument(
        "--author-rigid-body",
        action="store_true",
        help="Override recommendation.authoring.author_rigid_body and author a kinematic rigid body on the default prim",
    )
    parser.add_argument(
        "--author-center-of-mass",
        action="store_true",
        help="Author UsdPhysics.MassAPI centerOfMass on the default prim",
    )
    parser.add_argument(
        "--no-author-center-of-mass",
        action="store_true",
        help="Do not author UsdPhysics.MassAPI centerOfMass, even if the recommendation enables it",
    )
    parser.add_argument(
        "--center-of-mass-policy",
        choices=["bbox_center", "explicit", "none"],
        help="How to choose centerOfMass when authoring it. explicit uses recommendation.authoring.center_of_mass.",
    )
    args = parser.parse_args(argv)

    recommendation = load_json(args.recommendation_json)
    recommendation_body = recommendation.get("recommendation", {}) or {}
    authoring = dict(recommendation_body.get("authoring", {}) or {})
    if "author_center_of_mass" not in authoring:
        authoring["author_center_of_mass"] = True
    if args.author_rigid_body:
        authoring["author_rigid_body"] = True
    if args.author_center_of_mass:
        authoring["author_center_of_mass"] = True
    if args.no_author_center_of_mass:
        authoring["author_center_of_mass"] = False
    if args.center_of_mass_policy:
        authoring["center_of_mass_policy"] = args.center_of_mass_policy
    collision_plan = recommendation_body.get("collision_plan", {}) or {}
    target_mesh_paths = authoring.get("target_mesh_paths") or collision_plan.get("target_mesh_paths") or []
    approximation = authoring.get("approximation") or collision_plan.get("usd_approximation") or "convexHull"
    source_usd_for_authoring = authoring.get("source_usd_for_authoring") or args.input_usd
    output_path = args.output or _default_output_path(os.path.abspath(source_usd_for_authoring), args.output_format)

    stage = open_stage(source_usd_for_authoring)
    asset_dependencies = inspect_asset_dependencies(stage, source_usd_for_authoring)
    preserve_builtin_render_assets = not args.bundle_omniverse_builtin_mdl
    repaired_missing_assets = _repair_missing_environment_light_textures(stage, asset_dependencies)
    if repaired_missing_assets:
        asset_dependencies = inspect_asset_dependencies(stage, source_usd_for_authoring)
    bundled_paths: List[str] = []
    if not args.no_copy_relative_assets and args.bundle_omniverse_builtin_mdl:
        bundled_paths = _copy_bundled_asset_dependencies(asset_dependencies, output_path)
    missing_assets = _remaining_missing_assets(asset_dependencies, preserve_builtin_render_assets)
    if missing_assets and not args.allow_missing_assets:
        print("error: source USD has missing relative asset dependencies:")
        for item in missing_assets:
            print(f"  {item.get('prim')} {item.get('attribute')} -> {item.get('asset_path')}")
        print("Use --allow-missing-assets only if this incomplete output is intentional.")
        return 2

    applied_paths = []
    for mesh_path in target_mesh_paths:
        prim = stage.GetPrimAtPath(mesh_path)
        if not prim or not prim.IsValid():
            continue
        _apply_collision_to_prim(prim, approximation)
        applied_paths.append(str(mesh_path))

    if authoring.get("author_rigid_body"):
        default_prim = stage.GetDefaultPrim()
        if default_prim and default_prim.IsValid():
            rigid_body_api = UsdPhysics.RigidBodyAPI.Apply(default_prim)
            rigid_body_api.CreateRigidBodyEnabledAttr(True)
            rigid_body_api.CreateKinematicEnabledAttr(True)

    applied_orientation = False
    if authoring.get("apply_orientation_correction"):
        applied_orientation = _apply_orientation_correction(stage, authoring.get("orientation_correction") or {})

    applied_reference_scale = None
    if authoring.get("apply_reference_scale") and not args.no_apply_reference_scale:
        scale = _safe_float(authoring.get("suggested_uniform_scale"))
        if scale is not None and _apply_reference_scale(stage, scale):
            applied_reference_scale = scale

    authored_center_of_mass = None
    if _bool_value(authoring.get("author_rigid_body")) or _bool_value(authoring.get("author_center_of_mass"), True):
        authored_center_of_mass = _apply_center_of_mass(stage, authoring)

    rewritten_count = 0
    if not args.no_copy_relative_assets:
        rewritten_count = _rewrite_asset_paths_to_relative(stage, asset_dependencies, preserve_builtin_render_assets)
    try:
        export_format = _export_stage(stage, output_path, args.output_format)
    except ValueError as error:
        print(f"error: {error}")
        return 2
    file_rewritten_count = 0
    if not args.no_copy_relative_assets:
        if export_format == "usdc":
            file_rewritten_count = _rewrite_exported_stage_asset_paths(
                output_path,
                asset_dependencies,
                preserve_builtin_render_assets,
            )
        else:
            file_rewritten_count = _rewrite_exported_usda_asset_paths(
                output_path,
                asset_dependencies,
                preserve_builtin_render_assets,
            )
    copied_paths: List[str] = []
    if not args.no_copy_relative_assets:
        copied_paths = _copy_asset_dependencies(asset_dependencies, output_path)

    size_validation_failures = []
    if not args.skip_size_validation:
        size_validation_failures = _validate_authored_bbox_size(output_path, recommendation, authoring)
        if size_validation_failures:
            print("error: exported bbox size does not match the recommendation:")
            for failure in size_validation_failures:
                print(f"  {failure}")
            return 3

    print(output_path)
    if rewritten_count:
        print(f"rewrote {rewritten_count} asset paths to relative paths")
    if file_rewritten_count:
        print(f"normalized {file_rewritten_count} exported asset paths")
    if bundled_paths:
        print(f"copied {len(bundled_paths)} bundled asset dependencies")
    if copied_paths:
        print(f"copied {len(copied_paths)} relative asset dependencies")
    if repaired_missing_assets:
        print(f"auto-repaired {len(repaired_missing_assets)} missing environment light dependencies")
    if applied_reference_scale is not None:
        print(f"applied reference uniform scale {applied_reference_scale}")
    if applied_orientation:
        print("applied orientation correction")
    if authored_center_of_mass is not None:
        print(f"authored centerOfMass {authored_center_of_mass}")
    if not applied_paths:
        print("warning: no target mesh paths were authored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
