"""Resolve which SimReady profile an asset should be validated against.

Layers, in order of authority:
  1. explicit  - caller passed --profile
  2. declared  - SimReady_Metadata.validation.profile embedded in the asset
  3. heuristic - deterministic candidates derived from the inspection report
  4. validated - run each candidate and keep the ones that pass all required features
  5. advisor   - optional LLM tie-break, only between validated candidates

Nothing here trusts an advisor over evidence: an LLM may only *rank* candidates
that the deterministic layers produced, never introduce a profile.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

# most specific first; used only to break ties between candidates that all pass
SPECIFICITY = ["Robot-Gripper", "Robot-Body", "AIF-Entity", "Robotics-Prop",
               "Package", "Package-NoBOM", "Package-Candidate"]


@dataclass
class ProfileDecision:
    profile_id: Optional[str] = None
    profile_version: Optional[str] = None
    source: str = "unresolved"
    confidence: float = 0.0
    candidates: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    advisor: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def candidates_from_report(report: Dict[str, Any]) -> tuple[List[str], List[str]]:
    """Return (profile candidates, human-readable evidence)."""
    physics = report.get("physics", {}) or {}
    geometry = report.get("geometry", {}) or {}
    summary = report.get("summary", {}) or {}
    metadata = report.get("metadata", {}) or {}

    joints = physics.get("joints", []) or []
    articulations = physics.get("articulations", []) or []
    bodies = physics.get("rigid_bodies", []) or []
    schemes = summary.get("schema_counts", {}) or {}
    semantic = {lab for item in (report.get("semantic_candidates") or []) for lab in item.get("labels", [])}
    name_blob = " ".join([
        str(report.get("file", "")),
        str(metadata.get("asset_info", {}).get("identifier", "")),
        " ".join(str(item.get("display_name", "")) for item in metadata.get("display_names", []) or []),
    ]).lower()

    evidence: List[str] = []
    candidates: List[str] = []

    gripper_markers = ("gripper", "finger", "tcp")
    robot_markers = ("robot", "arm", "ur10", "ur5", "franka", "spot", "humanoid")
    aif_markers = ("cdu", "crah", "ups", "rack", "coolant", "manifold")

    if articulations:
        evidence.append(f"articulation roots: {len(articulations)}")
        candidates.append("Robot-Body")
    if any(marker in name_blob for marker in gripper_markers):
        evidence.append("gripper markers in names")
        candidates.append("Robot-Gripper")
    if any(marker in name_blob for marker in robot_markers):
        evidence.append("robot markers in names")
        candidates.append("Robot-Body")
    if any(marker in name_blob for marker in aif_markers):
        evidence.append("AI-factory equipment markers in names")
        candidates.append("AIF-Entity")

    if joints:
        evidence.append(f"joints: {len(joints)}")
    if bodies:
        evidence.append(f"rigid bodies: {len(bodies)}")
    if "furniture" in semantic or "chest_of_drawers" in semantic:
        evidence.append("semantic class: furniture")
    if bodies or joints or "PhysicsScene" in schemes:
        candidates.append("Robotics-Prop")

    if not bodies and not joints:
        evidence.append("no physics bodies: packaging profile may be the only fit")
        candidates.append("Package-Candidate")

    # stable, de-duplicated, most-specific-first ordering
    ordered = [name for name in SPECIFICITY if name in set(candidates)]
    return ordered, evidence


def run_validator(validator: str, asset: str, profile: str, version: str,
                  out_json: str) -> Dict[str, Any]:
    subprocess.run(
        [validator, "--profile", profile, "--version", version,
         "--output", out_json, asset],
        check=False, capture_output=True, text=True,
    )
    with open(out_json) as handle:
        data = json.load(handle)
    return data[next(iter(data))] if data else {}


def rank(results: List[tuple[str, str, Dict[str, Any]]]) -> Optional[tuple[str, str]]:
    """Among candidates that clear every required feature, pick the most specific."""
    clean = [(name, ver) for name, ver, body in results
             if body and not any(f.get("blocking_failure") for f in body.get("features_summary", {}).values())]
    for name in SPECIFICITY:
        for candidate, version in clean:
            if candidate == name:
                return candidate, version
    return clean[0] if clean else None


def finalize(decision: ProfileDecision, results: List[tuple[str, str, Dict[str, Any]]]) -> ProfileDecision:
    """Confirm a guess with evidence; never keep a candidate that failed validation."""
    best = rank(results)
    if best:
        decision.profile_id, decision.profile_version = best
        decision.source, decision.confidence = "validated", 0.95
        return decision
    decision.profile_id = None
    decision.profile_version = None
    decision.source = "rejected"
    decision.confidence = 0.0
    decision.evidence.append("every candidate failed at least one required feature")
    return decision


def resolve(report: Dict[str, Any], *, explicit: Optional[str] = None,
            declared: Optional[str] = None, advisor=None) -> ProfileDecision:
    decision = ProfileDecision()
    if explicit:
        decision.profile_id, decision.source, decision.confidence = explicit, "explicit", 1.0
        return decision
    if declared:
        decision.profile_id, decision.source, decision.confidence = declared, "declared", 1.0
        decision.evidence.append("SimReady_Metadata.validation.profile")
        return decision

    candidates, evidence = candidates_from_report(report)
    decision.candidates, decision.evidence = candidates, evidence
    if len(candidates) == 1:
        decision.profile_id, decision.source, decision.confidence = candidates[0], "heuristic", 0.7
        return decision
    if not candidates:
        return decision

    if advisor is not None:
        advice = advisor({"candidates": candidates, "evidence": evidence}) or {}
        chosen = advice.get("profile")
        if chosen in candidates:
            decision.profile_id = chosen
            decision.source = "llm"
            decision.confidence = float(advice.get("confidence", 0.5))
            decision.advisor = advice
            return decision

    decision.source = "ambiguous"
    decision.confidence = 0.3
    return decision
