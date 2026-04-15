#!/usr/bin/env python3
"""Apply static furniture SimReady physics settings to a USD asset."""

from __future__ import annotations

import argparse
import os
from typing import List, Optional

from pxr import UsdPhysics

from static_furniture import load_json
from usd_inspector import open_stage


def _default_output_path(input_usd: str) -> str:
    basename = os.path.basename(input_usd)
    stem, _ = os.path.splitext(basename)
    return os.path.join(os.getcwd(), f"{stem}.simready_static.usda")


def _apply_collision_to_prim(prim, approximation: str) -> None:
    collision_api = UsdPhysics.CollisionAPI.Apply(prim)
    collision_api.CreateCollisionEnabledAttr(True)
    mesh_collision_api = UsdPhysics.MeshCollisionAPI.Apply(prim)
    mesh_collision_api.CreateApproximationAttr(approximation)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Apply static furniture SimReady settings from recommendation JSON.")
    parser.add_argument("input_usd", help="Path to source USD asset")
    parser.add_argument("recommendation_json", help="Path to recommendation JSON from recommend_static_furniture_simready.py")
    parser.add_argument("--output", help="Path to write the authored USD; default is ./<name>.simready_static.usda")
    args = parser.parse_args(argv)

    recommendation = load_json(args.recommendation_json)
    authoring = ((recommendation.get("recommendation", {}) or {}).get("authoring", {}) or {})
    target_mesh_paths = authoring.get("target_mesh_paths") or []
    approximation = authoring.get("approximation") or "convexHull"
    source_usd_for_authoring = authoring.get("source_usd_for_authoring") or args.input_usd

    stage = open_stage(source_usd_for_authoring)
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

    output_path = args.output or _default_output_path(os.path.abspath(source_usd_for_authoring))
    stage.Export(output_path)
    print(output_path)
    if not applied_paths:
        print("warning: no target mesh paths were authored")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
