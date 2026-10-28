"""Gate behaviour is locked by construction: each case builds its own asset."""
import json
import os
import sys

import pytest

pxr = pytest.importorskip("pxr")
from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import visual_gate  # noqa: E402


def _box(stage, path, center, size):
    mesh = UsdGeom.Mesh.Define(stage, path)
    cx, cy, cz = center
    sx, sy, sz = size
    x0, x1 = cx - sx / 2, cx + sx / 2
    y0, y1 = cy - sy / 2, cy + sy / 2
    z0, z1 = cz - sz / 2, cz + sz / 2
    mesh.CreatePointsAttr([(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
                           (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)])
    mesh.CreateFaceVertexCountsAttr([4] * 6)
    mesh.CreateFaceVertexIndicesAttr([0, 3, 2, 1, 4, 5, 6, 7, 0, 1, 5, 4,
                                      1, 2, 6, 5, 2, 3, 7, 6, 3, 0, 4, 7])
    mesh.CreateExtentAttr([(x0, y0, z0), (x1, y1, z1)])
    mesh.CreateSubdivisionSchemeAttr("none")
    return mesh


def build(tmp_path, *, physics=False, unbound=False, stray=False, name="asset.usda"):
    path = str(tmp_path / name)
    stage = Usd.Stage.CreateNew(path)
    stage.SetMetadata("upAxis", "Z")
    stage.SetMetadata("metersPerUnit", 1.0)
    root = UsdGeom.Xform.Define(stage, "/cab").GetPrim()
    stage.SetDefaultPrim(root)
    UsdGeom.Xform.Define(stage, "/cab/geometry")
    material = UsdShade.Material.Define(stage, "/cab/looks/mat")
    shader = UsdShade.Shader.Define(stage, "/cab/looks/mat/s")
    shader.CreateIdAttr("UsdPreviewSurface")
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")

    carcass = _box(stage, "/cab/geometry/carcass/body", (0, 0, 0.335), (0.69, 0.38, 0.67))
    UsdShade.MaterialBindingAPI.Apply(carcass.GetPrim()).Bind(material)
    for index, z in enumerate((0.2, 0.4)):
        UsdGeom.Xform.Define(stage, f"/cab/geometry/drawer_{index + 1:02d}")
        body = _box(stage, f"/cab/geometry/drawer_{index + 1:02d}/body", (0, 0, z), (0.63, 0.33, 0.15))
        if not (unbound and index == 1):
            UsdShade.MaterialBindingAPI.Apply(body.GetPrim()).Bind(material)
        if physics:
            UsdPhysics.CollisionAPI.Apply(body.GetPrim())
    if stray:
        UsdGeom.Xform.Define(stage, "/cab/geometry/drawer_far")
        _box(stage, "/cab/geometry/drawer_far/body", (3.0, 0, 3.0), (0.3, 0.3, 0.3))
    stage.GetRootLayer().Save()
    return path, stage


HANDOFF = {
    "schema": "simready-handoff/1",
    "root": "/cab/geometry/carcass",
    "moving": [{"prim": "/cab/geometry/drawer_01"}, {"prim": "/cab/geometry/drawer_02"}],
}


def _gate(stage, handoff=None, baseline=None):
    return visual_gate.run_gate(stage, {}, handoff or {}, baseline)


def test_clean_visual_layer_passes(tmp_path):
    path, stage = build(tmp_path)
    gate = _gate(stage, HANDOFF)
    assert gate["status"] == "pass", gate["failed"]
    assert gate["facts"]["moving"] == ["/cab/geometry/drawer_01", "/cab/geometry/drawer_02"]


def test_physics_in_visual_layer_is_rejected(tmp_path):
    path, stage = build(tmp_path, physics=True)
    gate = _gate(stage)
    failed = {check["id"] for check in gate["failed"]}
    assert gate["status"] == "fail"
    assert "GATE.04" in failed


def test_unbound_mesh_is_rejected(tmp_path):
    path, stage = build(tmp_path, unbound=True)
    gate = _gate(stage)
    assert "GATE.05" in {check["id"] for check in gate["failed"]}


def test_moving_part_outside_root_is_rejected(tmp_path):
    path, stage = build(tmp_path, stray=True)
    handoff = dict(HANDOFF, moving=["/cab/geometry/drawer_01", "/cab/geometry/drawer_far"])
    gate = _gate(stage, handoff)
    assert "GATE.08" in {check["id"] for check in gate["failed"]}


def test_missing_moving_prim_is_rejected(tmp_path):
    path, stage = build(tmp_path)
    gate = _gate(stage, {"moving": ["/cab/geometry/drawer_99"]})
    assert "GATE.07" in {check["id"] for check in gate["failed"]}


def test_path_drift_against_baseline_is_rejected(tmp_path):
    path, stage = build(tmp_path)
    baseline = [prim.GetPath().pathString for prim in stage.Traverse()]
    stable = _gate(stage, HANDOFF, baseline)
    assert stable["status"] == "pass"

    UsdGeom.Xform.Define(stage, "/cab/geometry/extra")
    drifted = _gate(stage, HANDOFF, baseline)
    assert "GATE.09" in {check["id"] for check in drifted["failed"]}
