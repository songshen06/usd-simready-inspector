"""The runtime request is a data contract; these tests need no pxr."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import runtime_request  # noqa: E402

CONFORMANCE = {
    "asset": "/assets/example/simready_usd/example.usda",
    "profile_decision": {"profile_id": "Robotics-Prop", "profile_version": "4.0.0",
                         "source": "validated", "confidence": 0.95},
    "conformance": {
        "status": "conformant",
        "profile_id": "Robotics-Prop", "profile_version": "4.0.0",
        "simready_validate_version": "2026.8.0", "tier_versions": {"simready-foundation-tier-core": "2026.8.1"},
        "deferred_to_runtime": {"physx": ["FET_000_PHYSX"], "newton": ["FET_000_NEWTON"]},
    },
    "runtime_handoff": {
        "units": {"up_axis": "Z", "meters_per_unit": 1.0, "kilograms_per_unit": 1.0},
        "gravity": {"direction": [0, 0, -1], "magnitude": 9.81},
        "bodies": [
            {"path": "/example/cabinet", "kinematic": True, "mass_kg": 18.5},
            {"path": "/example/drawer_01", "kinematic": False, "mass_kg": 4.4},
            {"path": "/example/drawer_02", "kinematic": False, "mass_kg": 4.4},
            {"path": "/example/drawer_03", "kinematic": False, "mass_kg": 4.4},
        ],
        "joints": [
            {"path": "/example/j1", "axis": "Y", "lower_limit": -0.28, "upper_limit": 0.0},
            {"path": "/example/j2", "axis": "Y", "lower_limit": -0.28, "upper_limit": 0.0},
            {"path": "/example/j3", "axis": "Y", "lower_limit": -0.28, "upper_limit": 0.0},
        ],
        "colliders": [{"path": "/example/mesh", "approximation": "convexHull"}],
        "reproduce": "simready-validate --profile Robotics-Prop --version 4.0.0 example.usda",
    },
}

HANDOFF = {"moving": [{"prim": "/example/drawer_01", "travel_m": 0.28},
                      {"prim": "/example/drawer_02", "travel_m": 0.28},
                      {"prim": "/example/drawer_03", "travel_m": 0.26}]}

REPORT = {
    "file": "/assets/example/simready_usd/example.usda",
    "physics": {
        "rigid_bodies": [
            {"path": "/example/cabinet", "kinematic_enabled": True},
            {"path": "/example/drawer_01", "kinematic_enabled": False},
            {"path": "/example/drawer_02", "kinematic_enabled": False},
            {"path": "/example/drawer_03", "kinematic_enabled": False},
        ],
        "joints": [{"path": "/example/j1"}, {"path": "/example/j2"}, {"path": "/example/j3"}],
    },
}


def test_request_freezes_counts_and_travel():
    request = runtime_request.build(CONFORMANCE, HANDOFF)
    assert request["schema"] == runtime_request.SCHEMA
    assert request["authoritative"] is False
    assert request["expected"] == {"rigid_bodies": 4, "joints": 3, "kinematic_bases": 1,
                                   "total_mass_kg": 31.7, "max_travel_m": 0.28}
    assert request["profile"]["validated_with"]["simready_validate"] == "2026.8.0"


def test_reproduce_commands_name_the_downstream_cli():
    request = runtime_request.build(CONFORMANCE, HANDOFF, foundation_tag="v2026.04.1")
    assert request["reproduce"]["runtime"].startswith("omni-asset-cli physics-hit-test")
    assert "--foundation-tag v2026.04.1" in request["reproduce"]["foundation"]


def test_compare_passes_against_matching_report():
    request = runtime_request.build(CONFORMANCE, HANDOFF)
    statuses = {check["name"]: check["status"] for check in runtime_request.compare(request, REPORT)}
    assert statuses["runtime_request_joint_count"] == "passed"
    assert statuses["runtime_request_body_count"] == "passed"
    assert statuses["runtime_request_kinematic_bases"] == "passed"
    assert statuses["runtime_request_authoritative"] == "warning"


def test_compare_flags_a_stale_request():
    request = runtime_request.build(CONFORMANCE, HANDOFF)
    stale = copy.deepcopy(REPORT)
    stale["physics"]["joints"] = [{"path": "/example/j1"}]
    checks = runtime_request.compare(request, stale)
    failed = {check["name"] for check in checks if check["status"] == "failed"}
    assert failed == {"runtime_request_joint_count"}


def test_compare_requires_contact_evidence_when_runtime_report_is_present():
    request = runtime_request.build(CONFORMANCE, HANDOFF)
    no_contact = runtime_request.compare(request, REPORT, {"checks": {}})
    assert "runtime_contact_evidence" in {check["name"] for check in no_contact if check["status"] == "failed"}

    with_contact = runtime_request.compare(request, REPORT, {"checks": {"contact_report_detected": True}})
    assert "runtime_contact_evidence" not in {check["name"] for check in with_contact if check["status"] == "failed"}


def test_missing_handoff_still_produces_a_request_without_travel():
    request = runtime_request.build(CONFORMANCE, None)
    assert request["expected"]["max_travel_m"] is None
    assert request["expected"]["joints"] == 3
