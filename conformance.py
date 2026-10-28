"""Profile conformance for SimReady assets.

Static-only by design: this module answers "does the file satisfy the contract the
downstream simulator will rely on", never "does it simulate correctly".

Validation itself is delegated to the upstream ``simready-validate`` CLI so the
rule set cannot drift from the specification. Assets are never claimed conformant
on a guess: a heuristic profile candidate is only accepted after it passes.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import profile_auto

EXIT_CONFORMANT = 0
EXIT_NON_CONFORMANT = 1
EXIT_NOT_EVALUATED = 2

RUNTIME_BY_SUFFIX = (
    ("_PHYSX", "physx"),
    ("_NEWTON", "newton"),
    ("_MUJOCO", "mujoco"),
    ("_ISAAC", "isaac"),
    ("_RTX", "rtx"),
)


def find_validator(explicit: Optional[str] = None) -> Optional[str]:
    """Locate the upstream validator without importing it (keeps pxr versions apart)."""
    for candidate in (explicit, os.environ.get("SIMREADY_VALIDATE"), "simready-validate"):
        if not candidate:
            continue
        if os.path.sep in candidate:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
            continue
        found = shutil.which(candidate)
        if found:
            return found
    return None


def run_validator(validator: str, asset: str, profile: str,
                  version: Optional[str], timeout: int = 600) -> Optional[Dict[str, Any]]:
    """Run one profile validation and return that asset's entry, or None."""
    handle, out_path = tempfile.mkstemp(suffix=".json", prefix="simready-conformance-")
    os.close(handle)
    command = [validator, "--profile", profile, "--output", out_path]
    if version:
        command += ["--version", version]
    command.append(asset)
    try:
        subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
        with open(out_path, encoding="utf-8") as stream:
            data = json.load(stream)
        return data.get(os.path.abspath(asset)) or (next(iter(data.values())) if data else None)
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration):
        return None
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass


def _runtime_for(feature_id: str) -> Optional[str]:
    for suffix, runtime in RUNTIME_BY_SUFFIX:
        if feature_id.endswith(suffix):
            return runtime
    return None


