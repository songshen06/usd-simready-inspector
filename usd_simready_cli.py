#!/usr/bin/env python3
"""Unified CLI for USD inspection and SimReady static asset authoring."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from apply_static_furniture_simready import build_simready_expectations, main as apply_static_main
from content_physics_agent import main as content_physics_main
from content_physics_supplement import main as physics_supplement_main
from simready_diagnosis import diagnose_simready, format_diagnosis_summary
from static_furniture import inspect_asset, load_json, recommend_from_reference, save_json
from usd_inspector import build_detailed_report, open_stage


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


def _write_recommendation(reference_json: str, input_usd: str, output: str, max_prims: int) -> str:
    reference = load_json(reference_json)
    inspected = inspect_asset(input_usd, max_prims=max(0, max_prims))
    recommendation = recommend_from_reference(reference, inspected["report"], inspected["knowledge"])
    recommendation["simready_expectations"] = build_simready_expectations(recommendation, source_usd=input_usd)
    save_json(output, recommendation, pretty=True)
    return output


def _cmd_inspect(args: argparse.Namespace) -> int:
    output = _write_inspection_report(args.input_usd, args.output, args.pretty, args.max_prims)
    if output:
        print(output)
    return 0


def _cmd_recommend(args: argparse.Namespace) -> int:
    output = args.output or _default_recommendation_output(args.input_usd)
    _write_recommendation(args.reference_json, args.input_usd, output, args.max_prims)
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
    if getattr(args, "no_apply_reference_scale", False):
        apply_args.append("--no-apply-reference-scale")
    if getattr(args, "skip_size_validation", False):
        apply_args.append("--skip-size-validation")
    return apply_args


def _cmd_apply(args: argparse.Namespace) -> int:
    return apply_static_main(_apply_args(args, args.input_usd, args.recommendation_json, args.output))


def _cmd_process(args: argparse.Namespace) -> int:
    output_usd = args.output or _default_process_output(args.input_usd, args.output_dir, args.output_format)
    os.makedirs(os.path.dirname(os.path.abspath(output_usd)), exist_ok=True)

    recommendation_output = args.recommendation_output or _default_recommendation_output(args.input_usd, output_usd)
    os.makedirs(os.path.dirname(os.path.abspath(recommendation_output)), exist_ok=True)
    _write_recommendation(args.reference_json, args.input_usd, recommendation_output, args.max_prims)

    apply_result = apply_static_main(_apply_args(args, args.input_usd, recommendation_output, output_usd))
    if apply_result != 0:
        return apply_result

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


def _cmd_physics_agent(args: argparse.Namespace) -> int:
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
    supplement_args = [args.recommendation_json, "--physics-predictions", args.physics_predictions]
    if args.source_usd:
        supplement_args.extend(["--source-usd", args.source_usd])
    if args.output:
        supplement_args.extend(["--output", args.output])
    if args.center_of_mass_mode:
        supplement_args.extend(["--center-of-mass-mode", args.center_of_mass_mode])
    return physics_supplement_main(supplement_args)


def _cmd_diagnose(args: argparse.Namespace) -> int:
    recommendation = load_json(args.recommendation)
    report = load_json(args.report)
    runtime_report = load_json(args.runtime_report) if args.runtime_report else None
    result = diagnose_simready(recommendation, report, runtime_report)
    if args.output:
        save_json(args.output, result, pretty=True)
        print(args.output)
    print(format_diagnosis_summary(result))
    return 0 if result.get("status") in {"passed", "warning"} else 1


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
        "--no-apply-reference-scale",
        action="store_true",
        help="Do not apply recommendation.authoring.suggested_uniform_scale to the default prim",
    )
    parser.add_argument(
        "--skip-size-validation",
        action="store_true",
        help="Skip post-export bbox validation against the recommendation scale/orientation",
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
    _add_apply_flags(process_parser)
    process_parser.set_defaults(func=_cmd_process)

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

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
