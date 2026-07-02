#!/usr/bin/env python3
"""Run an ovphysx simulation for a pre-authored runtime smoke scene.

This module intentionally imports ovphysx, and should be executed in a Python
environment that is compatible with ovphysx. The main inspector environment
uses usd-core, which ovphysx currently cannot share in-process.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional


def _save_json(path: str, payload: Dict[str, Any], pretty: bool = True) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if pretty else None, ensure_ascii=False)


def _vector_norm(values: Any) -> Optional[float]:
    try:
        return math.sqrt(sum(float(value) * float(value) for value in values))
    except Exception:
        return None


def _read_pose(binding: Any) -> Optional[List[float]]:
    try:
        import numpy as np

        out = np.zeros(binding.shape, dtype=np.float32)
        binding.read(out)
        if out.size < 7:
            return None
        return [float(value) for value in out.reshape(-1, 7)[0].tolist()]
    except Exception:
        return None


def _status_from_checks(checks: Dict[str, Any]) -> str:
    if not checks.get("ovphysx_imported"):
        return "unavailable"
    if not checks.get("scene_loaded") or not checks.get("simulation_advanced"):
        return "failed"
    if checks.get("contact_binding_created") and not checks.get("contact_detected"):
        return "failed"
    return "passed"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Execute an ovphysx runtime smoke scene.")
    parser.add_argument("--scene", required=True, help="USD scene authored by ovphysx_runtime_check.py")
    parser.add_argument("--output", required=True, help="JSON report path")
    parser.add_argument("--frames", type=int, default=240)
    parser.add_argument("--fps", type=float, default=60.0)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--sensor", default="/World/DropBox")
    parser.add_argument("--filter", dest="filters", action="append", default=[])
    parser.add_argument("--contact-force-threshold", type=float, default=1e-5)
    args = parser.parse_args(argv)

    report: Dict[str, Any] = {
        "backend": "ovphysx",
        "scene_path": os.path.abspath(args.scene),
        "frames": args.frames,
        "fps": args.fps,
        "device": args.device,
        "sensor_path": args.sensor,
        "filter_paths": args.filters,
        "checks": {
            "ovphysx_imported": False,
            "scene_loaded": False,
            "pose_binding_created": False,
            "contact_binding_created": False,
            "simulation_advanced": False,
            "contact_detected": False,
        },
        "errors": [],
    }

    try:
        import ovphysx
        from ovphysx import PhysX
        from ovphysx.types import TensorType
        import numpy as np
    except Exception as exc:
        report["checks"]["ovphysx_imported"] = False
        report["status"] = "unavailable"
        report["reason"] = "ovphysx_import_failed"
        report["errors"].append(str(exc))
        _save_json(args.output, report)
        return 2

    report["checks"]["ovphysx_imported"] = True
    report["ovphysx_version"] = getattr(ovphysx, "__version__", None)

    physx = None
    usd_handle = None
    pose_binding = None
    contact_binding = None
    try:
        physx = PhysX(device=args.device)
        add_result = physx.add_usd(os.path.abspath(args.scene))
        if isinstance(add_result, tuple):
            usd_handle = add_result[0]
        else:
            usd_handle = add_result
        physx.wait_all()
        report["checks"]["scene_loaded"] = True

        try:
            pose_binding = physx.create_tensor_binding(
                pattern=args.sensor,
                tensor_type=TensorType.RIGID_BODY_POSE,
            )
            report["checks"]["pose_binding_created"] = True
            report["initial_pose"] = _read_pose(pose_binding)
        except Exception as exc:
            report["errors"].append(f"pose_binding_failed: {exc}")

        if args.filters:
            try:
                contact_binding = physx.create_contact_binding(
                    sensor_patterns=[args.sensor],
                    filter_patterns=args.filters,
                    filters_per_sensor=max(1, len(args.filters)),
                    max_contact_data_count=1024,
                )
                report["checks"]["contact_binding_created"] = True
                report["contact_binding"] = {
                    "sensor_count": int(contact_binding.sensor_count),
                    "filter_count": int(contact_binding.filter_count),
                }
            except Exception as exc:
                report["errors"].append(f"contact_binding_failed: {exc}")

        dt = 1.0 / max(float(args.fps), 1e-6)
        for frame in range(max(0, int(args.frames))):
            physx.step(dt, frame * dt)
        physx.wait_all()
        report["checks"]["simulation_advanced"] = args.frames > 0

        if pose_binding is not None:
            report["final_pose"] = _read_pose(pose_binding)

        if contact_binding is not None:
            net_forces = np.zeros((contact_binding.sensor_count, 3), dtype=np.float32)
            contact_binding.read_net_forces(net_forces)
            force_matrix = np.zeros((contact_binding.sensor_count, contact_binding.filter_count, 3), dtype=np.float32)
            contact_binding.read_force_matrix(force_matrix)
            net_force_list = net_forces.tolist()
            matrix_list = force_matrix.tolist()
            max_net_force = max((_vector_norm(row) or 0.0 for row in net_force_list), default=0.0)
            max_pair_force = 0.0
            for sensor_rows in matrix_list:
                for row in sensor_rows:
                    max_pair_force = max(max_pair_force, _vector_norm(row) or 0.0)
            report["contact_forces"] = {
                "net_forces": net_force_list,
                "force_matrix": matrix_list,
                "max_net_force": max_net_force,
                "max_pair_force": max_pair_force,
                "threshold": args.contact_force_threshold,
            }
            report["checks"]["contact_detected"] = max(max_net_force, max_pair_force) > args.contact_force_threshold

    except Exception as exc:
        report["errors"].append(str(exc))
    finally:
        try:
            if contact_binding is not None:
                contact_binding.destroy()
        except Exception:
            pass
        try:
            if pose_binding is not None:
                pose_binding.destroy()
        except Exception:
            pass
        try:
            if physx is not None and usd_handle is not None:
                physx.remove_usd(usd_handle)
        except Exception:
            pass
        try:
            if physx is not None:
                physx.release()
        except Exception:
            pass

    report["status"] = _status_from_checks(report["checks"])
    if report["status"] == "failed" and report["checks"].get("contact_binding_created") and not report["checks"].get("contact_detected"):
        report["reason"] = "contact_not_detected"
    elif report["status"] == "failed":
        report["reason"] = "simulation_failed"
    _save_json(args.output, report)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
