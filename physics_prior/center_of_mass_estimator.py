"""Center-of-mass estimation from rule and Content Agents physics priors."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .component_mass_prior import component_weight


SUPPORTED_CENTER_OF_MASS_MODES = {"none", "bbox_center", "lower_center", "semantic_weighted"}


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _vec3(values: Any) -> Optional[List[float]]:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        return None
    parsed = [_safe_float(value) for value in values]
    if any(value is None for value in parsed):
        return None
    return [float(value) for value in parsed]


def _stage_units_per_cm(recommendation: Dict[str, Any]) -> float:
    size = _recommendation_size(recommendation)
    bbox = size.get("bbox", {}) if isinstance(size, dict) else {}
    meters_per_unit = _safe_float(size.get("stage_meters_per_unit") or bbox.get("stage_meters_per_unit"))
    if meters_per_unit is None or meters_per_unit <= 0.0:
        return 1.0
    return 1.0 / (meters_per_unit * 100.0)


def _recommendation_size(recommendation: Dict[str, Any]) -> Dict[str, Any]:
    for container in (recommendation.get("recommendation", {}) or {}, recommendation.get("asset", {}) or {}):
        size = container.get("size", {}) or {}
        if isinstance(size, dict) and size:
            return size
    return {}


def _bbox_from_recommendation(recommendation: Dict[str, Any]) -> Optional[Dict[str, List[float]]]:
    size = _recommendation_size(recommendation)
    bbox = size.get("bbox", {}) if isinstance(size, dict) else {}
    min_values = _vec3(bbox.get("min"))
    max_values = _vec3(bbox.get("max"))
    center_values = _vec3(bbox.get("center"))
    if min_values and max_values:
        units_per_cm = _stage_units_per_cm(recommendation)
        min_stage = [value * units_per_cm for value in min_values]
        max_stage = [value * units_per_cm for value in max_values]
        center_stage = (
            [value * units_per_cm for value in center_values]
            if center_values
            else [(min_stage[index] + max_stage[index]) * 0.5 for index in range(3)]
        )
        return {"min": min_stage, "max": max_stage, "center": center_stage}
    return None


def _up_axis_index(recommendation: Dict[str, Any]) -> int:
    size = _recommendation_size(recommendation)
    bbox = size.get("bbox", {}) if isinstance(size, dict) else {}
    up_axis = str(size.get("stage_up_axis") or bbox.get("stage_up_axis") or "Z").upper()
    return {"X": 0, "Y": 1, "Z": 2}.get(up_axis, 2)


def _bbox_center_estimate(recommendation: Dict[str, Any], method: str, basis: List[str]) -> Dict[str, Any]:
    bbox = _bbox_from_recommendation(recommendation)
    if not bbox:
        return {
            "method": method,
            "local_position": None,
            "units": "stage_units",
            "confidence": 0.0,
            "basis": basis + ["recommendation bbox was unavailable"],
        }
    return {
        "method": method,
        "local_position": [round(value, 9) for value in bbox["center"]],
        "units": "stage_units",
        "confidence": 0.45 if method == "bbox_center" else 0.5,
        "basis": basis + ["used recommendation default prim bbox center"],
    }


def _lower_center_estimate(recommendation: Dict[str, Any], method: str, basis: List[str]) -> Dict[str, Any]:
    bbox = _bbox_from_recommendation(recommendation)
    if not bbox:
        return _bbox_center_estimate(recommendation, method, basis)
    up_index = _up_axis_index(recommendation)
    center = list(bbox["center"])
    center[up_index] = bbox["min"][up_index] + (bbox["max"][up_index] - bbox["min"][up_index]) * 0.35
    return {
        "method": method,
        "local_position": [round(value, 9) for value in center],
        "units": "stage_units",
        "confidence": 0.55,
        "basis": basis + ["biased bbox center toward the support side along the stage up axis"],
    }


def _component_bounds_from_usd(
    source_usd: Optional[str],
    components: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:
    if not source_usd:
        return {}, "source USD was not provided"
    try:
        from pxr import Gf, Usd, UsdGeom
    except Exception as error:  # pragma: no cover - depends on USD runtime.
        return {}, f"USD Python bindings unavailable: {error}"

    path = Path(source_usd).expanduser()
    stage = Usd.Stage.Open(str(path))
    if stage is None:
        return {}, f"could not open source USD: {path}"
    default_prim = stage.GetDefaultPrim()
    if not default_prim or not default_prim.IsValid():
        return {}, "source USD has no valid default prim"

    bbox_cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_], useExtentsHint=True)
    xform_cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    default_world = xform_cache.GetLocalToWorldTransform(default_prim)
    default_world_inverse = default_world.GetInverse()
    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)

    bounds: Dict[str, Dict[str, Any]] = {}
    for component in components:
        prim_path = component.get("prim_path")
        if not prim_path:
            continue
        prim = stage.GetPrimAtPath(str(prim_path))
        if not prim or not prim.IsValid():
            continue
        bbox_range = bbox_cache.ComputeWorldBound(prim).ComputeAlignedRange()
        if bbox_range.IsEmpty():
            continue
        midpoint = bbox_range.GetMidpoint()
        local_center = default_world_inverse.Transform(Gf.Vec3d(float(midpoint[0]), float(midpoint[1]), float(midpoint[2])))
        size = bbox_range.GetSize()
        volume_m3 = (
            abs(float(size[0]) * meters_per_unit)
            * abs(float(size[1]) * meters_per_unit)
            * abs(float(size[2]) * meters_per_unit)
        )
        bounds[str(prim_path)] = {
            "local_center": [float(local_center[0]), float(local_center[1]), float(local_center[2])],
            "bbox_volume_m3": volume_m3,
        }
    return bounds, None


def _semantic_weighted_estimate(
    recommendation: Dict[str, Any],
    components: List[Dict[str, Any]],
    source_usd: Optional[str],
    basis: List[str],
) -> Dict[str, Any]:
    component_bounds, bounds_warning = _component_bounds_from_usd(source_usd, components)
    weighted_components = []
    total_weight = 0.0
    weighted_position = [0.0, 0.0, 0.0]

    for component in components:
        prim_path = component.get("prim_path")
        bounds = component_bounds.get(str(prim_path))
        if not bounds:
            continue
        weight = component_weight(component, bounds.get("bbox_volume_m3"))
        center = bounds["local_center"]
        total_weight += weight
        for index in range(3):
            weighted_position[index] += center[index] * weight
        weighted_components.append(
            {
                "prim_path": prim_path,
                "component_name": component.get("component_name"),
                "component_type": component.get("component_type"),
                "material": component.get("material"),
                "local_center": [round(value, 9) for value in center],
                "relative_weight": round(weight, 9),
            }
        )

    if not weighted_components or total_weight <= 0.0:
        fallback = _lower_center_estimate(
            recommendation,
            "semantic_weighted_components_fallback_lower_center",
            basis + ["component bbox/weight data was insufficient"],
        )
        if bounds_warning:
            fallback["basis"].append(bounds_warning)
        fallback["component_weights"] = []
        return fallback

    center = [value / total_weight for value in weighted_position]
    confidence = 0.65
    if len(weighted_components) >= 3:
        confidence = 0.75
    elif len(weighted_components) == 1:
        confidence = 0.6

    result_basis = basis + [
        "weighted component centers by Physics Agent mass/density/material/role priors",
        f"component_count={len(weighted_components)}",
    ]
    if bounds_warning:
        result_basis.append(bounds_warning)

    return {
        "method": "semantic_weighted_components",
        "local_position": [round(value, 9) for value in center],
        "units": "stage_units",
        "confidence": confidence,
        "basis": result_basis,
        "component_weights": weighted_components,
    }


def estimate_center_of_mass(
    recommendation: Dict[str, Any],
    components: Optional[List[Dict[str, Any]]] = None,
    *,
    mode: str = "semantic_weighted",
    source_usd: Optional[str] = None,
) -> Dict[str, Any]:
    """Estimate centerOfMass in default-prim local stage units."""

    normalized_mode = str(mode or "none").strip().lower()
    if normalized_mode not in SUPPORTED_CENTER_OF_MASS_MODES:
        raise ValueError(f"unsupported center-of-mass mode: {mode}")
    basis = [f"mode={normalized_mode}"]

    if normalized_mode == "none":
        return {
            "method": "none",
            "local_position": None,
            "units": "stage_units",
            "confidence": 0.0,
            "basis": basis + ["center-of-mass estimation disabled"],
        }
    if normalized_mode == "bbox_center":
        return _bbox_center_estimate(recommendation, "bbox_center", basis)
    if normalized_mode == "lower_center":
        return _lower_center_estimate(recommendation, "lower_center", basis)
    return _semantic_weighted_estimate(recommendation, list(components or []), source_usd, basis)
