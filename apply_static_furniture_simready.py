#!/usr/bin/env python3
"""Apply static furniture SimReady physics settings to a USD asset."""

from __future__ import annotations

import argparse
import os
import re
import shutil
from typing import List, Optional

from pxr import Gf, Sdf, UsdGeom, UsdPhysics

from static_furniture import load_json
from usd_inspector import inspect_asset_dependencies, open_stage


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_RELATIVE_ASSETS = {
    "gltf/pbr.mdl": os.path.join(SCRIPT_DIR, "gltf", "pbr.mdl"),
}


def _default_output_path(input_usd: str) -> str:
    basename = os.path.basename(input_usd)
    stem, _ = os.path.splitext(basename)
    return os.path.join(os.getcwd(), f"{stem}.simready_static.usda")


def _apply_collision_to_prim(prim, approximation: str) -> None:
    collision_api = UsdPhysics.CollisionAPI.Apply(prim)
    collision_api.CreateCollisionEnabledAttr(True)
    mesh_collision_api = UsdPhysics.MeshCollisionAPI.Apply(prim)
    mesh_collision_api.CreateApproximationAttr(approximation)


def _safe_float(value) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        result = float(value)
    except Exception:
        return None
    return result if result > 0.0 else None


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


def _asset_target_relative_path(asset_path: str, source_path: str) -> str:
    if not os.path.isabs(asset_path):
        return _normalize_asset_key(asset_path)
    parent_name = os.path.basename(os.path.dirname(source_path))
    basename = os.path.basename(source_path)
    if parent_name:
        return os.path.join(parent_name, basename).replace("\\", "/")
    return basename


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


def _normalize_asset_key(asset_path: str) -> str:
    return asset_path.replace("\\", "/").lstrip("./")


def _copy_bundled_asset_dependencies(asset_dependencies: dict, output_path: str) -> List[str]:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    copied_paths: List[str] = []
    seen = set()
    for item in asset_dependencies.get("missing_relative", []) or []:
        asset_path = item.get("asset_path")
        if not asset_path:
            continue
        asset_key = _normalize_asset_key(asset_path)
        bundled_source = BUNDLED_RELATIVE_ASSETS.get(asset_key)
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


def _remaining_missing_assets(asset_dependencies: dict) -> List[dict]:
    remaining = []
    for item in asset_dependencies.get("missing_relative", []) or []:
        asset_path = item.get("asset_path")
        if asset_path and _normalize_asset_key(asset_path) in BUNDLED_RELATIVE_ASSETS:
            continue
        remaining.append(item)
    return remaining


def _rewrite_asset_paths_to_relative(stage, asset_dependencies: dict) -> int:
    rewrites = 0
    for item in asset_dependencies.get("all", []) or []:
        prim_path = item.get("prim")
        attr_name = item.get("attribute")
        asset_path = item.get("asset_path")
        source_path = item.get("resolved_path") if item.get("is_relative") else asset_path
        if not prim_path or not attr_name or not asset_path or not source_path:
            continue
        if "://" in asset_path or not os.path.exists(source_path):
            continue
        relative_path = _asset_target_relative_path(asset_path, source_path)
        if asset_path == relative_path:
            continue
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            continue
        attr = prim.GetAttribute(attr_name)
        if not attr or not attr.IsValid():
            continue
        attr.Set(Sdf.AssetPath(relative_path))
        rewrites += 1
    return rewrites


def _asset_path_rewrite_map(asset_dependencies: dict) -> dict:
    replacements = {}
    for item in asset_dependencies.get("all", []) or []:
        asset_path = item.get("asset_path")
        source_path = item.get("resolved_path") if item.get("is_relative") else asset_path
        if not asset_path or not source_path or "://" in asset_path or not os.path.exists(source_path):
            continue
        relative_path = _asset_target_relative_path(asset_path, source_path)
        replacements[asset_path] = relative_path
        replacements[os.path.abspath(source_path)] = relative_path
    return replacements


def _rewrite_exported_usda_asset_paths(output_path: str, asset_dependencies: dict) -> int:
    try:
        with open(output_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except UnicodeDecodeError:
        return 0

    replacements = _asset_path_rewrite_map(asset_dependencies)
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
    parser.add_argument("--output", help="Path to write the authored USD; default is ./<name>.simready_static.usda")
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
        "--no-apply-reference-scale",
        action="store_true",
        help="Do not apply recommendation.authoring.suggested_uniform_scale to the default prim",
    )
    args = parser.parse_args(argv)

    recommendation = load_json(args.recommendation_json)
    recommendation_body = recommendation.get("recommendation", {}) or {}
    authoring = (recommendation_body.get("authoring", {}) or {})
    collision_plan = recommendation_body.get("collision_plan", {}) or {}
    target_mesh_paths = authoring.get("target_mesh_paths") or collision_plan.get("target_mesh_paths") or []
    approximation = authoring.get("approximation") or collision_plan.get("usd_approximation") or "convexHull"
    source_usd_for_authoring = authoring.get("source_usd_for_authoring") or args.input_usd
    output_path = args.output or _default_output_path(os.path.abspath(source_usd_for_authoring))

    stage = open_stage(source_usd_for_authoring)
    asset_dependencies = inspect_asset_dependencies(stage, source_usd_for_authoring)
    bundled_paths: List[str] = []
    if not args.no_copy_relative_assets:
        bundled_paths = _copy_bundled_asset_dependencies(asset_dependencies, output_path)
    missing_assets = _remaining_missing_assets(asset_dependencies)
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

    rewritten_count = 0
    if not args.no_copy_relative_assets:
        rewritten_count = _rewrite_asset_paths_to_relative(stage, asset_dependencies)
    stage.Export(output_path)
    file_rewritten_count = 0
    if not args.no_copy_relative_assets:
        file_rewritten_count = _rewrite_exported_usda_asset_paths(output_path, asset_dependencies)
    copied_paths: List[str] = []
    if not args.no_copy_relative_assets:
        copied_paths = _copy_asset_dependencies(asset_dependencies, output_path)
    print(output_path)
    if rewritten_count:
        print(f"rewrote {rewritten_count} asset paths to relative paths")
    if file_rewritten_count:
        print(f"normalized {file_rewritten_count} exported asset paths")
    if bundled_paths:
        print(f"copied {len(bundled_paths)} bundled asset dependencies")
    if copied_paths:
        print(f"copied {len(copied_paths)} relative asset dependencies")
    if applied_reference_scale is not None:
        print(f"applied reference uniform scale {applied_reference_scale}")
    if applied_orientation:
        print("applied orientation correction")
    if not applied_paths:
        print("warning: no target mesh paths were authored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
