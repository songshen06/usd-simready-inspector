#!/usr/bin/env python3
"""Apply safe primitive-collider schema repairs selected by omni-asset-cli."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional

from pxr import Usd, UsdGeom, UsdPhysics


RULE_ID = "RB.COL.002"
REPAIR_OWNER = "usd-simready-inspector"
REPAIR_ACTION = "remove_non_mesh_mesh_collision_api_and_approximation"
PRESERVED = ["PhysicsCollisionAPI", "geometry", "transforms", "materials", "rigid_bodies", "joints"]


def _default_output_path(input_usd: str) -> str:
    root, _ = os.path.splitext(os.path.abspath(input_usd))
    return root + ".rb_col_002_repaired.usda"


def _default_report_path(output_usd: str) -> str:
    root, _ = os.path.splitext(os.path.abspath(output_usd))
    return root + ".collider_repair.json"


def _write_json(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)


def _selected_findings(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    findings = payload.get("findings")
    if not isinstance(findings, list):
        raise ValueError("findings JSON must contain a list at key 'findings'")
    for finding in findings:
        if not isinstance(finding, dict) or finding.get("rule_id") != RULE_ID:
            continue
        repair = finding.get("repair")
        if not isinstance(repair, dict):
            raise ValueError(f"{RULE_ID} finding has no repair contract: {finding.get('prim')}")
        if repair.get("owner") != REPAIR_OWNER or repair.get("action") != REPAIR_ACTION:
            raise ValueError(f"unsupported repair contract for {finding.get('prim')}")
        if finding.get("repairability") != "safe":
            raise ValueError(f"finding is not safe to repair: {finding.get('prim')}")
        yield finding


def repair_primitive_colliders(input_usd: str, findings_json: str, output_usd: str) -> Dict[str, Any]:
    """Export a candidate USD after removing only selected primitive schema conflicts."""
    input_path = os.path.abspath(input_usd)
    findings_path = os.path.abspath(findings_json)
    output_path = os.path.abspath(output_usd)
    if input_path == output_path:
        raise ValueError("output USD must differ from input USD; source assets are never modified")

    with open(findings_path, "r", encoding="utf-8") as handle:
        audit = json.load(handle)
    if not isinstance(audit, dict):
        raise ValueError("findings JSON root must be an object")

    findings = list(_selected_findings(audit))
    if not findings:
        raise ValueError(f"findings JSON contains no {RULE_ID} safe repair entries")

    paths = [str(finding.get("prim") or "") for finding in findings]
    if any(not path.startswith("/") for path in paths):
        raise ValueError("all selected findings must have absolute prim paths")
    if len(set(paths)) != len(paths):
        raise ValueError("findings JSON contains duplicate prim paths")

    stage = Usd.Stage.Open(input_path)
    if stage is None:
        raise ValueError(f"could not open input USD: {input_path}")

    applied: List[Dict[str, Any]] = []
    for finding in findings:
        prim_path = str(finding["prim"])
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            raise ValueError(f"finding target does not exist in input USD: {prim_path}")
        if prim.IsA(UsdGeom.Mesh):
            raise ValueError(f"refusing to remove MeshCollisionAPI from mesh prim: {prim_path}")
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            raise ValueError(f"finding target is no longer a collider: {prim_path}")
        if not prim.HasAPI(UsdPhysics.MeshCollisionAPI):
            raise ValueError(f"finding target has no MeshCollisionAPI to remove: {prim_path}")

        if not prim.RemoveAPI(UsdPhysics.MeshCollisionAPI):
            raise ValueError(f"failed to remove MeshCollisionAPI: {prim_path}")
        # Approximation is part of MeshCollisionAPI; remove a locally authored
        # property too so the exported candidate is unambiguous to readers.
        prim.RemoveProperty("physics:approximation")
        applied.append(
            {
                "rule_id": RULE_ID,
                "prim": prim_path,
                "prim_type": prim.GetTypeName(),
                "removed": ["PhysicsMeshCollisionAPI", "physics:approximation"],
                "preserved": PRESERVED,
            }
        )

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if not stage.GetRootLayer().Export(output_path):
        raise ValueError(f"failed to export repaired USD: {output_path}")

    return {
        "schema_version": "1.0",
        "operation": "primitive-collider-repair",
        "status": "applied_safe",
        "input_usd": input_path,
        "findings_json": findings_path,
        "output_usd": output_path,
        "rule_id": RULE_ID,
        "repair_contract": {"owner": REPAIR_OWNER, "action": REPAIR_ACTION},
        "applied_count": len(applied),
        "applied": applied,
        "source_modified": False,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apply safe RB.COL.002 primitive-collider repairs selected by omni-asset-cli."
    )
    parser.add_argument("input_usd")
    parser.add_argument("--findings", required=True, help="primitive_collider_audit.json from omni-asset-cli")
    parser.add_argument("--output", help="Candidate USD path; source USD is never modified")
    parser.add_argument("--report", help="Repair report JSON path")
    args = parser.parse_args(argv)

    input_usd = os.path.abspath(args.input_usd)
    output_usd = os.path.abspath(args.output or _default_output_path(input_usd))
    report_path = os.path.abspath(args.report or _default_report_path(output_usd))
    try:
        report = repair_primitive_colliders(input_usd, args.findings, output_usd)
        _write_json(report_path, report)
    except Exception as error:
        print(f"error: primitive collider repair failed: {error}", file=sys.stderr)
        return 2
    print(output_usd)
    print(report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
