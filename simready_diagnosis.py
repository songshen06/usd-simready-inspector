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


def _float_list(value: Any) -> Optional[List[float]]:
    if not _is_number_list(value):
        return None
    return [float(item) for item in value]


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
    return _float_list((((report.get("geometry", {}) or {}).get("bbox", {}) or {}).get("world", {}) or {}).get("size"))


def _runtime_bbox_size_cm(runtime_report: Dict[str, Any]) -> Optional[List[float]]:
    bbox_min = _float_list(runtime_report.get("asset_bbox_min"))
    bbox_max = _float_list(runtime_report.get("asset_bbox_max"))
    if not bbox_min or not bbox_max:
        return None
    return [bbox_max[index] - bbox_min[index] for index in range(3)]


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
        _check(
            checks,
            name="runtime_report_present",
            expected="optional runtime report",
            observed="present",
            status="passed",
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
        observed_box_size = _safe_float(runtime_report.get("box_size"))
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

        hit_analysis = runtime_report.get("hit_analysis", {}) or {}
        box_descended = hit_analysis.get("box_descended")
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

        render_capture = ((runtime_report.get("final_state", {}) or {}).get("render_capture", {}) or {})
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