def summarize_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Split a validator result into required failures and runtime-deferred work."""
    features = result.get("features_summary", {}) or {}
    failing_required: List[str] = []
    deferred: Dict[str, List[Dict[str, Any]]] = {}

    for feature_id, body in features.items():
        if body.get("passed"):
            continue
        entry = {
            "feature": feature_id,
            "version": body.get("version"),
            "failing_requirements": body.get("failing requirements")
            or body.get("unmet requirements") or [],
        }
        if body.get("blocking_failure"):
            failing_required.append(feature_id)
        else:
            runtime = _runtime_for(feature_id) or "capability"
            deferred.setdefault(runtime, []).append(entry)

    return {
        "profile_id": result.get("profile_id"),
        "profile_version": result.get("profile_version"),
        "simready_validate_version": result.get("simready_validate_version"),
        "tier_versions": result.get("tier_versions", {}),
        "status": "non_conformant" if failing_required else "conformant",
        "failing_required_features": sorted(failing_required),
        "deferred_to_runtime": {key: sorted(value, key=lambda item: item["feature"])
                                for key, value in sorted(deferred.items())},
        "features": features,
    }


def declared_profile(stage) -> Tuple[Optional[str], Optional[str]]:
    """Read the profile an asset says it conforms to (same source upstream uses)."""
    try:
        layer_data = stage.GetRootLayer().customLayerData or {}
        metadata = layer_data.get("SimReady_Metadata") or {}
        validation = metadata.get("validation") or {}
        return validation.get("profile"), validation.get("profile_version")
    except Exception:
        return None, None


def _joint_facts(stage) -> List[Dict[str, Any]]:
    from pxr import UsdPhysics

    facts: List[Dict[str, Any]] = []
    for prim in stage.Traverse():
        if not prim.IsA(UsdPhysics.Joint):
            continue
        joint = UsdPhysics.Joint(prim)
        item: Dict[str, Any] = {
            "path": prim.GetPath().pathString,
            "type": str(prim.GetTypeName()),
            "body0": [str(target) for target in joint.GetBody0Rel().GetTargets()],
            "body1": [str(target) for target in joint.GetBody1Rel().GetTargets()],
        }
        if prim.IsA(UsdPhysics.PrismaticJoint):
            typed = UsdPhysics.PrismaticJoint(prim)
        elif prim.IsA(UsdPhysics.RevoluteJoint):
            typed = UsdPhysics.RevoluteJoint(prim)
        else:
            typed = None
        if typed is not None:
            item["axis"] = str(typed.GetAxisAttr().Get())
            item["lower_limit"] = typed.GetLowerLimitAttr().Get()
            item["upper_limit"] = typed.GetUpperLimitAttr().Get()
        facts.append(item)
    return facts


def build_runtime_handoff(stage, report: Dict[str, Any], profile: Dict[str, Any],
                          asset: str, validator: Optional[str]) -> Dict[str, Any]:
    """Everything the downstream runtime test needs, so it does not re-derive it."""
    physics = report.get("physics", {}) or {}
    stage_info = report.get("stage", {}) or {}
    mass_by_path = {item.get("path"): item.get("mass") for item in physics.get("mass_api", []) or []}

    bodies = [
        {
            "path": item.get("path"),
            "kinematic": bool(item.get("kinematic_enabled")),
            "mass_kg": mass_by_path.get(item.get("path")),
        }
        for item in physics.get("rigid_bodies", []) or []
    ]
    colliders = [
        {"path": item.get("path"), "approximation": item.get("approximation"),
         "body_type": item.get("body_type")}
        for item in physics.get("colliders", []) or []
    ]
    scenes = physics.get("scenes", []) or []
    gravity = {}
    if scenes:
        gravity = {"direction": scenes[0].get("gravity_direction"),
                   "magnitude": scenes[0].get("gravity_magnitude")}

    reproduce = [validator or "simready-validate", "--profile", profile.get("profile_id") or "<profile>"]
    if profile.get("profile_version"):
        reproduce += ["--version", profile["profile_version"]]
    reproduce.append(asset)

    return {
        "profile": {"id": profile.get("profile_id"), "version": profile.get("profile_version")},
        "validated_with": {
            "simready_validate": profile.get("simready_validate_version"),
            "tier_versions": profile.get("tier_versions", {}),
        },
        "units": {
            "up_axis": stage_info.get("up_axis"),
            "meters_per_unit": stage_info.get("meters_per_unit"),
            "kilograms_per_unit": stage_info.get("kilograms_per_unit"),
        },
        "gravity": gravity,
        "bodies": bodies,
        "joints": _joint_facts(stage),
        "colliders": colliders,
        "reproduce": " ".join(reproduce),
        "note": "Runtime behaviour (contact, stability, articulation) is validated downstream.",
    }


def resolve_and_validate(report: Dict[str, Any], asset: str, *,
                         requested: str = "auto",
                         version: Optional[str] = None,
                         validator: Optional[str] = None,
                         require_confidence: float = 0.0,
                         advisor=None) -> Dict[str, Any]:
    """Decide the profile, prove it, and report what could not be evaluated."""
    decision = profile_auto.ProfileDecision()
    resolved_validator = validator

    if requested and requested != "auto":
        decision.profile_id, decision.profile_version = requested, version
        decision.source, decision.confidence = "explicit", 1.0
        if resolved_validator is None:
            return {"decision": decision, "result": None, "status": "not_evaluated"}
        result = run_validator(resolved_validator, asset, requested, version)
        return {"decision": decision, "result": result,
                "status": "validated" if result else "not_evaluated"}

    declared_id, declared_version = report.get("_declared_profile", (None, None))
    if declared_id:
        decision.profile_id, decision.profile_version = declared_id, declared_version or version
        decision.source, decision.confidence = "declared", 1.0
        decision.evidence.append("SimReady_Metadata.validation.profile")
        if resolved_validator is None:
            return {"decision": decision, "result": None, "status": "not_evaluated"}
        result = run_validator(resolved_validator, asset, declared_id, decision.profile_version)
        return {"decision": decision, "result": result,
                "status": "validated" if result else "not_evaluated"}

    candidates, evidence = profile_auto.candidates_from_report(report)
    decision.candidates, decision.evidence = candidates, evidence
    if not candidates:
        decision.source = "unresolved"
        return {"decision": decision, "result": None, "status": "unresolved"}

    if resolved_validator is None:
        decision.profile_id = candidates[0]
        decision.source = "heuristic"
        decision.confidence = 0.0
        decision.evidence.append("validator unavailable: candidate not confirmed")
        return {"decision": decision, "result": None, "status": "not_evaluated"}

    results: List[Tuple[str, str, Dict[str, Any]]] = []
    for candidate in candidates:
        result = run_validator(resolved_validator, asset, candidate, version)
        if result:
            results.append((candidate, result.get("profile_version") or version or "", result))
    profile_auto.finalize(decision, results)

    winner = next((body for name, _v, body in results
                   if name == decision.profile_id), None)
    if decision.confidence < require_confidence:
        decision.evidence.append(
            f"confidence {decision.confidence:.2f} below required {require_confidence:.2f}")
        return {"decision": decision, "result": winner, "status": "needs_review"}

    return {"decision": decision, "result": winner,
            "status": "validated" if winner else "unresolved"}


def exit_code_for(status: str, conformance: Optional[Dict[str, Any]]) -> int:
    if status in ("not_evaluated", "unresolved", "needs_review"):
        return EXIT_NOT_EVALUATED
    if conformance and conformance.get("status") == "non_conformant":
        return EXIT_NON_CONFORMANT
    return EXIT_CONFORMANT
