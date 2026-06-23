#!/usr/bin/env python3
"""Author a lightweight proxy collider for a USD asset."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics


def _default_output_path(input_usd: str) -> str:
    root, ext = os.path.splitext(os.path.abspath(input_usd))
    return root + ".proxy_collider" + (ext or ".usda")


def _range_to_lists(bbox_range: Gf.Range3d) -> Tuple[List[float], List[float], List[float]]:
    return (
        [float(value) for value in bbox_range.GetMin()],
        [float(value) for value in bbox_range.GetMax()],
        [float(value) for value in bbox_range.GetSize()],
    )


def _default_prim_world_bbox(stage: Usd.Stage) -> Gf.Range3d:
    default_prim = stage.GetDefaultPrim()
    if not default_prim or not default_prim.IsValid():
        raise ValueError("input USD has no valid defaultPrim")
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=True,
    )
    bbox = cache.ComputeWorldBound(default_prim).ComputeAlignedRange()
    if bbox.IsEmpty():
        raise ValueError("could not compute defaultPrim world bbox")
    return bbox


def _world_bbox_as_default_local(stage: Usd.Stage, world_bbox: Gf.Range3d) -> Gf.Range3d:
    default_prim = stage.GetDefaultPrim()
    if not default_prim or not default_prim.IsValid():
        raise ValueError("input USD has no valid defaultPrim")
    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    local_to_world = xform_cache.GetLocalToWorldTransform(default_prim)
    world_to_local = local_to_world.GetInverse()
    minimum = world_bbox.GetMin()
    maximum = world_bbox.GetMax()
    local_range = Gf.Range3d()
    for x in (minimum[0], maximum[0]):
        for y in (minimum[1], maximum[1]):
            for z in (minimum[2], maximum[2]):
                local_range.UnionWith(world_to_local.Transform(Gf.Vec3d(x, y, z)))
    if local_range.IsEmpty():
        raise ValueError("could not convert defaultPrim world bbox to local space")
    return local_range


def _bool_attr_value(prim: Usd.Prim, name: str, default: bool = True) -> bool:
    attr = prim.GetAttribute(name)
    if not attr:
        return default
    value = attr.Get()
    return default if value is None else bool(value)


def _collision_prims(stage: Usd.Stage, exclude_path: Optional[str] = None) -> List[Usd.Prim]:
    prims: List[Usd.Prim] = []
    for prim in stage.Traverse():
        if exclude_path and prim.GetPath().pathString == exclude_path:
            continue
        if prim.HasAPI(UsdPhysics.CollisionAPI):
            prims.append(prim)
    return prims


def _disable_authored_colliders(stage: Usd.Stage, exclude_path: str) -> List[str]:
    disabled: List[str] = []
    for prim in _collision_prims(stage, exclude_path=exclude_path):
        collision_api = UsdPhysics.CollisionAPI.Apply(prim)
        collision_api.CreateCollisionEnabledAttr(False)
        disabled.append(prim.GetPath().pathString)
    return disabled


def _ensure_parent_xforms(stage: Usd.Stage, path: Sdf.Path) -> None:
    current = Sdf.Path.absoluteRootPath
    for element in path.GetPrefixes()[1:-1]:
        current = element
        prim = stage.GetPrimAtPath(current)
        if not prim or not prim.IsValid():
            UsdGeom.Xform.Define(stage, current)


def _author_bbox_proxy(
    stage: Usd.Stage,
    proxy_path: str,
    bbox_min: List[float],
    bbox_max: List[float],
    bbox_size: List[float],
) -> Dict[str, List[float]]:
    path = Sdf.Path(proxy_path)
    if not path.IsAbsolutePath():
        raise ValueError(f"proxy path must be absolute: {proxy_path}")
    _ensure_parent_xforms(stage, path)

    center = [
        (bbox_min[0] + bbox_max[0]) * 0.5,
        (bbox_min[1] + bbox_max[1]) * 0.5,
        (bbox_min[2] + bbox_max[2]) * 0.5,
    ]
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    cube.CreatePurposeAttr(UsdGeom.Tokens.proxy)
    prim = cube.GetPrim()
    prim.CreateAttribute("simready:proxyCollider", Sdf.ValueTypeNames.Bool).Set(True)
    prim.CreateAttribute("simready:proxyCollider:source", Sdf.ValueTypeNames.String).Set("bbox")
    xformable = UsdGeom.Xformable(prim)
    for op in list(xformable.GetOrderedXformOps()):
        xformable.RemoveXformOp(op)
    xformable.AddTranslateOp().Set(Gf.Vec3d(center[0], center[1], center[2]))
    xformable.AddScaleOp().Set(Gf.Vec3f(bbox_size[0], bbox_size[1], bbox_size[2]))
    UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr(True)
    return {"min": bbox_min, "max": bbox_max, "size": bbox_size, "center": center}


def _write_report(path: str, payload: Dict[str, object]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def author_bbox_proxy_collider(
    input_usd: str,
    output_usd: str,
    *,
    proxy_path: Optional[str] = None,
    keep_authored_colliders: bool = False,
    report_overrides: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    """Author a bbox proxy collider and export the edited stage."""
    input_path = os.path.abspath(input_usd)
    output_path = os.path.abspath(output_usd)
    stage = Usd.Stage.Open(input_path)
    if stage is None:
        raise ValueError(f"could not open input USD: {input_path}")
    default_prim = stage.GetDefaultPrim()
    if not default_prim or not default_prim.IsValid():
        raise ValueError("input USD has no valid defaultPrim")

    authored_proxy_path = proxy_path or f"{default_prim.GetPath().pathString.rstrip('/')}/PhysicsProxy/BBoxCollider"
    world_bbox = _default_prim_world_bbox(stage)
    local_bbox = _world_bbox_as_default_local(stage, world_bbox)
    bbox_min, bbox_max, bbox_size = _range_to_lists(local_bbox)
    world_min, world_max, world_size = _range_to_lists(world_bbox)
    proxy_bbox = _author_bbox_proxy(stage, authored_proxy_path, bbox_min, bbox_max, bbox_size)
    disabled = [] if keep_authored_colliders else _disable_authored_colliders(stage, authored_proxy_path)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if not stage.GetRootLayer().Export(output_path):
        raise ValueError(f"failed to export {output_path}")

    report: Dict[str, object] = {
        "input_usd": input_path,
        "output_usd": output_path,
        "default_prim": default_prim.GetPath().pathString,
        "proxy_path": authored_proxy_path,
        "proxy_bbox_local": proxy_bbox,
        "source_bbox_world": {
            "min": world_min,
            "max": world_max,
            "size": world_size,
        },
        "disabled_authored_colliders": disabled,
        "kept_authored_colliders": bool(keep_authored_colliders),
    }
    if report_overrides:
        report.update(report_overrides)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Add a lightweight bbox proxy collider to a USD asset.")
    parser.add_argument("input_usd")
    parser.add_argument("--output", help="Output USD path; defaults to <input>.proxy_collider.<ext>")
    parser.add_argument("--proxy-path", help="Absolute proxy prim path. Defaults under the defaultPrim.")
    parser.add_argument(
        "--keep-authored-colliders",
        action="store_true",
        help="Keep existing authored colliders enabled instead of disabling them.",
    )
    parser.add_argument("--report", help="Optional JSON authoring report")
    args = parser.parse_args(argv)

    input_usd = os.path.abspath(args.input_usd)
    output = os.path.abspath(args.output or _default_output_path(input_usd))
    try:
        report = author_bbox_proxy_collider(
            input_usd,
            output,
            proxy_path=args.proxy_path,
            keep_authored_colliders=args.keep_authored_colliders,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.report:
        _write_report(args.report, report)
    print(output)
    if args.report:
        print(args.report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
