#!/usr/bin/env python3
"""Merge Content Agents Physics Agent predictions into SimReady recommendations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in ("", None):
            return None
        return float(value)
    except Exception:
        return None


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSONL row: {error}") from error
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _compact_component_prediction(row: Dict[str, Any]) -> Dict[str, Any]:
    classification = row.get("classification", {}) or {}
    physical_properties = classification.get("physical_properties", {}) or {}
    return {
        "prim_path": row.get("id"),
        "asset_type": classification.get("asset_type"),
        "component_type": classification.get("component_type"),
        "component_name": classification.get("component_name"),
        "material": classification.get("classification") or classification.get("material"),
        "confidence": classification.get("confidence"),
        "physical_properties": {
            "density": _safe_float(physical_properties.get("density")),
            "estimated_mass_kg": _safe_float(physical_properties.get("estimated_mass_kg")),
            "static_friction": _safe_float(physical_properties.get("static_friction")),
            "dynamic_friction": _safe_float(physical_properties.get("dynamic_friction")),
            "restitution": _safe_float(physical_properties.get("restitution")),
        },
        "reasoning": classification.get("reasoning"),
    }


def _first_present(items: Iterable[Any]) -> Any:
    for item in items:
        if item not in (None, ""):
            return item
    return None


def _dominant_value(rows: List[Dict[str, Any]], key: str) -> Optional[Any]:
    counts: Dict[Any, int] = {}
    for row in rows:
        value = row.get(key)
        if value in (None, ""):
            continue
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))[0][0]


def _physics_material_suggestion(components: List[Dict[str, Any]]) -> Dict[str, Any]:
    static_values = []
    dynamic_values = []
    restitution_values = []
    density_values = []
    mass_values = []
    for component in components:
        props = component.get("physical_properties", {}) or {}
        for target, value in (
            (static_values, props.get("static_friction")),
            (dynamic_values, props.get("dynamic_friction")),
            (restitution_values, props.get("restitution")),
            (density_values, props.get("density")),
            (mass_values, props.get("estimated_mass_kg")),
        ):
            number = _safe_float(value)
            if number is not None:
                target.append(number)

    def average(values: List[float]) -> Optional[float]:
        return round(sum(values) / len(values), 6) if values else None

    return {
        "material": _dominant_value(components, "material"),
        "density": average(density_values),
        "estimated_mass_kg": average(mass_values),
        "static_friction": average(static_values),
        "dynamic_friction": average(dynamic_values),
        "restitution": average(restitution_values),
    }


def _build_review_flags(recommendation: Dict[str, Any], supplement: Dict[str, Any]) -> List[str]:
    flags: List[str] = []
    rec = recommendation.get("recommendation", {}) or {}
    rule_class = rec.get("furniture_class")
    agent_asset_type = supplement.get("asset_type")
    material = ((supplement.get("physics_material_suggestion") or {}).get("material") or "").lower()
    estimated_mass = _safe_float((supplement.get("physics_material_suggestion") or {}).get("estimated_mass_kg"))
    mass_assessment = supplement.get("mass_assessment", {}) or {}

    if rule_class and agent_asset_type and str(rule_class).lower() not in {str(agent_asset_type).lower(), "decor"}:
        flags.append("content_agent_asset_type_differs_from_rule_class")
    if mass_assessment.get("status") == "invalid_unscaled_geometry":
        flags.append("content_agent_mass_from_unscaled_geometry")
    if bool(rec.get("is_decor")) and estimated_mass is not None and estimated_mass > 20.0:
        flags.append("content_agent_mass_outlier_for_decor")
    if material in {"glass", "ceramic"} and rec.get("recommended_collider", {}).get("approximation") == "convexHull":
        flags.append("fragile_material_with_coarse_static_collider")
    return flags


def build_content_physics_supplement(predictions_jsonl: Path, source_usd: Optional[str] = None) -> Dict[str, Any]:
    rows = _read_jsonl(predictions_jsonl)
    components = [_compact_component_prediction(row) for row in rows]
    asset_type = _first_present(component.get("asset_type") for component in components)
    confidence = _first_present(component.get("confidence") for component in components)
    return {
        "source": "nvidia_content_agents_physics_agent",
        "mode": "supplemental_evidence",
        "source_usd": source_usd,
        "predictions_jsonl": str(predictions_jsonl),
        "prediction_count": len(components),
        "asset_type": asset_type,
        "confidence": confidence,
        "components": components,
        "physics_material_suggestion": _physics_material_suggestion(components),
        "application_policy": {
            "rules_remain_primary": True,
            "auto_override_static_recommendation": False,
            "intended_use": "review evidence for furniture/decor physics material, mass, friction, and collider choices",
        },
    }


def _scale_context(recommendation: Dict[str, Any]) -> Dict[str, Any]:
    rec = recommendation.get("recommendation", {}) or {}
    authoring = rec.get("authoring", {}) or {}
    size_recommendation = rec.get("size_recommendation", {}) or {}
    scale = _safe_float(authoring.get("suggested_uniform_scale"))
    apply_scale = bool(authoring.get("apply_reference_scale"))
    return {
        "apply_reference_scale": apply_scale,
        "suggested_uniform_scale": scale,
        "size_recommendation_status": size_recommendation.get("status"),
        "size_warning": size_recommendation.get("size_warning"),
        "reference_target_bbox": size_recommendation.get("reference_target_bbox"),
    }


def _target_size_cm(recommendation: Dict[str, Any]) -> Optional[List[float]]:
    rec = recommendation.get("recommendation", {}) or {}
    size_recommendation = rec.get("size_recommendation", {}) or {}
    target = size_recommendation.get("reference_target_bbox")
    if isinstance(target, list) and len(target) == 3 and all(_safe_float(item) is not None for item in target):
        return [float(item) for item in target]
    size = rec.get("size", {}) or {}
    bbox_size = size.get("bbox_size")
    if isinstance(bbox_size, list) and len(bbox_size) == 3 and all(_safe_float(item) is not None for item in bbox_size):
        return [float(item) for item in bbox_size]
    return None


def _mass_range_for_rules(recommendation: Dict[str, Any]) -> Dict[str, Any]:
    rec = recommendation.get("recommendation", {}) or {}
    furniture_class = str(rec.get("furniture_class") or "unknown")
    target_size = _target_size_cm(recommendation)
    volume_liters = None
    if target_size:
        volume_liters = round((target_size[0] * target_size[1] * target_size[2]) / 1000.0, 6)

    if furniture_class == "decor":
        default_range = [0.02, 5.0]
        if volume_liters is not None and volume_liters <= 2.0:
            default_range = [0.05, 1.5]
        return {
            "min_kg": default_range[0],
            "max_kg": default_range[1],
            "basis": [
                "rule_class=decor",
                "small decor/container assets should use a bounded mass prior before accepting VLM estimates",
            ],
        }
    if furniture_class in {"chair", "stool", "bench", "ottoman"}:
        return {"min_kg": 2.0, "max_kg": 40.0, "basis": [f"rule_class={furniture_class}"]}
    if furniture_class in {"table", "desk"}:
        return {"min_kg": 3.0, "max_kg": 80.0, "basis": [f"rule_class={furniture_class}"]}
    if furniture_class in {"sofa"}:
        return {"min_kg": 10.0, "max_kg": 120.0, "basis": [f"rule_class={furniture_class}"]}
    if furniture_class in {"cabinet", "shelf", "storage"}:
        return {"min_kg": 5.0, "max_kg": 150.0, "basis": [f"rule_class={furniture_class}"]}
    return {"min_kg": None, "max_kg": None, "basis": [f"rule_class={furniture_class}", "no bounded mass prior"]}


def _build_rule_constraints(recommendation: Dict[str, Any]) -> Dict[str, Any]:
    rec = recommendation.get("recommendation", {}) or {}
    recommended = rec.get("recommended_collider", {}) or {}
    return {
        "source": "usd_simready_rule_engine",
        "rule_class": rec.get("furniture_class"),
        "is_furniture": rec.get("is_furniture"),
        "is_decor": rec.get("is_decor"),
        "target_size_cm": _target_size_cm(recommendation),
        "scale_context": _scale_context(recommendation),
        "mass_range_kg": _mass_range_for_rules(recommendation),
        "collider": {
            "approximation": recommended.get("approximation"),
            "scope": recommended.get("scope"),
        },
        "policy": {
            "rules_define_bounds_first": True,
            "vlm_may_fill_material_and_friction": True,
            "vlm_may_override_rule_class": False,
            "vlm_mass_must_fit_rule_bounds": True,
        },
    }


def _add_mass_assessment(recommendation: Dict[str, Any], supplement: Dict[str, Any]) -> None:
    suggestion = supplement.get("physics_material_suggestion", {}) or {}
    raw_mass = _safe_float(suggestion.get("estimated_mass_kg"))
    rule_constraints = supplement.get("rule_constraints", {}) or _build_rule_constraints(recommendation)
    scale_context = rule_constraints.get("scale_context", {}) or _scale_context(recommendation)
    mass_range = rule_constraints.get("mass_range_kg", {}) or {}
    scale = _safe_float(scale_context.get("suggested_uniform_scale"))
    min_mass = _safe_float(mass_range.get("min_kg"))
    max_mass = _safe_float(mass_range.get("max_kg"))
    mass_for_authoring = raw_mass
    status = "usable"
    basis: List[str] = []

    if raw_mass is None:
        status = "missing"
        mass_for_authoring = None
        basis.append("Physics Agent did not provide an estimated mass")
    elif scale_context.get("apply_reference_scale") and scale is not None and abs(scale - 1.0) > 0.05:
        status = "invalid_unscaled_geometry"
        mass_for_authoring = None
        basis.extend(
            [
                "Physics Agent mass was estimated from source geometry before rule-based scale normalization",
                f"rule_suggested_uniform_scale={scale}",
                "mass should be re-estimated after the asset is authored at final scale",
            ]
        )
    elif min_mass is not None and raw_mass < min_mass:
        status = "outside_rule_bounds"
        mass_for_authoring = None
        basis.append(f"raw mass {raw_mass} kg is below rule lower bound {min_mass} kg")
    elif max_mass is not None and raw_mass > max_mass:
        status = "outside_rule_bounds"
        mass_for_authoring = None
        basis.append(f"raw mass {raw_mass} kg is above rule upper bound {max_mass} kg")

    if raw_mass is not None and scale is not None:
        scale_context["cubic_scaled_mass_kg"] = round(raw_mass * (scale ** 3), 9)
        if status == "invalid_unscaled_geometry":
            basis.append("cubic-scaled mass is recorded only as a diagnostic, not an accepted value")

    suggestion["raw_estimated_mass_kg"] = raw_mass
    suggestion["mass_for_authoring_kg"] = mass_for_authoring
    supplement["physics_material_suggestion"] = suggestion
    supplement["mass_assessment"] = {
        "status": status,
        "rule_mass_range_kg": mass_range,
        "scale_context": scale_context,
        "basis": basis,
    }


def merge_recommendation_with_content_physics(
    recommendation: Dict[str, Any],
    predictions_jsonl: Path,
    source_usd: Optional[str] = None,
) -> Dict[str, Any]:
    merged = dict(recommendation)
    supplements = dict(merged.get("supplements", {}) or {})
    content_physics = build_content_physics_supplement(predictions_jsonl, source_usd=source_usd)
    content_physics["rule_constraints"] = _build_rule_constraints(merged)
    _add_mass_assessment(merged, content_physics)
    content_physics["review_flags"] = _build_review_flags(merged, content_physics)
    supplements["content_agent_physics"] = content_physics
    merged["supplements"] = supplements

    review_flags = list(merged.get("review_flags", []) or [])
    for flag in content_physics["review_flags"]:
        if flag not in review_flags:
            review_flags.append(flag)
    merged["review_flags"] = review_flags
    return merged


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append Content Agents Physics Agent predictions to a SimReady recommendation JSON.",
    )
    parser.add_argument("recommendation_json", type=Path)
    parser.add_argument("--physics-predictions", type=Path, required=True, help="Physics Agent predictions.jsonl")
    parser.add_argument("--source-usd", help="Optional source USD path recorded in the supplement")
    parser.add_argument("--output", type=Path, help="Output recommendation JSON. Defaults to *.with_physics.json")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    with args.recommendation_json.open("r", encoding="utf-8") as handle:
        recommendation = json.load(handle)
    merged = merge_recommendation_with_content_physics(
        recommendation,
        args.physics_predictions,
        source_usd=args.source_usd,
    )
    output = args.output
    if output is None:
        output = args.recommendation_json.with_name(args.recommendation_json.stem + ".with_physics.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        json.dump(merged, handle, ensure_ascii=False, indent=2)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
