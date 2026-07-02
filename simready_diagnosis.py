#!/usr/bin/env python3
"""Diagnose SimReady expectation mismatches across authoring and runtime reports."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _is_number_list(value: Any, length: int = 3) -> bool:
    return isinstance(value, list) and len(value) == length and all(_safe_float(item) is not None for item in value)


def _float_list(value: Any, length: int = 3) -> Optional[List[float]]:
    if not _is_number_list(value, length=length):
        return None
    return [float(item) for item in value]


def _stage_units_to_cm(values: Optional[List[float]], meters_per_unit: Any) -> Optional[List[float]]:
    if values is None:
        return None
    mpu = _safe_float(meters_per_unit)
    if mpu is None:
        return values
    return [value * mpu * 100.0 for value in values]


def _stage_unit_to_cm(value: Optional[float], meters_per_unit: Any) -> Optional[float]:
    if value is None:
        return None
    mpu = _safe_float(meters_per_unit)
    if mpu is None:
        return value
    return value * mpu * 100.0


def _status_from_checks(checks: List[Dict[str, Any]]) -> str:
    if any(check["status"] == "failed" for check in checks):
        return "failed"
    if any(check["status"] == "warning" for check in checks):
        return "warning"
    return "passed"


def _check(
    checks: List[Dict[str, Any]],
    *,
    name: str,
    expected: Any,
    observed: Any,
    status: str,
    reason: str = "",
) -> None:
    checks.append(
        {
            "name": name,
            "expected": expected,
            "observed": observed,
            "status": status,
            "reason": reason,
        }
    )


def _tolerance(expectations: Dict[str, Any], expected_value: float) -> float:
    tolerance = expectations.get("tolerance", {}) or {}
    relative = _safe_float(tolerance.get("bbox_relative"))
    absolute = _safe_float(tolerance.get("bbox_absolute_cm"))
    if relative is None:
        relative = 0.05
    if absolute is None:
        absolute = 0.05
    return max(abs(expected_value) * relative, absolute)


def _compare_bbox_size(
    checks: List[Dict[str, Any]],
    *,
    name: str,
    expectations: Dict[str, Any],
    expected: Optional[List[float]],
    observed: Optional[List[float]],
    diagnosis_code: str,
    diagnosis: List[str],
    suggested_patches: List[Dict[str, Any]],
    patch: Dict[str, Any],
) -> None:
    if not expected:
        _check(
            checks,
            name=name,
            expected=None,
            observed=observed,
            status="warning",
            reason="missing expected bbox size",
        )
        return
    if not observed:
        diagnosis.append(diagnosis_code)
        suggested_patches.append(patch)
        _check(
            checks,
            name=name,
            expected=expected,
            observed=observed,
            status="failed",
            reason="missing observed bbox size",
        )
        return

    expected_sorted = sorted(expected)
    observed_sorted = sorted(observed)
    failures = []
    for index, (expected_value, observed_value) in enumerate(zip(expected_sorted, observed_sorted)):
        tolerance = _tolerance(expectations, expected_value)
        if abs(observed_value - expected_value) > tolerance:
            failures.append(
                {
                    "axis": index,
                    "expected_cm": expected_value,
                    "observed_cm": observed_value,
                    "tolerance_cm": tolerance,
                }
            )

    if failures:
        diagnosis.append(diagnosis_code)
        suggested_patches.append(patch)
        _check(
            checks,
            name=name,
            expected=expected,
            observed=observed,
            status="failed",
            reason=json.dumps(failures, ensure_ascii=False),
        )
        return

    _check(checks, name=name, expected=expected, observed=observed, status="passed")


def _expectations_from(recommendation: Dict[str, Any], report: Dict[str, Any]) -> Dict[str, Any]:
    return (report.get("simready_expectations") or recommendation.get("simready_expectations") or {}) or {}


def _report_bbox_size_cm(report: Dict[str, Any]) -> Optional[List[float]]:
    size = _float_list((((report.get("geometry", {}) or {}).get("bbox", {}) or {}).get("world", {}) or {}).get("size"))
    meters_per_unit = (report.get("stage", {}) or {}).get("meters_per_unit")
    return _stage_units_to_cm(size, meters_per_unit)


def _runtime_bbox_size_cm(runtime_report: Dict[str, Any]) -> Optional[List[float]]:
    bbox_min = _float_list(runtime_report.get("asset_bbox_min"))
    bbox_max = _float_list(runtime_report.get("asset_bbox_max"))
    scene = runtime_report.get("scene", {}) or {}
    meters_per_unit = (
        _safe_float(scene.get("meters_per_unit"))
        or _safe_float(runtime_report.get("meters_per_unit"))
        or _safe_float(runtime_report.get("stage_meters_per_unit"))
    )
    if not bbox_min or not bbox_max:
        scene_size = _float_list(scene.get("asset_bbox_size"))
        converted = _stage_units_to_cm(scene_size, meters_per_unit)
        if converted:
            return converted
        return None
    size = [bbox_max[index] - bbox_min[index] for index in range(3)]
    return _stage_units_to_cm(size, meters_per_unit)


def _runtime_drop_actor_size_cm(runtime_report: Dict[str, Any]) -> Optional[float]:
    scene = runtime_report.get("scene", {}) or {}
    scene_box_size = _safe_float(scene.get("drop_box_size"))
    meters_per_unit = (
        _safe_float(scene.get("meters_per_unit"))
        or _safe_float(runtime_report.get("meters_per_unit"))
        or _safe_float(runtime_report.get("stage_meters_per_unit"))
    )
    if scene_box_size is not None:
        return _stage_unit_to_cm(scene_box_size, meters_per_unit)
    return _stage_unit_to_cm(_safe_float(runtime_report.get("box_size")), meters_per_unit)


def _runtime_motion_observed(runtime_report: Dict[str, Any]) -> Optional[bool]:
    initial_pose = _float_list(runtime_report.get("initial_pose"), length=7)
    final_pose = _float_list(runtime_report.get("final_pose"), length=7)
    if initial_pose and final_pose:
        return final_pose[2] < initial_pose[2]
    hit_analysis = runtime_report.get("hit_analysis", {}) or {}
    if "box_descended" in hit_analysis:
        return hit_analysis.get("box_descended") is True
    checks = runtime_report.get("checks", {}) or {}
    if "simulation_advanced" in checks:
        return checks.get("simulation_advanced") is True
    return None


def _expected_drop_box_size_cm(expectations: Dict[str, Any]) -> Optional[float]:
    expected_bbox = _float_list(expectations.get("expected_authored_bbox_size_cm"))
    if not expected_bbox:
        return None
    footprint_min = max(min(expected_bbox[0], expected_bbox[1]), 1e-6)
    max_span = max(expected_bbox[0], expected_bbox[1], expected_bbox[2], 8.0)
    return max(8.0, min(footprint_min * 0.45, max_span * 0.25, 75.0))


def _append_unique(target: List[str], value: str) -> None:
    if value not in target:
        target.append(value)


def _append_patch(target: List[Dict[str, Any]], patch: Dict[str, Any]) -> None:
    if patch not in target:
        target.append(patch)


def _runtime_contact_report(runtime_report: Dict[str, Any]) -> Dict[str, Any]:
    return (((runtime_report.get("final_state", {}) or {}).get("contact_report", {}) or {}))


def _runtime_checks(runtime_report: Dict[str, Any]) -> Dict[str, Any]:
    return runtime_report.get("checks", {}) or {}


def diagnose_simready(
    recommendation: Dict[str, Any],
    report: Dict[str, Any],
    runtime_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    diagnosis: List[str] = []
    suggested_patches: List[Dict[str, Any]] = []
    expectations = _expectations_from(recommendation, report)

    if not expectations:
        _check(
            checks,
            name="simready_expectations_present",
            expected="simready_expectations",
            observed=None,
            status="failed",
            reason="recommendation/report does not include simready_expectations",
        )
        diagnosis.append("missing_expectations")
        suggested_patches.append(
            {
                "target": "pipeline",
                "operation": "rerun",
                "reason": "Regenerate recommendation/report with a version that emits simready_expectations.",
            }
        )
        return {
            "status": "failed",
            "checks": checks,
            "diagnosis": diagnosis,
            "suggested_patches": suggested_patches,
        }

    _check(
        checks,
        name="simready_expectations_present",
        expected="simready_expectations",
        observed="present",
        status="passed",
    )

    units = expectations.get("units", {}) or {}
    expected_bbox = _float_list(expectations.get("expected_authored_bbox_size_cm"))
    _compare_bbox_size(
        checks,
        name="exported_usd_default_prim_bbox",
        expectations=expectations,
        expected=expected_bbox,
        observed=_report_bbox_size_cm(report),
        diagnosis_code="exported_bbox_mismatch",
        diagnosis=diagnosis,
        suggested_patches=suggested_patches,
        patch={
            "target": "recommendation.authoring",
            "operation": "review",
            "reason": "Exported USD bbox does not match expected authored bbox. Check suggested_uniform_scale and orientation_correction.",
        },
    )

    expected_up_axis = units.get("expected_output_up_axis")
    observed_up_axis = (report.get("stage", {}) or {}).get("up_axis")
    if expected_up_axis and observed_up_axis != expected_up_axis:
        _append_unique(diagnosis, "up_axis_mismatch")
        _append_patch(
            suggested_patches,
            {
                "target": "recommendation.authoring.orientation_correction.set_stage_up_axis",
                "operation": "set",
                "value": expected_up_axis,
                "reason": "Exported stage up axis differs from the expectation.",
            },
        )
        _check(
            checks,
            name="exported_stage_up_axis",
            expected=expected_up_axis,
            observed=observed_up_axis,
            status="failed",
            reason="stage up axis mismatch",
        )
    else:
        _check(checks, name="exported_stage_up_axis", expected=expected_up_axis, observed=observed_up_axis, status="passed")

    expected_mpu = _safe_float(units.get("expected_output_meters_per_unit"))
    observed_mpu = _safe_float((report.get("stage", {}) or {}).get("meters_per_unit"))
    if expected_mpu is not None and observed_mpu is not None and abs(expected_mpu - observed_mpu) > 1e-9:
        _append_unique(diagnosis, "meters_per_unit_mismatch")
        _append_patch(
            suggested_patches,
            {
                "target": "exported_stage.meters_per_unit",
                "operation": "set",
                "value": expected_mpu,
                "reason": "Exported stage metersPerUnit differs from the expectation.",
            },
        )
        _check(
            checks,
            name="exported_stage_meters_per_unit",
            expected=expected_mpu,
            observed=observed_mpu,
            status="failed",
            reason="meters per unit mismatch",
        )
    else:
        _check(
            checks,
            name="exported_stage_meters_per_unit",
            expected=expected_mpu,
            observed=observed_mpu,
            status="passed" if expected_mpu is not None else "warning",
            reason="" if expected_mpu is not None else "missing expected metersPerUnit",
        )

    if runtime_report is None:
        _check(
            checks,
            name="runtime_report_present",
            expected="optional runtime report",
            observed=None,
            status="warning",
            reason="runtime report was not provided",
        )
    else:
        runtime_backend = str(runtime_report.get("backend") or "omni_asset_cli")
        _check(
            checks,
            name="runtime_report_present",
            expected="optional runtime report",
            observed="present",
            status="passed",
        )
        runtime_status = runtime_report.get("status")
        runtime_unavailable = runtime_backend == "ovphysx" and runtime_status == "unavailable"
        if runtime_backend == "ovphysx":
            if runtime_status == "passed":
                _check(checks, name="ovphysx_runtime_status", expected="passed", observed=runtime_status, status="passed")
            elif runtime_status == "unavailable":
                _check(
                    checks,
                    name="ovphysx_runtime_status",
                    expected="passed",
                    observed=runtime_status,
                    status="warning",
                    reason=runtime_report.get("reason") or "ovphysx runtime unavailable",
                )
            else:
                _append_unique(diagnosis, "ovphysx_runtime_failed")
                _check(
                    checks,
                    name="ovphysx_runtime_status",
                    expected="passed",
                    observed=runtime_status,
                    status="failed",
                    reason=runtime_report.get("reason") or "ovphysx runtime did not pass",
                )
        _compare_bbox_size(
            checks,
            name="runtime_referenced_asset_bbox",
            expectations=expectations,
            expected=expected_bbox,
            observed=_runtime_bbox_size_cm(runtime_report),
            diagnosis_code="runtime_bbox_mismatch",
            diagnosis=diagnosis,
            suggested_patches=suggested_patches,
            patch={
                "target": "omni_asset_cli.runtime_physics_harness",
                "operation": "review",
                "reason": "Runtime asset bbox differs from expectation. Ensure the runtime wrapper does not override referenced asset xform ops.",
            },
        )

        expected_box_size = _expected_drop_box_size_cm(expectations)
        observed_box_size = _runtime_drop_actor_size_cm(runtime_report)
        if expected_box_size is not None and observed_box_size is not None:
            tolerance = max(expected_box_size * 0.1, 0.1)
            if abs(observed_box_size - expected_box_size) > tolerance:
                _append_unique(diagnosis, "drop_actor_scale_mismatch")
                _append_patch(
                    suggested_patches,
                    {
                        "target": "omni_asset_cli.runtime_physics_harness.drop_actor",
                        "operation": "make_unit_aware",
                        "reason": "Drop actor size does not match expectation-derived stage units.",
                    },
                )
                _check(
                    checks,
                    name="runtime_drop_actor_size",
                    expected=expected_box_size,
                    observed=observed_box_size,
                    status="failed",
                    reason=f"outside tolerance {tolerance}",
                )
            else:
                _check(
                    checks,
                    name="runtime_drop_actor_size",
                    expected=expected_box_size,
                    observed=observed_box_size,
                    status="passed",
                )
        else:
            _check(
                checks,
                name="runtime_drop_actor_size",
                expected=expected_box_size,
                observed=observed_box_size,
                status="warning",
                reason="missing expected or observed drop actor size",
            )

        if runtime_unavailable:
            _check(
                checks,
                name="runtime_physics_execution",
                expected="ovphysx simulation executed",
                observed=runtime_status,
                status="warning",
                reason="ovphysx runtime was unavailable; motion/contact checks were skipped",
            )
            status = _status_from_checks(checks)
            return {
                "status": status,
                "checks": checks,
                "diagnosis": diagnosis,
                "suggested_patches": suggested_patches,
            }

        hit_analysis = runtime_report.get("hit_analysis", {}) or {}
        box_descended = _runtime_motion_observed(runtime_report)
        if box_descended is not True:
            _append_unique(diagnosis, "runtime_motion_missing")
            _append_patch(
                suggested_patches,
                {
                    "target": "runtime_test",
                    "operation": "rerun_or_review",
                    "reason": "Runtime report does not show the drop actor descending.",
                },
            )
            _check(
                checks,
                name="runtime_drop_actor_motion",
                expected=True,
                observed=box_descended,
                status="failed",
                reason="drop actor did not descend",
            )
        else:
            _check(checks, name="runtime_drop_actor_motion", expected=True, observed=box_descended, status="passed")

        runtime_checks = _runtime_checks(runtime_report)
        contact_report = _runtime_contact_report(runtime_report)
        if runtime_backend == "ovphysx":
            contact_report_detected = runtime_checks.get("contact_detected")
        else:
            contact_report_detected = runtime_checks.get("contact_report_detected") or hit_analysis.get("contact_detected")
        if contact_report_detected is True:
            _check(
                checks,
                name="runtime_physx_contact_report",
                expected=True,
                observed=True,
                status="passed",
            )
        else:
            _append_unique(diagnosis, "runtime_contact_report_missing")
            _append_patch(
                suggested_patches,
                {
                    "target": "upstream_static_furniture_authoring",
                    "operation": "improve_colliders_or_contact_targeting",
                    "reason": (
                        "Downstream Linux Docker runtime did not report a PhysX contact. "
                        "Review collider generation, target mesh paths, bbox placement, and contact instrumentation."
                    ),
                    "observed": {
                        "contact_evidence_level": hit_analysis.get("contact_evidence_level"),
                        "contact_report": {
                            "event_count": contact_report.get("event_count"),
                            "target_event_count": contact_report.get("target_event_count"),
                            "asset_subtree_event_count": contact_report.get("asset_subtree_event_count"),
                            "guide_bbox_event_count": contact_report.get("guide_bbox_event_count"),
                            "errors": contact_report.get("errors"),
                        },
                    },
                },
            )
            _check(
                checks,
                name="runtime_physx_contact_report",
                expected=True,
                observed=contact_report_detected,
                status="failed",
                reason="missing strong PhysX contact evidence from Docker runtime",
            )

        render_capture = ((runtime_report.get("final_state", {}) or {}).get("render_capture", {}) or {})
        if runtime_backend == "ovphysx":
            _check(
                checks,
                name="render_artifacts",
                expected="not produced by ovphysx smoke test",
                observed=None,
                status="warning",
                reason="ovphysx smoke test validates physics/contact only",
            )
            status = _status_from_checks(checks)
            return {
                "status": status,
                "checks": checks,
                "diagnosis": diagnosis,
                "suggested_patches": suggested_patches,
            }
        render_count = _safe_float(render_capture.get("frame_count"))
        render_errors = render_capture.get("errors") or []
        if render_capture.get("enabled") and render_count and render_count > 0 and not render_errors:
            _check(
                checks,
                name="render_artifacts",
                expected="captured frames without errors",
                observed={"frame_count": int(render_count), "errors": render_errors},
                status="passed",
            )
        else:
            _append_unique(diagnosis, "render_capture_missing")
            _append_patch(
                suggested_patches,
                {
                    "target": "runtime_test.render",
                    "operation": "enable_or_review",
                    "reason": "Render capture did not produce frames without errors.",
                },
            )
            _check(
                checks,
                name="render_artifacts",
                expected="captured frames without errors",
                observed={"enabled": render_capture.get("enabled"), "frame_count": render_count, "errors": render_errors},
                status="failed",
                reason="missing render frames or capture errors",
            )

    status = _status_from_checks(checks)
    return {
        "status": status,
        "checks": checks,
        "diagnosis": diagnosis,
        "suggested_patches": suggested_patches,
    }


def format_diagnosis_summary(result: Dict[str, Any]) -> str:
    lines = [f"Status: {result.get('status', 'unknown')}"]
    diagnosis = result.get("diagnosis") or []
    if diagnosis:
        lines.append("Diagnosis:")
        for item in diagnosis:
            lines.append(f"- {item}")
    lines.append("Checks:")
    for check in result.get("checks", []) or []:
        reason = f" ({check.get('reason')})" if check.get("reason") else ""
        lines.append(f"- {check.get('status')}: {check.get('name')}{reason}")
    patches = result.get("suggested_patches") or []
    if patches:
        lines.append("Suggested patches:")
        for patch in patches:
            target = patch.get("target")
            operation = patch.get("operation")
            reason = patch.get("reason")
            value = f" value={patch.get('value')}" if "value" in patch else ""
            lines.append(f"- {target}: {operation}{value} - {reason}")
    return "\n".join(lines)
