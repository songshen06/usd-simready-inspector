"""Merge declared intent with measured geometry into a handoff document.

Input is the inspection JSON (facts) plus a small intent file; output is the
contract that authoring consumes. This module deliberately has no `pxr`
dependency: the handoff is a data-only seam between modelling and SimReady
authoring, so it can be validated and tested without a USD stage.

Every derived value comes from geometry:
    pivot  = the face of the part bbox that the joint pulls away from
    travel = bbox extent along the joint axis minus a reserve
Nothing is guessed: moving parts must be declared, and a part that is not in the
inspection facts is an error rather than a silent skip.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

SCHEMA = "simready-handoff/1"
INTENT_SCHEMA = "simready-intent/1"
AXES = ("X", "Y", "Z")
AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}


class HandoffError(Exception):
    def __init__(self, issues: List[Dict[str, str]]):
        super().__init__("; ".join(issue["message"] for issue in issues))
        self.issues = issues


def _issue(code: str, message: str, owner: str = "intent") -> Dict[str, str]:
    return {"code": code, "message": message, "owner": owner}


def load_intent(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def parts_index(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    facts = ((report.get("visual_gate") or {}).get("facts") or {})
    parts = facts.get("parts") or []
    return {part["prim"]: part for part in parts if part.get("prim")}


def _axis_bounds(bbox: List[float], axis: str) -> Tuple[float, float]:
    index = AXIS_INDEX[axis]
    return float(bbox[index]), float(bbox[index + 3])


def pivot_for(bbox: List[float], axis: str, pull: str) -> List[float]:
    index = AXIS_INDEX[axis]
    low, high = _axis_bounds(bbox, axis)
    centre = [(float(bbox[i]) + float(bbox[i + 3])) / 2 for i in range(3)]
    centre[index] = low if pull == "negative" else high
    return [round(value, 6) for value in centre]


def depth_for(bbox: List[float], axis: str) -> float:
    low, high = _axis_bounds(bbox, axis)
    return round(high - low, 6)


def _resolve_travel(moving: Dict[str, Any], depth: float) -> float:
    travel = moving.get("travel") or {"policy": "bbox_depth_minus_reserve", "reserve_m": 0.05}
    if "value_m" in travel:
        return float(travel["value_m"])
    policy = travel.get("policy", "bbox_depth_minus_reserve")
    if policy != "bbox_depth_minus_reserve":
        raise HandoffError([_issue("HO.05", f"unsupported travel policy: {policy}")])
    reserve = float(travel.get("reserve_m", 0.05))
    return round(depth - reserve, 6)


def _digest(payload: Dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build(report: Dict[str, Any], intent: Dict[str, Any]) -> Dict[str, Any]:
    """Return the handoff document, or raise HandoffError with every problem found."""
    issues: List[Dict[str, str]] = []
    parts = parts_index(report)
    if not parts:
        raise HandoffError([_issue(
            "HO.01",
            "inspection report has no visual_gate.facts.parts; run `inspect --gate visual` first",
            owner="tooling")])

    moving_declared = intent.get("moving") or []
    if not isinstance(moving_declared, list) or not moving_declared:
        raise HandoffError([_issue("HO.02", "intent declares no moving parts")])

    root_path = intent.get("root")
    root_part = parts.get(root_path)
    if root_path and not root_part:
        issues.append(_issue("HO.03", f"root prim not found in inspection facts: {root_path}"))

    seen: Dict[str, Any] = {}
    resolved: List[Dict[str, Any]] = []
    for entry in moving_declared:
        declared = {"prim": entry} if isinstance(entry, str) else dict(entry or {})
        prim = declared.get("prim")
        if not prim:
            issues.append(_issue("HO.04", "moving entry has no 'prim'"))
            continue
        if prim in seen:
            issues.append(_issue("HO.07", f"duplicate moving prim: {prim}"))
            continue
        seen[prim] = declared
        part = parts.get(prim)
        if not part:
            issues.append(_issue("HO.04", f"moving prim not found in inspection facts: {prim}"))
            continue
        bbox = part.get("bbox")
        if not bbox:
            issues.append(_issue("HO.04", f"moving prim has no computed bbox: {prim}"))
            continue

        joint = declared.get("joint") or {"type": "prismatic", "axis": "Y", "pull": "negative"}
        axis = str(joint.get("axis", "Y")).upper()
        pull = str(joint.get("pull", "negative")).lower()
        if axis not in AXES:
            issues.append(_issue("HO.05", f"{prim}: unsupported joint axis {axis!r}"))
            continue
        if pull not in ("positive", "negative"):
            issues.append(_issue("HO.05", f"{prim}: pull must be positive or negative, got {pull!r}"))
            continue
        if str(joint.get("type", "prismatic")).lower() != "prismatic":
            issues.append(_issue("HO.05", f"{prim}: only prismatic joints are supported by authoring"))
            continue

        if root_part and root_part.get("bbox"):
            root_bbox = root_part["bbox"]
            inside = all(root_bbox[i] - 1e-4 <= bbox[i] and bbox[i + 3] <= root_bbox[i + 3] + 1e-4
                         for i in range(3))
            if not inside:
                issues.append(_issue("HO.06", f"{prim} is not inside the root bounds {root_path}",
                                     owner="modeling"))

        depth = depth_for(bbox, axis)
        try:
            travel = _resolve_travel(declared, depth)
        except HandoffError as error:
            issues.extend(error.issues)
            continue
        if travel <= 0:
            issues.append(_issue("HO.08", f"{prim}: travel resolves to {travel} m"))
            continue
        if travel >= depth:
            issues.append(_issue(
                "HO.08", f"{prim}: travel {travel} m would pull the part clear of its {depth} m depth"))

        mass = declared.get("mass") or {"policy": "volume_x_density", "density_kg_m3": 700.0}
        density = mass.get("density_kg_m3")
        if mass.get("policy", "volume_x_density") == "volume_x_density" and (
                density is None or float(density) <= 0):
            issues.append(_issue("HO.09", f"{prim}: mass policy needs a positive density_kg_m3"))

        resolved.append({
            "prim": prim,
            "joint": {"type": "prismatic", "axis": axis, "pull": pull},
            "travel_m": travel,
            "pivot": pivot_for(bbox, axis, pull),
            "depth_m": depth,
            "bbox": bbox,
            "materials": part.get("materials", []),
            "mass": mass,
        })

    if issues:
        raise HandoffError(issues)

    assets = intent.get("asset") or (report.get("metadata") or {}).get("asset_info") or {}
    handoff = {
        "schema": SCHEMA,
        "asset": assets,
        "root": root_path,
        "moving": sorted(resolved, key=lambda item: item["prim"]),
        "source": {"inspection": report.get("file"), "gate": (report.get("visual_gate") or {}).get("status")},
    }
    handoff["facts_digest"] = _digest({
        "root": root_path,
        "moving": [{key: item[key] for key in ("prim", "pivot", "depth_m", "bbox", "materials")}
                   for item in handoff["moving"]],
    })
    return handoff


def summarize(handoff: Dict[str, Any]) -> str:
    parts = [f"{item['prim']} travel={item['travel_m']}m axis={item['joint']['axis']}"
             for item in handoff.get("moving", [])]
    return f"{len(parts)} moving part(s): " + "; ".join(parts)
