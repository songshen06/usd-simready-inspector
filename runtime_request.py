"""Runtime request: the one document the downstream harness reads.

The static pipeline knows what *should* be true (bodies, joints, travel,
mass); the GPU harness knows what *is* true after a headless Isaac Sim run.
This module freezes the first half into a versioned JSON so the two sides can
agree without sharing code, and provides the comparison used by `diagnose`.

No pxr dependency: it is data in, data out.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional

SCHEMA = "simready-runtime-request/1"


def _asset_key(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        with open(path, "rb") as handle:
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
            return "sha256:" + digest.hexdigest()
    except OSError:
        return None


def build(conformance: Dict[str, Any], handoff: Optional[Dict[str, Any]] = None, *,
          asset: Optional[str] = None, foundation_tag: Optional[str] = None,
          package: str = "physics-prop") -> Dict[str, Any]:
    """Freeze the static side's expectations for the runtime harness."""
    handoff = handoff or {}
    block = conformance.get("conformance") or {}
    handoff_block = conformance.get("runtime_handoff") or {}
    decision = conformance.get("profile_decision") or {}

    bodies = handoff_block.get("bodies") or []
    joints = handoff_block.get("joints") or []
    colliders = handoff_block.get("colliders") or []

    travel_values = [float(item.get("travel_m", 0.0)) for item in handoff.get("moving") or []
                     if item.get("travel_m") is not None]
    total_mass = sum(float(body.get("mass_kg") or 0.0) for body in bodies)
    kinematic = sum(1 for body in bodies if body.get("kinematic"))

    asset_path = asset or handoff_block.get("asset") or conformance.get("asset")
    document: Dict[str, Any] = {
        "schema": SCHEMA,
        "asset": asset_path,
        "asset_digest": _asset_key(asset_path),
        "authoritative": False,
        "profile": {
            "id": decision.get("profile_id") or block.get("profile_id"),
            "version": decision.get("profile_version") or block.get("profile_version"),
            "source": decision.get("source"),
            "confidence": decision.get("confidence"),
            "validated_with": {
                "simready_validate": block.get("simready_validate_version"),
                "tier_versions": block.get("tier_versions", {}),
            },
            "status": block.get("status"),
        },
        "units": handoff_block.get("units", {}),
        "gravity": handoff_block.get("gravity", {}),
        "bodies": bodies,
        "joints": joints,
        "colliders": colliders,
        "expected": {
            "rigid_bodies": len(bodies),
            "joints": len(joints),
            "kinematic_bases": kinematic,
            "total_mass_kg": round(total_mass, 3),
            "max_travel_m": round(max(travel_values), 4) if travel_values else None,
        },
        "deferred_to_runtime": sorted((block.get("deferred_to_runtime") or {}).keys()),
        "reproduce": {
            "static": handoff_block.get("reproduce"),
            "foundation": (
                f"omni-asset-cli foundation-validate {package} "
                f"--foundation-tag {foundation_tag or '<pinned-tag>'} {asset_path or '<asset>'}"
            ),
            "runtime": f"omni-asset-cli physics-hit-test {asset_path or '<asset>'} --hit-mode top-drop",
        },
    }
    return document


def _check(name: str, expected: Any, observed: Any, status: str, reason: str = "") -> Dict[str, Any]:
    return {"name": name, "expected": expected, "observed": observed,
            "status": status, "reason": reason}


def compare(request: Dict[str, Any], report: Dict[str, Any],
            runtime_report: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Check the request against the asset's own inspection facts."""
    checks: List[Dict[str, Any]] = []
    expected = request.get("expected") or {}
    physics = (report.get("physics") or {})

    joints_observed = len(physics.get("joints") or [])
    want = expected.get("joints")
    checks.append(_check("runtime_request_joint_count", want, joints_observed,
                         "passed" if want is None or want == joints_observed else "failed",
                         "" if want == joints_observed else
                         "the runtime request is stale: regenerate it from the current asset"))

    bodies_observed = len(physics.get("rigid_bodies") or [])
    want_bodies = expected.get("rigid_bodies")
    checks.append(_check("runtime_request_body_count", want_bodies, bodies_observed,
                         "passed" if want_bodies in (None, bodies_observed) else "failed",
                         "" if want_bodies == bodies_observed else
                         "the runtime request is stale: regenerate it from the current asset"))

    kinematic_observed = sum(1 for body in physics.get("rigid_bodies") or []
                             if body.get("kinematic_enabled"))
    want_kinematic = expected.get("kinematic_bases")
    checks.append(_check("runtime_request_kinematic_bases", want_kinematic, kinematic_observed,
                         "passed" if want_kinematic in (None, kinematic_observed) else "failed",
                         "" if want_kinematic == kinematic_observed else
                         "base body count changed; someone re-authored the asset"))

    checks.append(_check(
        "runtime_request_authoritative", False, bool(request.get("authoritative", False)),
        "warning",
        "local conformance is a pre-check; downstream foundation-validate is authoritative",
    ))

    if runtime_report is not None:
        summary = runtime_report.get("checks") or {}
        detected = bool(summary.get("contact_report_detected")
                        or (runtime_report.get("hit_analysis") or {}).get("contact_detected"))
        checks.append(_check(
            "runtime_contact_evidence", True, detected,
            "passed" if detected else "failed",
            "" if detected else "no PhysX contact evidence in the runtime report",
        ))
    return checks


def summarize(request: Dict[str, Any]) -> str:
    expected = request.get("expected") or {}
    return (f"{expected.get('rigid_bodies')} bodies / {expected.get('joints')} joints / "
            f"max travel {expected.get('max_travel_m')} m")
