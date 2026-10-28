"""The handoff is a data seam: these tests need no pxr and no USD stage."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import handoff  # noqa: E402

REPORT = {
    "file": "/assets/example/simready_usd/example.usda",
    "visual_gate": {
        "status": "pass",
        "facts": {
            "parts": [
                {"prim": "/cab/geometry/carcass", "bbox": [-0.345, -0.19, 0.0, 0.345, 0.19, 0.67],
                 "materials": ["/cab/looks/mat"]},
                {"prim": "/cab/geometry/drawer_01", "bbox": [-0.315, -0.19, 0.08, 0.315, 0.14, 0.27],
                 "materials": ["/cab/looks/mat"]},
                {"prim": "/cab/geometry/drawer_02", "bbox": [-0.315, -0.19, 0.28, 0.315, 0.14, 0.47],
                 "materials": ["/cab/looks/mat"]},
            ]
        },
    },
}

INTENT = {
    "schema": "simready-intent/1",
    "root": "/cab/geometry/carcass",
    "moving": [
        {"prim": "/cab/geometry/drawer_01", "joint": {"type": "prismatic", "axis": "Y", "pull": "negative"},
         "travel": {"policy": "bbox_depth_minus_reserve", "reserve_m": 0.05},
         "mass": {"policy": "volume_x_density", "density_kg_m3": 700.0}},
        {"prim": "/cab/geometry/drawer_02", "joint": {"type": "prismatic", "axis": "Y", "pull": "negative"},
         "travel": {"policy": "bbox_depth_minus_reserve", "reserve_m": 0.05},
         "mass": {"policy": "volume_x_density", "density_kg_m3": 700.0}},
    ],
}


def test_travel_and_pivot_are_derived_from_geometry():
    document = handoff.build(REPORT, INTENT)
    first = document["moving"][0]
    assert first["depth_m"] == pytest.approx(0.33, abs=1e-6)
    assert first["travel_m"] == pytest.approx(0.28, abs=1e-6)
    # pulled toward -Y, so the pivot sits on the -Y face
    assert first["pivot"][1] == pytest.approx(-0.19, abs=1e-6)


def test_digest_is_stable_and_sensitive_to_geometry():
    first = handoff.build(REPORT, INTENT)
    again = handoff.build(copy.deepcopy(REPORT), copy.deepcopy(INTENT))
    assert first["facts_digest"] == again["facts_digest"]

    moved = copy.deepcopy(REPORT)
    moved["visual_gate"]["facts"]["parts"][1]["bbox"][4] = 0.15
    assert handoff.build(moved, INTENT)["facts_digest"] != first["facts_digest"]


def test_undeclared_part_cannot_be_inferred():
    intent = copy.deepcopy(INTENT)
    intent["moving"][0]["prim"] = "/cab/geometry/drawer_99"
    with pytest.raises(handoff.HandoffError) as error:
        handoff.build(REPORT, intent)
    assert {issue["code"] for issue in error.value.issues} == {"HO.04"}


def test_travel_larger_than_depth_is_rejected():
    intent = copy.deepcopy(INTENT)
    intent["moving"][0]["travel"] = {"policy": "bbox_depth_minus_reserve", "reserve_m": 0.0}
    with pytest.raises(handoff.HandoffError) as error:
        handoff.build(REPORT, intent)
    assert "HO.08" in {issue["code"] for issue in error.value.issues}


def test_unknown_axis_is_rejected():
    intent = copy.deepcopy(INTENT)
    intent["moving"][0]["joint"] = {"type": "prismatic", "axis": "W", "pull": "negative"}
    with pytest.raises(handoff.HandoffError) as error:
        handoff.build(REPORT, intent)
    assert "HO.05" in {issue["code"] for issue in error.value.issues}


def test_part_outside_root_is_reported_with_modeling_owner():
    report = copy.deepcopy(REPORT)
    report["visual_gate"]["facts"]["parts"][1]["bbox"] = [3.0, 3.0, 3.0, 3.3, 3.3, 3.3]
    with pytest.raises(handoff.HandoffError) as error:
        handoff.build(report, INTENT)
    owners = {issue["code"]: issue["owner"] for issue in error.value.issues}
    assert owners.get("HO.06") == "modeling"


def test_report_without_gate_facts_is_a_tooling_error():
    with pytest.raises(handoff.HandoffError) as error:
        handoff.build({"file": "x.usda"}, INTENT)
    assert error.value.issues[0]["owner"] == "tooling"
