#!/usr/bin/env python3
"""Author and run a lightweight ovphysx smoke test for an authored USD asset."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(SCRIPT_DIR, "ovphysx_runtime_runner.py")


def _save_json(path: str, payload: Dict[str, Any], pretty: bool = True) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, ensure_ascii=False)


def _default_output(input_usd: str) -> str:
    root, _ = os.path.splitext(os.path.abspath(input_usd))
    return root + ".ovphysx_smoke.json"


def _default_work_dir(output: str) -> str:
    root, _ = os.path.splitext(os.path.abspath(output))
    return root + "_artifacts"


def _bbox_for_default_prim(stage: Usd.Stage) -> Tuple[Gf.Range3d, List[float], List[float], List[float]]:
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
        raise ValueError("could not compute defaultPrim bbox")
    minimum = [float(value) for value in bbox.GetMin()]
    maximum = [float(value) for value in bbox.GetMax()]
    size = [float(value) for value in bbox.GetSize()]
    return bbox, minimum, maximum, size


def _collision_paths(stage: Usd.Stage) -> List[str]:
    paths: List[str] = []
    for prim in stage.Traverse():
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        collision_api = UsdPhysics.CollisionAPI(prim)
        enabled = collision_api.GetCollisionEnabledAttr().Get()
        if enabled is False:
            continue
        if prim.GetAttribute("simready:proxyCollider").Get() is True:
            paths.insert(0, prim.GetPath().pathString)
        else:
            paths.append(prim.GetPath().pathString)
    return paths


def _map_reference_path(source_path: str, default_prim_path: str, target_root: str) -> str:
    if source_path == default_prim_path:
        return target_root
    prefix = default_prim_path.rstrip("/") + "/"
    if source_path.startswith(prefix):
        return target_root.rstrip("/") + "/" + source_path[len(prefix):]
    return target_root.rstrip("/") + "/" + source_path.lstrip("/")


def _stage_units_metadata(stage: Usd.Stage) -> Tuple[float, str]:
    meters_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
    try:
        up_axis = str(UsdGeom.GetStageUpAxis(stage))
    except Exception:
        up_axis = str(stage.GetMetadata("upAxis") or "Z")
    return float(meters_per_unit or 1.0), up_axis.upper()


def _default_drop_box_size(bbox_size: List[float], meters_per_unit: float) -> float:
    bbox_size_cm = [abs(value) * meters_per_unit * 100.0 for value in bbox_size]
    footprint_min_cm = max(min(bbox_size_cm[0], bbox_size_cm[1]), 1e-6)
    max_span_cm = max(bbox_size_cm[0], bbox_size_cm[1], bbox_size_cm[2], 8.0)
    size_cm = max(8.0, min(footprint_min_cm * 0.45, max_span_cm * 0.25, 75.0))
    return size_cm / max(meters_per_unit * 100.0, 1e-9)


def _apply_api_schema_names(prim: Usd.Prim, names: List[str]) -> None:
    existing = [str(item) for item in prim.GetAppliedSchemas()]
    merged = []
    for name in existing + names:
        if name and name not in merged:
            merged.append(name)
    prim.SetMetadata("apiSchemas", Sdf.TokenListOp.CreateExplicit(merged))


def _define_drop_box(stage: Usd.Stage, path: str, size: float, z_center: float) -> None:
    half = size * 0.5
    mesh = UsdGeom.Mesh.Define(stage, path)
    prim = mesh.GetPrim()
    mesh.CreatePointsAttr(
        [
            (-half, -half, half),
            (half, -half, half),
            (-half, half, half),
            (half, half, half),
            (-half, -half, -half),
            (half, -half, -half),
            (-half, half, -half),
            (half, half, -half),
        ]
    )
    mesh.CreateFaceVertexCountsAttr([4, 4, 4, 4, 4, 4])
    mesh.CreateFaceVertexIndicesAttr(
        [
            0, 1, 3, 2,
            4, 6, 7, 5,
            6, 2, 3, 7,
            4, 5, 1, 0,
            4, 0, 2, 6,
            5, 7, 3, 1,
        ]
    )
    mesh.CreateExtentAttr([(-half, -half, -half), (half, half, half)])
    mesh.CreateSubdivisionSchemeAttr("none")
    xformable = UsdGeom.Xformable(prim)
    xformable.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, z_center))
    UsdPhysics.RigidBodyAPI.Apply(prim).CreateRigidBodyEnabledAttr(True)
    UsdPhysics.RigidBodyAPI(prim).CreateKinematicEnabledAttr(False)
    UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr(True)
    UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr("convexHull")
    UsdPhysics.MassAPI.Apply(prim).CreateDensityAttr(1000.0)
    prim.CreateAttribute("physxContactReport:threshold", Sdf.ValueTypeNames.Float).Set(0.0)
    prim.CreateAttribute("physxRigidBody:sleepThreshold", Sdf.ValueTypeNames.Float).Set(0.0)
    _apply_api_schema_names(
        prim,
        [
            "PhysicsRigidBodyAPI",
            "PhysicsMassAPI",
            "PhysicsCollisionAPI",
            "PhysicsMeshCollisionAPI",
            "PhysxContactReportAPI",
            "PhysxRigidBodyAPI",
        ],
    )


def _define_bbox_proxy(stage: Usd.Stage, path: str, bbox_size: List[float], z_center: float) -> None:
    cube = UsdGeom.Cube.Define(stage, path)
    prim = cube.GetPrim()
    cube.CreateSizeAttr(1.0)
    xformable = UsdGeom.Xformable(prim)
    xformable.AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, z_center))
    xformable.AddScaleOp().Set(Gf.Vec3f(float(bbox_size[0]), float(bbox_size[1]), float(bbox_size[2])))
    UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr(True)


def _author_scene(
    input_usd: str,
    scene_path: str,
    *,
    frames: int,
    fps: float,
    box_size: Optional[float],
    drop_height: Optional[float],
    asset_collider_mode: str,
) -> Dict[str, Any]:
    source_stage = Usd.Stage.Open(input_usd)
    if source_stage is None:
        raise ValueError(f"could not open input USD: {input_usd}")
    default_prim = source_stage.GetDefaultPrim()
    if not default_prim or not default_prim.IsValid():
        raise ValueError("input USD has no valid defaultPrim")

    _, bbox_min, bbox_max, bbox_size = _bbox_for_default_prim(source_stage)
    meters_per_unit, up_axis = _stage_units_metadata(source_stage)
    if up_axis != "Z":
        raise ValueError(f"ovphysx smoke scene expects Z-up input; observed upAxis={up_axis}")

    collisions = _collision_paths(source_stage)
    if not collisions:
        raise ValueError("input USD has no collision prims")

    os.makedirs(os.path.dirname(os.path.abspath(scene_path)), exist_ok=True)
    stage = Usd.Stage.CreateNew(scene_path)
    UsdGeom.SetStageMetersPerUnit(stage, meters_per_unit)
    UsdGeom.SetStageUpAxis(stage, "Z")
    stage.SetTimeCodesPerSecond(float(fps))
    stage.SetStartTimeCode(0)
    stage.SetEndTimeCode(max(0, int(frames)))

    world = UsdGeom.Xform.Define(stage, "/World").GetPrim()
    stage.SetDefaultPrim(world)
    scene = UsdPhysics.Scene.Define(stage, "/World/physicsScene")
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0.0, 0.0, -1.0))
    scene.CreateGravityMagnitudeAttr(float(9.81 / max(meters_per_unit, 1e-9)))

    asset_root = UsdGeom.Xform.Define(stage, "/World/TestAsset")
    asset_translation = Gf.Vec3d(
        -0.5 * (bbox_min[0] + bbox_max[0]),
        -0.5 * (bbox_min[1] + bbox_max[1]),
        -bbox_min[2],
    )
    asset_root.AddTranslateOp().Set(asset_translation)
    referenced_asset = UsdGeom.Xform.Define(stage, "/World/TestAsset/ReferencedAsset").GetPrim()
    referenced_asset.GetReferences().AddReference(os.path.abspath(input_usd))

    chosen_box_size = float(box_size) if box_size is not None else _default_drop_box_size(bbox_size, meters_per_unit)
    chosen_drop_height = (
        float(drop_height)
        if drop_height is not None
        else max(abs(bbox_size[2]) * 0.75, chosen_box_size * 3.0)
    )
    drop_z = abs(bbox_size[2]) + chosen_drop_height + chosen_box_size * 0.5
    _define_drop_box(stage, "/World/DropBox", chosen_box_size, drop_z)

    if asset_collider_mode == "bbox-proxy":
        _define_bbox_proxy(stage, "/World/AssetBBoxCollider", bbox_size, abs(bbox_size[2]) * 0.5)
        filter_paths = ["/World/AssetBBoxCollider"]
    else:
        target_root = "/World/TestAsset/ReferencedAsset"
        filter_paths = [_map_reference_path(path, default_prim.GetPath().pathString, target_root) for path in collisions]
    stage.GetRootLayer().Save()

    return {
        "input_usd": os.path.abspath(input_usd),
        "scene_path": os.path.abspath(scene_path),
        "meters_per_unit": meters_per_unit,
        "up_axis": up_axis,
        "asset_bbox_min": bbox_min,
        "asset_bbox_max": bbox_max,
        "asset_bbox_size": bbox_size,
        "asset_translation": [float(asset_translation[0]), float(asset_translation[1]), float(asset_translation[2])],
        "drop_box_path": "/World/DropBox",
        "drop_box_size": chosen_box_size,
        "drop_box_initial_z": drop_z,
        "asset_collider_mode": asset_collider_mode,
        "filter_paths": filter_paths,
        "frames": int(frames),
        "fps": float(fps),
    }


def _load_json_if_exists(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Author a small drop-test scene and run it in an external ovphysx Python environment."
    )
    parser.add_argument("input_usd", help="Authored USD/USDA/USDC asset with collision schemas")
    parser.add_argument("--output", help="JSON smoke report path")
    parser.add_argument("--work-dir", help="Directory for generated scene and runner artifacts")
    parser.add_argument("--ovphysx-python", default=os.environ.get("OVPHYSX_PYTHON") or "python3")
    parser.add_argument("--frames", type=int, default=240)
    parser.add_argument("--fps", type=float, default=60.0)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--box-size", type=float, help="Drop box size in input stage units")
    parser.add_argument("--drop-height", type=float, help="Gap above asset top in input stage units")
    parser.add_argument(
        "--asset-collider-mode",
        choices=["authored", "bbox-proxy"],
        default="authored",
        help="Use authored asset colliders or a temporary bbox proxy collider in the smoke scene",
    )
    parser.add_argument("--contact-force-threshold", type=float, default=1e-5)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    input_usd = os.path.abspath(args.input_usd)
    output = os.path.abspath(args.output or _default_output(input_usd))
    work_dir = os.path.abspath(args.work_dir or _default_work_dir(output))
    scene_path = os.path.join(work_dir, "ovphysx_smoke_scene.usda")

    report: Dict[str, Any] = {
        "backend": "ovphysx",
        "input_usd": input_usd,
        "output": output,
        "work_dir": work_dir,
        "checks": {
            "input_exists": os.path.exists(input_usd),
            "scene_authored": False,
            "runner_invoked": False,
        },
        "errors": [],
    }

    if not os.path.exists(input_usd):
        report.update({"status": "blocked", "reason": "input_usd_missing"})
        _save_json(output, report)
        print(output)
        return 2

    try:
        scene_metadata = _author_scene(
            input_usd,
            scene_path,
            frames=args.frames,
            fps=args.fps,
            box_size=args.box_size,
            drop_height=args.drop_height,
            asset_collider_mode=args.asset_collider_mode,
        )
        report["checks"]["scene_authored"] = True
        report["scene"] = scene_metadata
    except Exception as exc:
        report.update({"status": "blocked", "reason": "scene_authoring_failed"})
        report["errors"].append(str(exc))
        _save_json(output, report)
        print(output)
        return 2

    command = [
        args.ovphysx_python,
        RUNNER,
        "--scene",
        scene_path,
        "--output",
        output,
        "--frames",
        str(args.frames),
        "--fps",
        str(args.fps),
        "--device",
        args.device,
        "--sensor",
        scene_metadata["drop_box_path"],
        "--contact-force-threshold",
        str(args.contact_force_threshold),
    ]
    for filter_path in scene_metadata["filter_paths"]:
        command.extend(["--filter", filter_path])
    report["command"] = command

    if args.dry_run:
        report["status"] = "dry_run"
        _save_json(output, report)
        print(output)
        return 0

    executable = shutil.which(args.ovphysx_python) if not os.path.isabs(args.ovphysx_python) else args.ovphysx_python
    if not executable or not os.path.exists(executable):
        report.update({"status": "unavailable", "reason": "ovphysx_python_not_found"})
        _save_json(output, report)
        print(output)
        return 2

    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    report["checks"]["runner_invoked"] = True
    runner_report = _load_json_if_exists(output)
    if runner_report:
        runner_report["scene"] = scene_metadata
        runner_report["command"] = command
        runner_report["runner_returncode"] = completed.returncode
        if completed.stdout:
            runner_report["runner_stdout"] = completed.stdout
        if completed.stderr:
            runner_report["runner_stderr"] = completed.stderr
        _save_json(output, runner_report)
    else:
        report.update(
            {
                "status": "failed",
                "reason": "runner_report_missing",
                "runner_returncode": completed.returncode,
                "runner_stdout": completed.stdout,
                "runner_stderr": completed.stderr,
            }
        )
        _save_json(output, report)
    print(output)
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
