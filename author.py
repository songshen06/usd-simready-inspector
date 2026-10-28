"""Author SimReady physics from a handoff document.

The input is the *visual* layer plus a handoff; geometry is never inferred here.
Pivot, travel, axis and mass policy all come from the handoff, which derived them
from measured geometry in the previous step. Anything the handoff does not
declare is left alone.

Deliberately narrow: this authors rigid bodies, masses, colliders, a physics
scene and prismatic joints. It does not repair meshes, guess moving parts, or
decide conformance.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any, Dict, List, Optional

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

PHYSICS_HINTS = ("Physics", "Physx", "Newton", "Mjc")


class AuthoringError(Exception):
    def __init__(self, issues: List[Dict[str, str]]):
        super().__init__("; ".join(issue["message"] for issue in issues))
        self.issues = issues


def _issue(code: str, message: str, owner: str = "handoff") -> Dict[str, str]:
    return {"code": code, "message": message, "owner": owner}


def _has_physics(prim: Usd.Prim) -> bool:
    for schema in prim.GetAppliedSchemas():
        if any(hint.lower() in str(schema).lower() for hint in PHYSICS_HINTS):
            return True
    return any(hint in str(prim.GetTypeName()) for hint in ("Physics", "Physx"))


def _mesh_volume(prim: Usd.Prim, cache: UsdGeom.XformCache) -> float:
    """Signed-tetrahedron volume, so hollow parts do not inflate the mass."""
    mesh = UsdGeom.Mesh(prim)
    points = mesh.GetPointsAttr().Get() or []
    counts = mesh.GetFaceVertexCountsAttr().Get() or []
    indices = mesh.GetFaceVertexIndicesAttr().Get() or []
    if not points or not counts:
        return 0.0
    matrix = cache.GetLocalToWorldTransform(prim)
    world = [matrix.Transform(Gf.Vec3d(point)) for point in points]
    volume = 0.0
    cursor = 0
    for count in counts:
        face = indices[cursor:cursor + count]
        cursor += count
        if len(face) < 3:
            continue
        anchor = Gf.Vec3d(world[face[0]])
        for i in range(1, len(face) - 1):
            a = Gf.Vec3d(world[face[i]])
            b = Gf.Vec3d(world[face[i + 1]])
            volume += Gf.Dot(anchor, Gf.Cross(a - anchor, b - anchor)) / 6.0
    return abs(volume)


def _subtree_meshes(prim: Usd.Prim) -> List[Usd.Prim]:
    return [child for child in Usd.PrimRange(prim) if child.IsA(UsdGeom.Mesh)]


def author(input_usd: str, handoff: Dict[str, Any], output_usd: str) -> Dict[str, Any]:
    if os.path.abspath(input_usd) == os.path.abspath(output_usd):
        raise AuthoringError([_issue("AU.01", "output must differ from input", owner="tooling")])

    stage = Usd.Stage.Open(input_usd)
    if stage is None:
        raise AuthoringError([_issue("AU.02", f"cannot open {input_usd}", owner="tooling")])

    issues: List[Dict[str, str]] = []
    for prim in stage.Traverse():
        if _has_physics(prim):
            issues.append(_issue("AU.03", f"input already carries physics at {prim.GetPath()}",
                                 owner="modeling"))
    if issues:
        raise AuthoringError(issues)

    movers = handoff.get("moving") or []
    if not movers:
        raise AuthoringError([_issue("AU.04", "handoff declares no moving parts")])
    root_prim = stage.GetPrimAtPath(handoff.get("root") or "")
    if not root_prim or not root_prim.IsValid():
        raise AuthoringError([_issue("AU.05", f"handoff root not found: {handoff.get('root')}")])

    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    default_prim = stage.GetDefaultPrim()
    asset_root = default_prim.GetPath() if default_prim else stage.GetPseudoRoot().GetPath()
    physics_scope = UsdGeom.Scope.Define(stage, f"{asset_root}/physics").GetPrim()
    joint_scope = UsdGeom.Xform.Define(stage, f"{asset_root}/physics/joints").GetPrim()

    scene = UsdPhysics.Scene.Define(stage, f"{asset_root}/physics/physics_scene")
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    scene.CreateGravityMagnitudeAttr(9.81)

    default_density = float((handoff.get("defaults") or {}).get("density_kg_m3", 700.0))
    root_matrix = cache.GetLocalToWorldTransform(root_prim)

    # ---- static/kinematic base body
    root_meshes = _subtree_meshes(root_prim)
    moving_paths = {entry["prim"] for entry in movers}
    base_volume = sum(_mesh_volume(mesh, cache) for mesh in root_meshes)
    UsdPhysics.RigidBodyAPI.Apply(root_prim)
    UsdPhysics.MassAPI.Apply(root_prim).CreateMassAttr(max(0.1, base_volume * default_density))
    UsdPhysics.RigidBodyAPI(root_prim).CreateKinematicEnabledAttr(True)
    for mesh in root_meshes:
        _ensure_collider(mesh)

    # ---- moving bodies + joints
    created = []
    for entry in movers:
        prim = stage.GetPrimAtPath(entry["prim"])
        if not prim or not prim.IsValid():
            issues.append(_issue("AU.06", f"moving prim missing in stage: {entry['prim']}"))
            continue
        if entry["prim"] in {item["prim"] for item in created}:
            issues.append(_issue("AU.06", f"duplicate moving prim: {entry['prim']}"))
            continue

        volume = sum(_mesh_volume(mesh, cache) for mesh in _subtree_meshes(prim))
        policy = entry.get("mass") or {}
        density = float(policy.get("density_kg_m3", default_density))
        mass = max(0.05, volume * density)

        UsdPhysics.RigidBodyAPI.Apply(prim)
        UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(mass)
        for mesh in _subtree_meshes(prim):
            _ensure_collider(mesh)

        travel = float(entry["travel_m"])
        pivot = Gf.Vec3d(*[float(value) for value in entry["pivot"]])
        moving_matrix = cache.GetLocalToWorldTransform(prim)
        scale = _scale_of(moving_matrix)
        if any(abs(value - 1.0) > 1e-3 for value in scale):
            issues.append(_issue(
                "AU.09",
                f"{entry['prim']}: non-unit scale {tuple(round(v, 4) for v in scale)} breaks joint frames",
                owner="modeling"))
            continue

        # Joint frame rotation is carried by body0 (identity); body1 absorbs the
        # difference, so a rotated part still slides without spinning.
        rotation0 = root_matrix.ExtractRotation()
        rotation1 = moving_matrix.ExtractRotation()
        rotation1_local = rotation1.GetInverse() * rotation0

        joint = UsdPhysics.PrismaticJoint.Define(
            stage, f"{joint_scope.GetPath()}/joint_{os.path.basename(entry['prim'])}")
        joint.CreateBody0Rel().SetTargets([root_prim.GetPath()])
        joint.CreateBody1Rel().SetTargets([prim.GetPath()])
        joint.CreateAxisAttr(entry["joint"]["axis"])
        local0 = root_matrix.GetInverse().Transform(pivot)
        local1 = cache.GetLocalToWorldTransform(prim).GetInverse().Transform(pivot)
        joint.CreateLocalPos0Attr(Gf.Vec3f(*[float(value) for value in local0]))
        joint.CreateLocalPos1Attr(Gf.Vec3f(*[float(value) for value in local1]))
        joint.CreateLocalRot0Attr(Gf.Quatf(1, 0, 0, 0))
        quat = rotation1_local.GetQuat()
        joint.CreateLocalRot1Attr(Gf.Quatf(float(quat.GetReal()),
                                           float(quat.GetImaginary()[0]),
                                           float(quat.GetImaginary()[1]),
                                           float(quat.GetImaginary()[2])))
        if entry["joint"]["pull"] == "negative":
            joint.CreateLowerLimitAttr(-travel)
            joint.CreateUpperLimitAttr(0.0)
        else:
            joint.CreateLowerLimitAttr(0.0)
            joint.CreateUpperLimitAttr(travel)

        created.append({"prim": entry["prim"], "mass_kg": round(mass, 3),
                        "axis": entry["joint"]["axis"], "travel_m": travel,
                        "pivot_local": [round(float(v), 4) for v in local1],
                        "rotation_offset_deg": round(abs(rotation1_local.GetAngle()), 4)})

    if issues:
        raise AuthoringError(issues)

    os.makedirs(os.path.dirname(os.path.abspath(output_usd)), exist_ok=True)
    if not stage.GetRootLayer().Export(output_usd):
        raise AuthoringError([_issue("AU.08", f"failed to export {output_usd}", owner="tooling")])

    return {
        "schema_version": "1.0",
        "operation": "author-from-handoff",
        "input_usd": os.path.abspath(input_usd),
        "output_usd": os.path.abspath(output_usd),
        "root": root_prim.GetPath().pathString,
        "base_mass_kg": round(max(0.1, base_volume * default_density), 3),
        "moving": created,
        "facts_digest": handoff.get("facts_digest"),
        "physics_scope": physics_scope.GetPath().pathString,
    }


def _scale_of(matrix) -> tuple:
    """Column norms of the 3x3 part; 1.0 means no scaling."""
    return tuple(float(Gf.Vec3d(matrix[0][i], matrix[1][i], matrix[2][i]).GetLength())
                 for i in range(3))


def _ensure_collider(mesh: Usd.Prim) -> None:
    UsdPhysics.CollisionAPI.Apply(mesh)
    UsdPhysics.MeshCollisionAPI.Apply(mesh).CreateApproximationAttr("convexHull")


def load_handoff(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def summarize(audit: Dict[str, Any]) -> str:
    parts = ", ".join(f"{item['prim']} {item['mass_kg']}kg" for item in audit.get("moving", []))
    return f"{len(audit.get('moving', []))} moving part(s): {parts}"
