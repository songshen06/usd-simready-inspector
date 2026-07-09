#!/usr/bin/env python3
"""Unified CLI for USD inspection and SimReady static asset authoring."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

from simready_customer_report import generate_reports

try:
    from apply_static_furniture_simready import build_simready_expectations, main as apply_static_main
    from author_proxy_collider import author_bbox_proxy_collider, main as proxy_collider_main
    from content_physics_agent import main as content_physics_main
    from content_physics_supplement import main as physics_supplement_main
    from ovphysx_runtime_check import main as ovphysx_runtime_main
    from simready_diagnosis import diagnose_simready, format_diagnosis_summary
    from static_furniture import inspect_asset, load_json, recommend_from_reference, save_json
    from usd_inspector import build_detailed_report, open_stage
    _USD_RUNTIME_IMPORT_ERROR = None
except ModuleNotFoundError as error:
    build_simready_expectations = apply_static_main = None
    author_bbox_proxy_collider = proxy_collider_main = None
    content_physics_main = physics_supplement_main = ovphysx_runtime_main = None
    diagnose_simready = format_diagnosis_summary = None
    inspect_asset = load_json = recommend_from_reference = save_json = None
    build_detailed_report = open_stage = None
    _USD_RUNTIME_IMPORT_ERROR = error


MESH_BLOCKING_RULES = {
    "ValidateTopologyChecker",
    "ManifoldChecker",
    "ZeroAreaFaceChecker",
    "NormalsValidChecker",
    "WeldChecker",
}

CONTENT_LABEL_TARGET_BBOX_CM = {
    "wine_bottle": [7.5, 7.5, 30.0],
    "bottle": [7.5, 7.5, 30.0],
    "chair": [52.0, 58.0, 85.0],
    "wooden_chair": [52.0, 58.0, 85.0],
    "basketball": [24.0, 24.0, 24.0],
    "soccer_ball": [22.0, 22.0, 22.0],
    "football": [22.0, 22.0, 22.0],
}


def _require_usd_runtime() -> bool:
    if _USD_RUNTIME_IMPORT_ERROR is None:
        return True
    print(
        "error: this command requires the USD runtime dependencies that failed to import: "
        f"{_USD_RUNTIME_IMPORT_ERROR}",
        file=sys.stderr,
    )
    return False


def _replace_usd_suffix(path: str, suffix: str) -> str:
    for ext in (".usdz", ".usdc", ".usda", ".usd"):
        if path.lower().endswith(ext):
            return path[: -len(ext)] + suffix
    return path + suffix


def _default_process_output(input_usd: str, output_dir: Optional[str], output_format: str = "auto") -> str:
    base = os.path.basename(input_usd)
    stem, _ = os.path.splitext(base)
    directory = output_dir or os.getcwd()
    suffix = ".usdc" if output_format == "usdc" else ".usda"
    return os.path.join(directory, f"{stem}.simready_static{suffix}")


def _default_recommendation_output(input_usd: str, output_path: Optional[str] = None) -> str:
    if output_path:
        root, _ = os.path.splitext(os.path.abspath(output_path))
        return root + ".recommendation.json"
    return _replace_usd_suffix(os.path.abspath(input_usd), ".static_furniture_recommendation.json")


def _default_report_output(output_usd: str) -> str:
    return _replace_usd_suffix(os.path.abspath(output_usd), ".report.json")


def _default_mesh_preflight_output(input_usd: str, output_usd: str) -> str:
    root, _ = os.path.splitext(os.path.abspath(output_usd))
    return root + ".mesh_preflight.json"


def _default_mesh_repair_report_output(output_usd: str) -> str:
    root, _ = os.path.splitext(os.path.abspath(output_usd))
    return root + ".mesh_repair.json"


def _normalize_content_label(value: Optional[str]) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _parse_bbox_cm(value: Optional[str]) -> Optional[List[float]]:
    if not value:
        return None
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 3:
        raise ValueError("--target-bbox-cm must contain exactly three comma-separated numbers")
    bbox = [float(part) for part in parts]
    if any(item <= 0.0 for item in bbox):
        raise ValueError("--target-bbox-cm values must be positive")
    return bbox


def _source_bbox_size_cm(recommendation: Dict[str, Any]) -> Optional[List[float]]:
    for container in (recommendation.get("asset") or {}, recommendation.get("recommendation") or {}):
        size = container.get("size") or {}
        bbox_size = size.get("bbox_size")
        if isinstance(bbox_size, list) and len(bbox_size) == 3:
            return [float(item) for item in bbox_size]
        bbox = size.get("bbox") or {}
        candidate = bbox.get("size") if isinstance(bbox, dict) else None
        if isinstance(candidate, list) and len(candidate) == 3:
            return [float(item) for item in candidate]
    return None


def _apply_content_size_override(
    recommendation: Dict[str, Any],
    content_label: Optional[str],
    target_bbox_cm: Optional[List[float]],
) -> None:
    label = _normalize_content_label(content_label)
    target = target_bbox_cm or CONTENT_LABEL_TARGET_BBOX_CM.get(label)
    if not target:
        return

    source_bbox = _source_bbox_size_cm(recommendation)
    if not source_bbox:
        raise ValueError("cannot apply content size override because source bbox is missing")

    axis_scale = []
    for source_value, target_value in zip(source_bbox, target):
        if source_value <= 0.0:
            axis_scale.append(None)
        else:
            axis_scale.append(target_value / source_value)
    valid_scales = [value for value in axis_scale if value is not None]
    if not valid_scales:
        raise ValueError("cannot apply content size override because source bbox has no valid axes")
    uniform_scale = min(valid_scales)

    body = recommendation.setdefault("recommendation", {})
    body["content_label"] = label or "target_bbox_override"
    body["stage1_supported"] = True
    body["review_required"] = False
    body["review_reasons"] = []
    body["size_recommendation"] = {
        "status": "scale",
        "reference_target_bbox": target,
        "axis_scale_to_target_bbox": axis_scale,
        "suggested_uniform_scale": uniform_scale,
        "size_warning": "content_size_override",
        "basis": [
            "target bbox supplied by content label override",
            "bbox units=cm",
            "uniform scale preserves source proportions using the limiting target axis",
        ],
    }
    authoring = body.setdefault("authoring", {})
    authoring["apply_reference_scale"] = True
    authoring["suggested_uniform_scale"] = uniform_scale
    authoring["reference_target_bbox"] = target


def _default_omni_asset_python(omni_asset_cli: str) -> str:
    repo_root = os.path.dirname(os.path.abspath(os.path.expanduser(omni_asset_cli)))
    candidate = os.path.join(repo_root, ".venv", "bin", "python")
    return candidate if os.path.exists(candidate) else sys.executable


def _mesh_blocking_issues(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    issues = payload.get("issues") or []
    blocking: List[Dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if issue.get("rule") not in MESH_BLOCKING_RULES:
            continue
        if issue.get("severity") not in {"ERROR", "FAILURE", "WARNING"}:
            continue
        blocking.append(issue)
    return blocking


def _mesh_rule_counts(issues: List[Dict[str, Any]]) -> Dict[str, int]:
    rule_counts: Dict[str, int] = {}
    for issue in issues:
        rule = str(issue.get("rule") or "UnknownRule")
        rule_counts[rule] = rule_counts.get(rule, 0) + 1
    return rule_counts


def _format_mesh_rule_counts(issues: List[Dict[str, Any]]) -> str:
    return ", ".join(f"{rule}={count}" for rule, count in sorted(_mesh_rule_counts(issues).items()))


def _compact_mesh_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "rule": issue.get("rule"),
        "severity": issue.get("severity"),
        "message": issue.get("message"),
        "at": issue.get("at"),
        "code": issue.get("code"),
        "requirement": issue.get("requirement"),
        "tags": issue.get("tags"),
    }


def _run_mesh_preflight(args: argparse.Namespace, input_usd: str, output_usd: str) -> Dict[str, Any]:
    if getattr(args, "skip_mesh_preflight", False):
        return {
            "returncode": 0,
            "skipped": True,
            "output": None,
            "payload": None,
            "blocking_issues": [],
        }

    omni_asset_cli = os.path.abspath(os.path.expanduser(args.omni_asset_cli))
    omni_asset_python = os.path.abspath(os.path.expanduser(args.omni_asset_python or _default_omni_asset_python(omni_asset_cli)))
    preflight_output = args.mesh_preflight_output or _default_mesh_preflight_output(input_usd, output_usd)
    os.makedirs(os.path.dirname(os.path.abspath(preflight_output)), exist_ok=True)

    if not os.path.exists(omni_asset_cli):
        print(
            "Mesh preflight blocked: omni-asset-cli was not found at "
            f"{omni_asset_cli}. Use --omni-asset-cli or --skip-mesh-preflight.",
            file=sys.stderr,
        )
        return {"returncode": 2, "skipped": False, "output": preflight_output, "payload": None, "blocking_issues": []}

    command = [
        omni_asset_python,
        omni_asset_cli,
        "validate",
        input_usd,
        "--profile",
        "stage1-furniture",
        "--output-json",
        preflight_output,
    ]
    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if completed.returncode != 0:
        if completed.stdout:
            print(completed.stdout, file=sys.stderr)
        if completed.stderr:
            print(completed.stderr, file=sys.stderr)
        print(f"Mesh preflight command failed: {preflight_output}", file=sys.stderr)
        return {
            "returncode": completed.returncode,
            "skipped": False,
            "output": preflight_output,
            "payload": None,
            "blocking_issues": [],
        }

    payload = load_json(preflight_output)
    blocking_issues = _mesh_blocking_issues(payload)
    print(preflight_output)
    return {
        "returncode": 0,
        "skipped": False,
        "output": preflight_output,
        "payload": payload,
        "blocking_issues": blocking_issues,
    }


def _write_mesh_blocking_error(blocking_issues: List[Dict[str, Any]]) -> None:
    summary = _format_mesh_rule_counts(blocking_issues)
    print(
        "Mesh preflight blocked SimReady parameter authoring. "
        f"Repair mesh defects first, then rerun process. Blocking rules: {summary}. "
        "Use --mesh-defect-policy proxy-collider for physics proxy repair or "
        "--allow-mesh-defects only for explicit override.",
        file=sys.stderr,
    )


def _write_noop_mesh_repair_report(
    report_output: str,
    input_usd: str,
    preflight_output: Optional[str],
    blocking_issues: List[Dict[str, Any]],
) -> None:
    save_json(
        report_output,
        {
            "schema_version": 1,
            "mode": "physics-proxy",
            "action": "none",
            "status": "no_mesh_blockers_detected",
            "input_usd": os.path.abspath(input_usd),
            "output_usd": None,
            "preflight_output": os.path.abspath(preflight_output) if preflight_output else None,
            "blocking_rule_counts": _mesh_rule_counts(blocking_issues),
            "blocking_issues": [_compact_mesh_issue(issue) for issue in blocking_issues],
            "runtime_validation": {
                "optional_command": None,
                "required": False,
            },
        },
        pretty=True,
    )


def _author_mesh_proxy_repair(
    input_usd: str,
    output_usd: str,
    *,
    preflight_output: Optional[str],
    blocking_issues: List[Dict[str, Any]],
    report_output: str,
    proxy_path: Optional[str] = None,
    keep_authored_colliders: bool = False,
) -> Dict[str, Any]:
    report = author_bbox_proxy_collider(
        input_usd,
        output_usd,
        proxy_path=proxy_path,
        keep_authored_colliders=keep_authored_colliders,
        report_overrides={
            "schema_version": 1,
            "mode": "physics-proxy",
            "action": "authored_proxy_collider",
            "status": "repaired",
            "preflight_output": os.path.abspath(preflight_output) if preflight_output else None,
            "blocking_rule_counts": _mesh_rule_counts(blocking_issues),
            "blocking_issues": [_compact_mesh_issue(issue) for issue in blocking_issues],
            "visual_mesh_modified": False,
            "runtime_validation": {
                "optional_command": (
                    "python3 usd_simready_cli.py ovphysx-smoke "
                    f"{os.path.abspath(output_usd)} --asset-collider-mode authored "
                    f"--output {_replace_usd_suffix(os.path.abspath(output_usd), '.ovphysx_smoke.json')}"
                ),
                "required": False,
            },
        },
    )
    save_json(report_output, report, pretty=True)
    return report


def _write_inspection_report(
    input_usd: str,
    output: Optional[str],
    pretty: bool,
    max_prims: int,
    simready_expectations: Optional[dict] = None,
) -> str:
    stage = open_stage(input_usd)
    report = build_detailed_report(stage, input_usd, max_prims=max(0, max_prims))
    if simready_expectations is not None:
        report["simready_expectations"] = simready_expectations
    text = json.dumps(report, indent=2 if pretty else None, ensure_ascii=False)
    if output:
        with open(output, "w", encoding="utf-8") as handle:
            handle.write(text)
        return output
    print(text)
    return ""


def _write_recommendation(
    reference_json: str,
    input_usd: str,
    output: str,
    max_prims: int,
    content_label: Optional[str] = None,
    target_bbox_cm: Optional[List[float]] = None,
) -> str:
    reference = load_json(reference_json)
    inspected = inspect_asset(input_usd, max_prims=max(0, max_prims))
    recommendation = recommend_from_reference(reference, inspected["report"], inspected["knowledge"])
    _apply_content_size_override(recommendation, content_label, target_bbox_cm)
    recommendation["simready_expectations"] = build_simready_expectations(recommendation, source_usd=input_usd)
    save_json(output, recommendation, pretty=True)
    return output


def _cmd_inspect(args: argparse.Namespace) -> int:
    if not _require_usd_runtime():
        return 2
    output = _write_inspection_report(args.input_usd, args.output, args.pretty, args.max_prims)
    if output:
        print(output)
    return 0


def _cmd_recommend(args: argparse.Namespace) -> int:
    if not _require_usd_runtime():
        return 2
    output = args.output or _default_recommendation_output(args.input_usd)
    try:
        target_bbox_cm = _parse_bbox_cm(args.target_bbox_cm)
        _write_recommendation(args.reference_json, args.input_usd, output, args.max_prims, args.content_label, target_bbox_cm)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(output)
    return 0


def _apply_args(args: argparse.Namespace, input_usd: str, recommendation_json: str, output_usd: str) -> List[str]:
    apply_args = [input_usd, recommendation_json, "--output", output_usd]
    if getattr(args, "output_format", "auto"):
        apply_args.extend(["--output-format", args.output_format])
    if getattr(args, "allow_missing_assets", False):
        apply_args.append("--allow-missing-assets")
    if getattr(args, "no_copy_relative_assets", False):
        apply_args.append("--no-copy-relative-assets")
    if getattr(args, "bundle_omniverse_builtin_mdl", False):
        apply_args.append("--bundle-omniverse-builtin-mdl")
    if getattr(args, "no_apply_reference_scale", False):
        apply_args.append("--no-apply-reference-scale")
    if getattr(args, "skip_size_validation", False):
        apply_args.append("--skip-size-validation")
    if getattr(args, "author_rigid_body", False):
        apply_args.append("--author-rigid-body")
    if getattr(args, "author_center_of_mass", False):
        apply_args.append("--author-center-of-mass")
    if getattr(args, "no_author_center_of_mass", False):
        apply_args.append("--no-author-center-of-mass")
    center_of_mass_policy = getattr(args, "center_of_mass_policy", None)
    if center_of_mass_policy:
        apply_args.extend(["--center-of-mass-policy", center_of_mass_policy])
    return apply_args


def _cmd_apply(args: argparse.Namespace) -> int:
    if not _require_usd_runtime():
        return 2
    return apply_static_main(_apply_args(args, args.input_usd, args.recommendation_json, args.output))


def _cmd_process(args: argparse.Namespace) -> int:
    if not _require_usd_runtime():
        return 2
    output_usd = args.output or _default_process_output(args.input_usd, args.output_dir, args.output_format)
    os.makedirs(os.path.dirname(os.path.abspath(output_usd)), exist_ok=True)

    preflight_result = _run_mesh_preflight(args, args.input_usd, output_usd)
    if preflight_result["returncode"] != 0:
        return int(preflight_result["returncode"])
    blocking_issues = preflight_result.get("blocking_issues") or []
    repair_mesh_proxy = bool(blocking_issues and args.mesh_defect_policy == "proxy-collider")
    if blocking_issues and not getattr(args, "allow_mesh_defects", False) and not repair_mesh_proxy:
        _write_mesh_blocking_error(blocking_issues)
        return 3

    recommendation_output = args.recommendation_output or _default_recommendation_output(args.input_usd, output_usd)
    os.makedirs(os.path.dirname(os.path.abspath(recommendation_output)), exist_ok=True)
    try:
        target_bbox_cm = _parse_bbox_cm(args.target_bbox_cm)
        _write_recommendation(
            args.reference_json,
            args.input_usd,
            recommendation_output,
            args.max_prims,
            args.content_label,
            target_bbox_cm,
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    apply_result = apply_static_main(_apply_args(args, args.input_usd, recommendation_output, output_usd))
    if apply_result != 0:
        return apply_result

    if repair_mesh_proxy:
        mesh_repair_report = args.mesh_repair_report_output or _default_mesh_repair_report_output(output_usd)
        try:
            _author_mesh_proxy_repair(
                output_usd,
                output_usd,
                preflight_output=preflight_result.get("output"),
                blocking_issues=blocking_issues,
                report_output=mesh_repair_report,
            )
        except Exception as error:
            print(f"error: mesh proxy repair failed: {error}", file=sys.stderr)
            return 2
        print(mesh_repair_report)

    if args.report_output or args.emit_report:
        report_output = args.report_output or _default_report_output(output_usd)
        recommendation = load_json(recommendation_output)
        expectations = build_simready_expectations(
            recommendation,
            source_usd=args.input_usd,
            output_usd=output_usd,
            authoring_overrides={"apply_reference_scale": False} if args.no_apply_reference_scale else None,
        )
        _write_inspection_report(output_usd, report_output, True, args.max_prims, expectations)
        print(report_output)

    print(recommendation_output)
    return 0


def _cmd_mesh_repair(args: argparse.Namespace) -> int:
    if not _require_usd_runtime():
        return 2
    input_usd = os.path.abspath(args.input_usd)
    output_usd = os.path.abspath(args.output or _replace_usd_suffix(input_usd, ".mesh_repaired.usda"))
    report_output = os.path.abspath(args.report or _default_mesh_repair_report_output(output_usd))
    preflight_output = os.path.abspath(args.preflight)
    try:
        preflight_payload = load_json(preflight_output)
    except Exception as error:
        print(f"error: could not read preflight JSON: {error}", file=sys.stderr)
        return 2

    blocking_issues = _mesh_blocking_issues(preflight_payload)
    if not blocking_issues and not args.force:
        _write_noop_mesh_repair_report(report_output, input_usd, preflight_output, blocking_issues)
        print(report_output)
        return 0

    try:
        _author_mesh_proxy_repair(
            input_usd,
            output_usd,
            preflight_output=preflight_output,
            blocking_issues=blocking_issues,
            report_output=report_output,
            proxy_path=args.proxy_path,
            keep_authored_colliders=args.keep_authored_colliders,
        )
    except Exception as error:
        print(f"error: mesh proxy repair failed: {error}", file=sys.stderr)
        return 2

    print(output_usd)
    print(report_output)
    return 0


def _cmd_physics_agent(args: argparse.Namespace) -> int:
    if not _require_usd_runtime():
        return 2
    agent_args = [args.input_usd]
    agent_args.extend(["--output-dir", args.output_dir])
    agent_args.extend(["--content-agents-root", args.content_agents_root])
    agent_args.extend(["--physics-agent", args.physics_agent])
    agent_args.extend(["--render-backend", args.render_backend])
    agent_args.extend(["--collision-approx", args.collision_approx])
    if args.vlm_backend:
        agent_args.extend(["--vlm-backend", args.vlm_backend])
    if args.vlm_model:
        agent_args.extend(["--vlm-model", args.vlm_model])
    if args.summary_json:
        agent_args.extend(["--summary-json", args.summary_json])
    if args.dry_run:
        agent_args.append("--dry-run")
    if args.clean:
        agent_args.append("--clean")
    if args.resume:
        agent_args.append("--resume")
    if args.skip:
        agent_args.extend(["--skip", args.skip])
    if args.only:
        agent_args.extend(["--only", args.only])
    return content_physics_main(agent_args)


def _cmd_physics_supplement(args: argparse.Namespace) -> int:
    if not _require_usd_runtime():
        return 2
    supplement_args = [args.recommendation_json, "--physics-predictions", args.physics_predictions]
    if args.source_usd:
        supplement_args.extend(["--source-usd", args.source_usd])
    if args.output:
        supplement_args.extend(["--output", args.output])
    if args.center_of_mass_mode:
        supplement_args.extend(["--center-of-mass-mode", args.center_of_mass_mode])
    return physics_supplement_main(supplement_args)


def _cmd_diagnose(args: argparse.Namespace) -> int:
    if not _require_usd_runtime():
        return 2
    recommendation = load_json(args.recommendation)
    report = load_json(args.report)
    runtime_report = load_json(args.runtime_report) if args.runtime_report else None
    result = diagnose_simready(recommendation, report, runtime_report)
    if args.output:
        save_json(args.output, result, pretty=True)
        print(args.output)
    print(format_diagnosis_summary(result))
    return 0 if result.get("status") in {"passed", "warning"} else 1


def _cmd_customer_report(args: argparse.Namespace) -> int:
    try:
        for path in generate_reports(args):
            print(path)
    except ValueError as exc:
        print(f"customer-report: {exc}", file=sys.stderr)
        return 2
    return 0


def _cmd_ovphysx_smoke(args: argparse.Namespace) -> int:
    if not _require_usd_runtime():
        return 2
    smoke_args = [args.input_usd]
    if args.output:
        smoke_args.extend(["--output", args.output])
    if args.work_dir:
        smoke_args.extend(["--work-dir", args.work_dir])
    if args.ovphysx_python:
        smoke_args.extend(["--ovphysx-python", args.ovphysx_python])
    smoke_args.extend(["--frames", str(args.frames)])
    smoke_args.extend(["--fps", str(args.fps)])
    smoke_args.extend(["--device", args.device])
    smoke_args.extend(["--contact-force-threshold", str(args.contact_force_threshold)])
    if args.box_size is not None:
        smoke_args.extend(["--box-size", str(args.box_size)])
    if args.drop_height is not None:
        smoke_args.extend(["--drop-height", str(args.drop_height)])
    smoke_args.extend(["--asset-collider-mode", args.asset_collider_mode])
    if args.dry_run:
        smoke_args.append("--dry-run")
    return ovphysx_runtime_main(smoke_args)


def _cmd_proxy_collider(args: argparse.Namespace) -> int:
    if not _require_usd_runtime():
        return 2
    proxy_args = [args.input_usd]
    if args.output:
        proxy_args.extend(["--output", args.output])
    if args.proxy_path:
        proxy_args.extend(["--proxy-path", args.proxy_path])
    if args.keep_authored_colliders:
        proxy_args.append("--keep-authored-colliders")
    if args.report:
        proxy_args.extend(["--report", args.report])
    return proxy_collider_main(proxy_args)


def _add_apply_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output-format",
        choices=["auto", "usda", "usdc"],
        default="auto",
        help="USD export format. auto follows the output extension; usdc writes compact binary USD.",
    )
    parser.add_argument(
        "--allow-missing-assets",
        action="store_true",
        help="Export even when non-bundled relative asset dependencies are missing",
    )
    parser.add_argument(
        "--no-copy-relative-assets",
        action="store_true",
        help="Do not copy resolvable relative asset dependencies next to the output USD",
    )
    parser.add_argument(
        "--bundle-omniverse-builtin-mdl",
        action="store_true",
        help=(
            "Bundle fallback MDL files for Omniverse built-in modules such as gltf/pbr.mdl. "
            "By default these paths are preserved so Omniverse/Isaac Sim can use its native glTF PBR shader."
        ),
    )
    parser.add_argument(
        "--no-apply-reference-scale",
        action="store_true",
        help="Do not apply recommendation.authoring.suggested_uniform_scale to the default prim",
    )
    parser.add_argument(
        "--skip-size-validation",
        action="store_true",
        help="Skip post-export bbox validation against the recommendation scale/orientation",
    )
    parser.add_argument(
        "--author-rigid-body",
        action="store_true",
        help="Override recommendation.authoring.author_rigid_body and author a kinematic rigid body on the default prim",
    )
    parser.add_argument(
        "--author-center-of-mass",
        action="store_true",
        help="Author UsdPhysics.MassAPI centerOfMass on the default prim",
    )
    parser.add_argument(
        "--no-author-center-of-mass",
        action="store_true",
        help="Do not author UsdPhysics.MassAPI centerOfMass, even if the recommendation enables it",
    )
    parser.add_argument(
        "--center-of-mass-policy",
        choices=["bbox_center", "explicit", "none"],
        help="How to choose centerOfMass when authoring it. explicit uses recommendation.authoring.center_of_mass.",
    )


def _add_content_size_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--content-label",
        help=(
            "Optional normalized content label used for built-in physical size priors "
            "such as wine_bottle, chair, basketball, or soccer_ball."
        ),
    )
    parser.add_argument(
        "--target-bbox-cm",
        help=(
            "Explicit target bbox in centimeters as X,Y,Z. Overrides the built-in "
            "--content-label size prior and preserves source proportions by uniform scale."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified USD SimReady inspection and static authoring CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a USD asset and emit a JSON report")
    inspect_parser.add_argument("input_usd")
    inspect_parser.add_argument("--output")
    inspect_parser.add_argument("--pretty", action="store_true")
    inspect_parser.add_argument("--max-prims", type=int, default=0)
    inspect_parser.set_defaults(func=_cmd_inspect)

    recommend_parser = subparsers.add_parser("recommend", help="Generate a SimReady recommendation JSON")
    recommend_parser.add_argument("reference_json")
    recommend_parser.add_argument("input_usd")
    recommend_parser.add_argument("--output")
    recommend_parser.add_argument("--max-prims", type=int, default=0)
    _add_content_size_flags(recommend_parser)
    recommend_parser.set_defaults(func=_cmd_recommend)

    apply_parser = subparsers.add_parser("apply", help="Apply a recommendation and export a self-contained USD")
    apply_parser.add_argument("input_usd")
    apply_parser.add_argument("recommendation_json")
    apply_parser.add_argument("--output", required=True)
    _add_apply_flags(apply_parser)
    apply_parser.set_defaults(func=_cmd_apply)

    process_parser = subparsers.add_parser("process", help="Recommend, apply, and optionally inspect in one step")
    process_parser.add_argument("reference_json")
    process_parser.add_argument("input_usd")
    process_parser.add_argument("--output")
    process_parser.add_argument("--output-dir")
    process_parser.add_argument("--recommendation-output")
    process_parser.add_argument("--emit-report", action="store_true")
    process_parser.add_argument("--report-output")
    process_parser.add_argument("--max-prims", type=int, default=0)
    _add_content_size_flags(process_parser)
    process_parser.add_argument(
        "--omni-asset-cli",
        default=os.path.expanduser("~/omni-asset-cli/omni_asset_cli.py"),
        help="Path to omni-asset-cli.py used for source mesh preflight validation",
    )
    process_parser.add_argument(
        "--omni-asset-python",
        help="Python executable for omni-asset-cli; defaults to its .venv/bin/python when present",
    )
    process_parser.add_argument(
        "--mesh-preflight-output",
        help="Path to the source mesh preflight JSON report",
    )
    process_parser.add_argument(
        "--skip-mesh-preflight",
        action="store_true",
        help="Skip omni-asset-cli source mesh validation before recommendation and authoring",
    )
    process_parser.add_argument(
        "--allow-mesh-defects",
        action="store_true",
        help="Continue processing even when mesh topology/manifold/normal defects are detected",
    )
    process_parser.add_argument(
        "--mesh-defect-policy",
        choices=["block", "proxy-collider"],
        default="block",
        help=(
            "How process handles mesh preflight blockers. block preserves the existing stop behavior; "
            "proxy-collider authors a physics proxy collider after apply without modifying visual meshes."
        ),
    )
    process_parser.add_argument(
        "--mesh-repair-report-output",
        help="Path to write mesh physics proxy repair JSON when --mesh-defect-policy proxy-collider is used",
    )
    _add_apply_flags(process_parser)
    process_parser.set_defaults(func=_cmd_process)

    mesh_repair_parser = subparsers.add_parser(
        "mesh-repair",
        help="Author a physics proxy collider when mesh preflight blockers affect collision reliability",
    )
    mesh_repair_parser.add_argument("input_usd")
    mesh_repair_parser.add_argument("--preflight", required=True, help="omni-asset-cli validate JSON report")
    mesh_repair_parser.add_argument("--output", help="Output USD path; defaults to <input>.mesh_repaired.usda")
    mesh_repair_parser.add_argument("--report", help="Mesh repair JSON report path")
    mesh_repair_parser.add_argument("--proxy-path", help="Absolute proxy prim path. Defaults under the defaultPrim.")
    mesh_repair_parser.add_argument(
        "--keep-authored-colliders",
        action="store_true",
        help="Keep existing authored colliders enabled instead of disabling them",
    )
    mesh_repair_parser.add_argument(
        "--force",
        action="store_true",
        help="Author a proxy collider even when the preflight report has no mesh blocker rules",
    )
    mesh_repair_parser.set_defaults(func=_cmd_mesh_repair)

    physics_agent_parser = subparsers.add_parser(
        "physics-agent",
        help="Run NVIDIA Content Agents Physics Agent against a USD asset",
    )
    physics_agent_parser.add_argument("input_usd")
    physics_agent_parser.add_argument("--output-dir", default="out/content_physics")
    physics_agent_parser.add_argument("--content-agents-root", default=os.path.expanduser("~/content-agents"))
    physics_agent_parser.add_argument(
        "--physics-agent",
        default=os.path.expanduser("~/content-agents/.venv/bin/physics-agent"),
    )
    physics_agent_parser.add_argument("--render-backend", choices=["ovrtx", "remote", "warp"], default="remote")
    physics_agent_parser.add_argument("--vlm-backend")
    physics_agent_parser.add_argument("--vlm-model")
    physics_agent_parser.add_argument("--collision-approx", default="convexHull")
    physics_agent_parser.add_argument("--summary-json")
    physics_agent_parser.add_argument("--dry-run", action="store_true")
    physics_agent_parser.add_argument("--clean", action="store_true")
    physics_agent_parser.add_argument("--resume", action="store_true")
    physics_agent_parser.add_argument("--skip")
    physics_agent_parser.add_argument("--only")
    physics_agent_parser.set_defaults(func=_cmd_physics_agent)

    ovphysx_parser = subparsers.add_parser(
        "ovphysx-smoke",
        help="Run a lightweight ovphysx drop/contact smoke test in a separate Python environment",
    )
    ovphysx_parser.add_argument("input_usd")
    ovphysx_parser.add_argument("--output")
    ovphysx_parser.add_argument("--work-dir")
    ovphysx_parser.add_argument(
        "--ovphysx-python",
        default=os.environ.get("OVPHYSX_PYTHON") or "python3",
        help="Python executable with ovphysx installed; defaults to OVPHYSX_PYTHON or python3",
    )
    ovphysx_parser.add_argument("--frames", type=int, default=240)
    ovphysx_parser.add_argument("--fps", type=float, default=60.0)
    ovphysx_parser.add_argument("--device", default="cpu")
    ovphysx_parser.add_argument("--box-size", type=float, help="Drop box size in input stage units")
    ovphysx_parser.add_argument("--drop-height", type=float, help="Gap above asset top in input stage units")
    ovphysx_parser.add_argument(
        "--asset-collider-mode",
        choices=["authored", "bbox-proxy"],
        default="authored",
        help="Use authored asset colliders or a temporary bbox proxy collider in the smoke scene",
    )
    ovphysx_parser.add_argument("--contact-force-threshold", type=float, default=1e-5)
    ovphysx_parser.add_argument("--dry-run", action="store_true")
    ovphysx_parser.set_defaults(func=_cmd_ovphysx_smoke)

    proxy_parser = subparsers.add_parser(
        "proxy-collider",
        help="Author a lightweight bbox proxy collider for a USD asset",
    )
    proxy_parser.add_argument("input_usd")
    proxy_parser.add_argument("--output")
    proxy_parser.add_argument("--proxy-path")
    proxy_parser.add_argument(
        "--keep-authored-colliders",
        action="store_true",
        help="Keep existing authored colliders enabled instead of disabling them",
    )
    proxy_parser.add_argument("--report", help="Optional JSON authoring report")
    proxy_parser.set_defaults(func=_cmd_proxy_collider)

    supplement_parser = subparsers.add_parser(
        "physics-supplement",
        help="Append Content Agents Physics Agent predictions to a recommendation JSON",
    )
    supplement_parser.add_argument("recommendation_json")
    supplement_parser.add_argument("--physics-predictions", required=True)
    supplement_parser.add_argument("--source-usd")
    supplement_parser.add_argument("--output")
    supplement_parser.add_argument(
        "--center-of-mass-mode",
        choices=["none", "bbox_center", "lower_center", "semantic_weighted"],
        default="none",
        help="Optional centerOfMass enhancement written as explicit recommendation authoring data",
    )
    supplement_parser.set_defaults(func=_cmd_physics_supplement)

    diagnose_parser = subparsers.add_parser(
        "diagnose",
        help="Compare SimReady expectations against inspection and optional runtime reports",
    )
    diagnose_parser.add_argument("--recommendation", required=True)
    diagnose_parser.add_argument("--report", required=True)
    diagnose_parser.add_argument("--runtime-report")
    diagnose_parser.add_argument("--output")
    diagnose_parser.set_defaults(func=_cmd_diagnose)

    customer_report_parser = subparsers.add_parser(
        "customer-report",
        help="Generate bilingual customer-facing HTML reports for the SimReady workflow",
    )
    customer_report_parser.add_argument("--asset-name")
    customer_report_parser.add_argument("--source-usd")
    customer_report_parser.add_argument("--output-usd")
    customer_report_parser.add_argument("--recommendation", required=True)
    customer_report_parser.add_argument("--report", required=True, help="usd-simready-inspector inspection report JSON")
    customer_report_parser.add_argument("--omni-validate", help="omni-asset-cli validate JSON")
    customer_report_parser.add_argument("--runtime-summary", help="Downstream runtime summary.json")
    customer_report_parser.add_argument("--runtime-report", help="Downstream runtime_report.json")
    customer_report_parser.add_argument("--proxy-report", help="Optional proxy collider / mesh repair report JSON")
    customer_report_parser.add_argument("--video", help="Validation video path, usually compressed mp4")
    customer_report_parser.add_argument("--compressed-video", help="Pre-compressed validation video path")
    customer_report_parser.add_argument("--internal-video", help="Internal engineering evidence video path, usually with debug overlays")
    customer_report_parser.add_argument("--internal-compressed-video", help="Pre-compressed internal engineering evidence video path")
    customer_report_parser.add_argument(
        "--compress-video",
        action="store_true",
        help="Compress --video with ffmpeg before embedding/linking",
    )
    customer_report_parser.add_argument("--video-max-width", type=int, default=960)
    customer_report_parser.add_argument("--video-crf", type=int, default=32)
    customer_report_parser.add_argument("--max-embed-mb", type=float, default=8.0)
    customer_report_parser.add_argument("--no-embed-video", action="store_true")
    customer_report_parser.add_argument(
        "--require-video",
        action="store_true",
        help="Fail if no non-empty video can be used; when embedding is enabled the video must fit --max-embed-mb",
    )
    customer_report_parser.add_argument("--output-base", help="Base output path when --output-zh/--output-en are omitted")
    customer_report_parser.add_argument("--output-json", help="Structured customer report JSON output")
    customer_report_parser.add_argument("--output-zh")
    customer_report_parser.add_argument("--output-en")
    customer_report_parser.set_defaults(func=_cmd_customer_report)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
