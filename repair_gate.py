"""Profile-gated repair: only touch what the current profile actually rejects.

Repairs are licensed by requirement codes, not by heuristics. A repairer runs
only when the validator reported that code as failing for the profile under
test; everything else is reported as unsupported or unlicensed and left alone.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Set

from pxr import Usd, UsdGeom, UsdPhysics

NAMESPACE_PREFIXES = ("com.nvidia.simready.", "com.nvidia.usd.")
PRIM_PATTERN = re.compile(r"Prim '([^']+)'")

# code -> what this tool is willing to change
REPAIRERS: Dict[str, Dict[str, str]] = {
    "RB.COL.001": {
        "action": "remove_collision_api_from_non_gprim",
        "description": "CollisionAPI on a prim that is not a UsdGeom GPrim",
    },
    "RB.COL.002": {
        "action": "remove_mesh_collision_api_from_non_mesh",
        "description": "MeshCollisionAPI on a prim that is not a UsdGeom Mesh",
    },
}


def normalize_code(code: str) -> str:
    for prefix in NAMESPACE_PREFIXES:
        if code.startswith(prefix):
            return code[len(prefix):]
    return code


def _messages_by_code(feature_body: Dict[str, Any]) -> Dict[str, List[str]]:
    raw = feature_body.get("requirement_messages")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    return {normalize_code(str(code)): [str(item) for item in values]
            for code, values in raw.items()}


def derive_findings(conformance: Dict[str, Any]) -> Dict[str, Any]:
    """Turn validator output into repair findings, one per (code, prim) pair."""
    findings: List[Dict[str, Any]] = []
    seen: Set[tuple] = set()
    for feature_id, body in (conformance.get("features") or {}).items():
        if not body.get("blocking_failure"):
            continue
        for code, messages in _messages_by_code(body).items():
            if code not in REPAIRERS:
                continue
            for message in messages:
                match = PRIM_PATTERN.search(message)
                if not match:
                    continue
                key = (code, match.group(1))
                if key in seen:
                    continue
                seen.add(key)
                findings.append({
                    "rule_id": code,
                    "prim": match.group(1),
                    "feature": feature_id,
                    "message": message,
                    "repairable": True,
                })
    return {"findings": findings}


def plan(conformance: Dict[str, Any], findings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Split findings into licensed / unlicensed / unsupported buckets."""
    derived = findings or derive_findings(conformance)
    failing_codes = set()
    for feature_id, body in (conformance.get("features") or {}).items():
        if body.get("blocking_failure"):
            failing_codes.update(_messages_by_code(body).keys())

    allowed, unlicensed, unsupported = [], [], []
    for finding in derived.get("findings", []):
        code = normalize_code(str(finding.get("rule_id")))
        if code not in REPAIRERS:
            unsupported.append({**finding, "rule_id": code, "reason": "no repairer for this requirement"})
        elif code not in failing_codes:
            unlicensed.append({**finding, "rule_id": code, "reason": "requirement not failing under this profile"})
        else:
            allowed.append({**finding, "rule_id": code, "action": REPAIRERS[code]["action"]})

    return {
        "allowed": allowed,
        "unlicensed": unlicensed,
        "unsupported": unsupported,
        "failing_codes": sorted(failing_codes),
        "repairable_codes": sorted({item["rule_id"] for item in allowed}),
        "blocked_codes": sorted({item["rule_id"] for item in unsupported}),
    }


def apply(input_usd: str, actions: List[Dict[str, Any]], output_usd: str) -> Dict[str, Any]:
    """Apply the licensed repairs and export a candidate asset."""
    input_path = os.path.abspath(input_usd)
    output_path = os.path.abspath(output_usd)
    if input_path == output_path:
        raise ValueError("output must differ from input; source assets are never modified")

    stage = Usd.Stage.Open(input_path)
    if stage is None:
        raise ValueError(f"could not open input USD: {input_path}")

    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for action in actions:
        code = action["rule_id"]
        prim = stage.GetPrimAtPath(action["prim"])
        if not prim or not prim.IsValid():
            skipped.append({"rule_id": code, "prim": action["prim"], "reason": "prim not found"})
            continue

        removed: List[str] = []
        if code == "RB.COL.001" and not prim.IsA(UsdGeom.Gprim):
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                prim.RemoveAPI(UsdPhysics.CollisionAPI)
                removed.append("PhysicsCollisionAPI")
        elif code == "RB.COL.002" and not prim.IsA(UsdGeom.Mesh):
            if prim.HasAPI(UsdPhysics.MeshCollisionAPI):
                prim.RemoveAPI(UsdPhysics.MeshCollisionAPI)
                prim.RemoveProperty("physics:approximation")
                removed.append("PhysicsMeshCollisionAPI")
                removed.append("physics:approximation")

        if removed:
            applied.append({"rule_id": code, "prim": action["prim"],
                            "prim_type": str(prim.GetTypeName()), "removed": removed})
        else:
            skipped.append({"rule_id": code, "prim": action["prim"],
                            "reason": "already compliant or prim type changed"})

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if not stage.GetRootLayer().Export(output_path):
        raise ValueError(f"failed to export repaired USD: {output_path}")

    return {
        "schema_version": "1.0",
        "operation": "profile-gated-repair",
        "input_usd": input_path,
        "output_usd": output_path,
        "applied": applied,
        "skipped": skipped,
        "repairable_rules": sorted(REPAIRERS),
    }
