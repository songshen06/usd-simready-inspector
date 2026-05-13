"""Component mass weighting priors for semantic physics estimates."""

from __future__ import annotations

from typing import Any, Dict, Optional


MATERIAL_DENSITY_PRIORS_KG_M3 = {
    "foam": 80.0,
    "fabric": 250.0,
    "rubber": 1100.0,
    "plastic": 1050.0,
    "wood": 650.0,
    "glass": 2500.0,
    "ceramic": 2300.0,
    "stone": 2600.0,
    "concrete": 2400.0,
    "metal": 7800.0,
    "steel": 7850.0,
    "aluminum": 2700.0,
}

COMPONENT_ROLE_MULTIPLIERS = {
    "base": 1.8,
    "support": 1.5,
    "leg": 1.35,
    "frame": 1.35,
    "structural": 1.25,
    "body": 1.0,
    "seat": 0.9,
    "backrest": 0.65,
    "cushion": 0.45,
    "decorative": 0.35,
    "trim": 0.25,
}


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        result = float(value)
    except Exception:
        return None
    return result if result > 0.0 else None


def _lower_text(*values: Any) -> str:
    return " ".join(str(value).lower() for value in values if value not in (None, ""))


def material_density_prior(component: Dict[str, Any]) -> Optional[float]:
    """Return a coarse density prior from the component material text."""

    text = _lower_text(component.get("material"), component.get("component_name"), component.get("reasoning"))
    for token, density in MATERIAL_DENSITY_PRIORS_KG_M3.items():
        if token in text:
            return density
    props = component.get("physical_properties", {}) or {}
    return _safe_float(props.get("density"))


def role_multiplier(component: Dict[str, Any]) -> float:
    """Return a mass multiplier from component role/name semantics."""

    text = _lower_text(component.get("component_type"), component.get("component_name"), component.get("reasoning"))
    for token, multiplier in COMPONENT_ROLE_MULTIPLIERS.items():
        if token in text:
            return multiplier
    return 1.0


def component_weight(component: Dict[str, Any], bbox_volume_m3: Optional[float] = None) -> float:
    """Estimate a relative mass weight for one component.

    Physics Agent mass, when present, is used as a relative signal even when the
    absolute mass may later be rejected by rule bounds.
    """

    props = component.get("physical_properties", {}) or {}
    estimated_mass = _safe_float(props.get("estimated_mass_kg"))
    if estimated_mass is not None:
        return max(estimated_mass * role_multiplier(component), 1e-6)

    density = material_density_prior(component) or 700.0
    volume = bbox_volume_m3 if bbox_volume_m3 is not None and bbox_volume_m3 > 0.0 else 1.0
    return max(density * volume * role_multiplier(component), 1e-6)
