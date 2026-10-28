"""Authoring consumes the handoff; geometry is never re-derived here."""
import copy
import os
import sys

import pytest

pytest.importorskip("pxr")
from pxr import Gf, Usd, UsdGeom, UsdPhysics

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import author  # noqa: E402
import handoff as handoff_module  # noqa: E402
import visual_gate  # noqa: E402
from test_visual_gate import build as build_visual  # noqa: E402

INTENT = {
    "schema": "simready-intent/1",
    "root": "/cab/geometry/carcass",
    "moving": [
        {"prim": "/cab/geometry/drawer_01", "joint": {"type": "prismatic", "axis": "Y", "pull": "negative"},
         "travel": {"policy": "bbox_depth_minus_reserve", "reserve_m": 0.05},
         "mass": {"policy": "volume_x_density", "density_kg_m3": 700}},
        {"prim": "/cab/geometry/drawer_02", "joint": {"type": "prismatic", "axis": "Y", "pull": "negative"},
         "travel": {"policy": "bbox_depth_minus_reserve", "reserve_m": 0.05},
         "mass": {"policy": "volume_x_density", "density_kg_m3": 700}},
    ],
}


def _handoff_for(stage):
    report = {"file": "fixture", "visual_gate": visual_gate.run_gate(stage, {}, {"moving": INTENT["moving"]})}
    return handoff_module.build(report, INTENT)


def test_authors_joints_with_handoff_values(tmp_path):
    path, stage = build_visual(tmp_path)
    document = _handoff_for(stage)
    out = str(tmp_path / "authored.usda")

    audit = author.author(path, document, out)
    assert len(audit["moving"]) == 2
    assert audit["moving"][0]["travel_m"] == pytest.approx(0.28, abs=1e-6)

    authored = Usd.Stage.Open(out)
    joints = [prim for prim in authored.Traverse() if prim.IsA(UsdPhysics.PrismaticJoint)]
    assert len(joints) == 2
    for prim in joints:
        joint = UsdPhysics.PrismaticJoint(prim)
        assert joint.GetAxisAttr().Get() == "Y"
        assert joint.GetLowerLimitAttr().Get() == pytest.approx(-0.28, abs=1e-6)
        assert joint.GetUpperLimitAttr().Get() == pytest.approx(0.0, abs=1e-6)
    bodies = [prim for prim in authored.Traverse() if prim.HasAPI(UsdPhysics.RigidBodyAPI)]
    assert len(bodies) == 3
    for prim in bodies:
        assert UsdPhysics.MassAPI(prim).GetMassAttr().Get() > 0


def test_refuses_input_that_already_has_physics(tmp_path):
    path, stage = build_visual(tmp_path, physics=True)
    document = _handoff_for(stage)
    with pytest.raises(author.AuthoringError) as error:
        author.author(path, document, str(tmp_path / "out.usda"))
    assert "AU.03" in {issue["code"] for issue in error.value.issues}


def test_rotated_moving_part_gets_a_compensating_joint_frame(tmp_path):
    path, stage = build_visual(tmp_path)
    UsdGeom.Xformable(stage.GetPrimAtPath("/cab/geometry/drawer_01")).AddRotateYOp().Set(5.0)
    stage.GetRootLayer().Save()

    reopened = Usd.Stage.Open(path)
    document = _handoff_for(reopened)
    out = str(tmp_path / "out.usda")
    audit = author.author(path, document, out)

    rotated = [item for item in audit["moving"] if item["prim"].endswith("drawer_01")][0]
    assert rotated["rotation_offset_deg"] == pytest.approx(5.0, abs=1e-2)

    authored = Usd.Stage.Open(out)
    joints = [prim for prim in authored.Traverse() if prim.IsA(UsdPhysics.PrismaticJoint)
              and prim.GetPath().pathString.endswith("drawer_01")]
    assert len(joints) == 1
    quat = UsdPhysics.PrismaticJoint(joints[0]).GetLocalRot1Attr().Get()
    assert abs(float(quat.GetReal())) < 0.9999  # not identity: the frame absorbs the rotation


def test_non_unit_scale_is_refused(tmp_path):
    path, stage = build_visual(tmp_path)
    UsdGeom.Xformable(stage.GetPrimAtPath("/cab/geometry/drawer_01")).AddScaleOp().Set(
        Gf.Vec3f(1.05, 1.05, 1.05))
    stage.GetRootLayer().Save()

    reopened = Usd.Stage.Open(path)
    document = _handoff_for(reopened)
    with pytest.raises(author.AuthoringError) as error:
        author.author(path, document, str(tmp_path / "out.usda"))
    assert "AU.09" in {issue["code"] for issue in error.value.issues}


def test_never_writes_over_the_input(tmp_path):
    path, stage = build_visual(tmp_path)
    document = _handoff_for(stage)
    with pytest.raises(author.AuthoringError) as error:
        author.author(path, document, path)
    assert error.value.issues[0]["code"] == "AU.01"
