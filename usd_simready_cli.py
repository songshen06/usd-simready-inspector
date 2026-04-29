#!/usr/bin/env python3
"""Unified CLI for USD inspection and SimReady static asset authoring."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from apply_static_furniture_simready import main as apply_static_main
from static_furniture import inspect_asset, load_json, recommend_from_reference, save_json
from usd_inspector import build_detailed_report, open_stage


def _replace_usd_suffix(path: str, suffix: str) -> str:
    for ext in (".usdz", ".usdc", ".usda", ".usd"):
        if path.lower().endswith(ext):
            return path[: -len(ext)] + suffix
    return path + suffix


def _default_process_output(input_usd: str, output_dir: Optional[str]) -> str:
    base = os.path.basename(input_usd)
    stem, _ = os.path.splitext(base)
    directory = output_dir or os.getcwd()
    return os.path.join(directory, f"{stem}.simready_static.usda")


def _default_recommendation_output(input_usd: str, output_path: Optional[str] = None) -> str:
    if output_path:
        root, _ = os.path.splitext(os.path.abspath(output_path))
        return root + ".recommendation.json"
    return _replace_usd_suffix(os.path.abspath(input_usd), ".static_furniture_recommendation.json")


def _default_report_output(output_usd: str) -> str:
    return _replace_usd_suffix(os.path.abspath(output_usd), ".report.json")


def _write_inspection_report(input_usd: str, output: Optional[str], pretty: bool, max_prims: int) -> str:
    stage = open_stage(input_usd)
    report = build_detailed_report(stage, input_usd, max_prims=max(0, max_prims))
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
    if getattr(args, "allow_missing_assets", False):
        apply_args.append("--allow-missing-assets")
    if getattr(args, "no_copy_relative_assets", False):
        apply_args.append("--no-copy-relative-assets")
    if getattr(args, "no_apply_reference_scale", False):
        apply_args.append("--no-apply-reference-scale")
    return apply_args


def _cmd_apply(args: argparse.Namespace) -> int:
    return apply_static_main(_apply_args(args, args.input_usd, args.recommendation_json, args.output))


def _cmd_process(args: argparse.Namespace) -> int:
    output_usd = args.output or _default_process_output(args.input_usd, args.output_dir)
    os.makedirs(os.path.dirname(os.path.abspath(output_usd)), exist_ok=True)

    recommendation_output = args.recommendation_output or _default_recommendation_output(args.input_usd, output_usd)
    os.makedirs(os.path.dirname(os.path.abspath(recommendation_output)), exist_ok=True)
    _write_recommendation(args.reference_json, args.input_usd, recommendation_output, args.max_prims)

    apply_result = apply_static_main(_apply_args(args, args.input_usd, recommendation_output, output_usd))
    if apply_result != 0:
        return apply_result

    if args.report_output or args.emit_report:
        report_output = args.report_output or _default_report_output(output_usd)
        _write_inspection_report(output_usd, report_output, True, args.max_prims)
        print(report_output)

    print(recommendation_output)
    return 0


def _add_apply_flags(parser: argparse.ArgumentParser) -> None:
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

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
