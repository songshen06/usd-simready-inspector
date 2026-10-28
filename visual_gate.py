"""Visual-layer gate.

Runs *before* any SimReady authoring. Its job is to reject geometry that cannot
be simulated reliably, and to publish the facts (bbox, materials, part identity)
that the handoff and authoring steps consume.

The gate never modifies the asset and never guesses which prims move: moving
parts must be declared, either in a handoff file or not at all.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

PHYSICS_API_HINTS = ("Physics", "Physx", "Newton", "Mjc")


def _has_physics(prim: Usd.Prim) -> List[str]:
    found = []
    for schema in prim.GetAppliedSchemas():
        name = str(schema)
        if any(hint.lower() in name.lower() for hint in PHYSICS_API_HINTS):
            found.append(name)
    type_name = str(prim.GetTypeName())
    if any(hint in type_name for hint in ("Physics", "Physx")):
        found.append(type_name)
    return found


def _bbox(cache: UsdGeom.BBoxCache, prim: Usd.Prim) -> Optional[List[float]]:
    try:
        aligned = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        if aligned.IsEmpty():
            return None
        low, high = aligned.GetMin(), aligned.GetMax()
        return [round(float(low[i]), 4) for i in range(3)] + [round(float(high[i]), 4) for i in range(3)]
    except Exception:
        return None


def _is_xformable(prim: Usd.Prim) -> bool:
    """Xform or a typeless grouping prim (DCC exporters emit both)."""
    return prim.IsA(UsdGeom.Xform) or str(prim.GetTypeName()) == ""


def _parts(stage: Usd.Stage, cache: UsdGeom.BBoxCache) -> List[Dict[str, Any]]:
    """Leaf grouping prims: they hold meshes, but no descendant group does."""
    parts: List[Dict[str, Any]] = []
    for prim in stage.Traverse():
        if prim.IsA(UsdGeom.Mesh):
            continue
        meshes = [child for child in Usd.PrimRange(prim) if child.IsA(UsdGeom.Mesh)]
        if not meshes:
            continue
        nested_groups = [child for child in prim.GetChildren()
                         if not child.IsA(UsdGeom.Mesh)
                         and any(desc.IsA(UsdGeom.Mesh) for desc in Usd.PrimRange(child))]
        if nested_groups:
            continue
        parts.append({
            "prim": prim.GetPath().pathString,
            "meshes": len(meshes),
            "bbox": _bbox(cache, prim),
            "materials": sorted({m for m in (_material_of(c) for c in meshes) if m}),
        })
    return parts


def _material_of(prim: Usd.Prim) -> Optional[str]:
    try:
        bound, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
        return bound.GetPath().pathString if bound else None
    except Exception:
        return None


def _check(check_id: str, name: str, ok: bool, detail: str, owner: str = "modeling") -> Dict[str, Any]:
    return {"id": check_id, "name": name, "status": "pass" if ok else "fail",
            "owner": owner, "detail": detail}


def load_handoff(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _moving_prims(handoff: Dict[str, Any]) -> List[str]:
    moving = handoff.get("moving") or []
    paths = []
    for item in moving:
        if isinstance(item, str):
            paths.append(item)
        elif isinstance(item, dict) and item.get("prim"):
            paths.append(str(item["prim"]))
    return paths


def run_gate(stage: Usd.Stage, report: Dict[str, Any], handoff: Optional[Dict[str, Any]] = None,
             baseline_paths: Optional[List[str]] = None) -> Dict[str, Any]:
    handoff = handoff or {}
    checks: List[Dict[str, Any]] = []
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), ["default", "render"])

    up_axis = stage.GetMetadata("upAxis")
    meters = stage.GetMetadata("metersPerUnit")
    default_prim = stage.GetDefaultPrim()
    mesh_prims = [prim for prim in stage.Traverse() if prim.IsA(UsdGeom.Mesh)]

    checks.append(_check("GATE.01", "upAxis declared as Z", up_axis == "Z", f"upAxis={up_axis}")
                  if up_axis == "Z" else
                  _check("GATE.01", "upAxis declared as Z", False, f"upAxis={up_axis!r}"))
    checks.append(_check("GATE.02", "metersPerUnit declared as 1.0",
                         meters is not None and abs(float(meters) - 1.0) < 1e-9,
                         f"metersPerUnit={meters}"))
    checks.append(_check("GATE.03", "defaultPrim authored and valid",
                         bool(default_prim and default_prim.IsValid()),
                         f"defaultPrim={default_prim.GetPath().pathString if default_prim else None}"))

    physics_hits = []
    for prim in stage.Traverse():
        apis = _has_physics(prim)
        if apis:
            physics_hits.append({"prim": prim.GetPath().pathString, "schemas": apis})
    checks.append(_check("GATE.04", "visual layer carries no physics schemas", not physics_hits,
                         "clean" if not physics_hits else f"{len(physics_hits)} prims carry physics",
                         owner="modeling"))

    unbound = []
    for prim in mesh_prims:
        if not _material_of(prim):
            unbound.append(prim.GetPath().pathString)
    checks.append(_check("GATE.05", "every render mesh has a material binding", not unbound,
                         "all bound" if not unbound else f"{len(unbound)} unbound: {unbound[:3]}"))

    outside = []
    if default_prim:
        root_path = default_prim.GetPath().pathString
        for prim in stage.Traverse():
            path = prim.GetPath().pathString
            if path != root_path and not path.startswith(root_path + "/"):
                outside.append(path)
    checks.append(_check("GATE.06", "single hierarchy under defaultPrim", not outside,
                         "contained" if not outside else f"outside: {outside[:3]}"))

    moving_paths = _moving_prims(handoff)
    if moving_paths:
        missing, malformed = [], []
        for path in moving_paths:
            prim = stage.GetPrimAtPath(path)
            if not prim or not prim.IsValid():
                missing.append(path)
                continue
            meshes = [c for c in Usd.PrimRange(prim) if c.IsA(UsdGeom.Mesh)]
            if not _is_xformable(prim) or not meshes:
                malformed.append(path)
        checks.append(_check("GATE.07", "declared moving parts resolve to Xforms with mesh descendants",
                             not missing and not malformed,
                             "ok" if not missing and not malformed
                             else f"missing={missing} malformed={malformed}"))

        root_path = handoff.get("root")
        if root_path:
            root_prim = stage.GetPrimAtPath(root_path)
            root_bbox = _bbox(cache, root_prim) if root_prim and root_prim.IsValid() else None
            if root_bbox:
                eclipsed = []
                for path in moving_paths:
                    prim = stage.GetPrimAtPath(path)
                    box = _bbox(cache, prim) if prim and prim.IsValid() else None
                    if not box:
                        continue
                    inside = all(root_bbox[i] - 1e-4 <= box[i] and box[i + 3] <= root_bbox[i + 3] + 1e-4
                                 for i in range(3))
                    if not inside:
                        eclipsed.append(path)
                checks.append(_check(
                    "GATE.08", "moving parts sit inside the declared root bounds", not eclipsed,
                    "ok" if not eclipsed else f"outside root {root_path}: {eclipsed}"))
    else:
        checks.append(_check("GATE.07", "declared moving parts resolve", True,
                             "skipped: no handoff supplied (facts only)", owner="intent"))

    if baseline_paths is not None:
        current = {prim.GetPath().pathString for prim in stage.Traverse()}
        added = sorted(current - set(baseline_paths))
        removed = sorted(set(baseline_paths) - current)
        checks.append(_check("GATE.09", "re-export keeps prim paths stable", not added and not removed,
                             "stable" if not added and not removed
                             else f"added={added[:3]} removed={removed[:3]}"))

    parts = _parts(stage, cache)

    status = "pass" if all(check["status"] == "pass" for check in checks) else "fail"
    return {
        "gate": "visual",
        "status": status,
        "checks": checks,
        "failed": [check for check in checks if check["status"] == "fail"],
        "facts": {
            "up_axis": up_axis,
            "meters_per_unit": meters,
            "default_prim": default_prim.GetPath().pathString if default_prim else None,
            "mesh_count": len(mesh_prims),
            "parts": parts,
            "physics_prims": physics_hits,
            "moving": moving_paths,
        },
    }


def exit_code(gate: Dict[str, Any]) -> int:
    return 0 if gate.get("status") == "pass" else 1


def load_baseline(path: Optional[str]) -> Optional[List[str]]:
    """Prim paths from a previous inspection report, for the idempotency check."""
    if not path:
        return None
    with open(path, encoding="utf-8") as handle:
        previous = json.load(handle)
    for key in ("prim_paths",):
        if isinstance(previous.get(key), list):
            return [str(item) for item in previous[key]]
    prims = previous.get("prims")
    if isinstance(prims, list):
        return [str(item.get("path")) for item in prims if isinstance(item, dict) and item.get("path")]
    return None
