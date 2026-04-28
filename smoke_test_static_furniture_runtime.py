#!/usr/bin/env python3
"""Run an omni-asset-cli top-drop smoke test from a furniture recommendation."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from typing import Any, Dict, List, Optional

from static_furniture import load_json, save_json


def _default_report_path(recommendation_json: str) -> str:
    root, _ = os.path.splitext(os.path.abspath(recommendation_json))
    return root + ".top_drop_smoke.json"


def _recommendation_body(data: Dict[str, Any]) -> Dict[str, Any]:
    return data.get("recommendation", {}) or {}


def _source_usd(data: Dict[str, Any], explicit_source: Optional[str]) -> str:
    if explicit_source:
        return explicit_source
    body = _recommendation_body(data)
    authoring = body.get("authoring", {}) or {}
    asset = data.get("asset", {}) or {}
    return authoring.get("source_usd_for_authoring") or asset.get("authoring_source_file") or asset.get("file") or ""


def _build_default_command(cli: str, source_usd: str, recommendation_json: str) -> List[str]:
    return [
        cli,
        "top-drop",
        "--input",
        source_usd,
        "--recommendation",
        recommendation_json,
        "--preserve-runtime",
    ]


def _build_template_command(template: str, source_usd: str, recommendation_json: str, output: str) -> List[str]:
    rendered = template.format(
        input=source_usd,
        recommendation=recommendation_json,
        output=output,
    )
    return shlex.split(rendered)


def _smoke_metadata(data: Dict[str, Any], source_usd: str) -> Dict[str, Any]:
    body = _recommendation_body(data)
    collision_plan = body.get("collision_plan", {}) or {}
    size = body.get("size", {}) or {}
    return {
        "source_usd": source_usd,
        "review_required": bool(body.get("review_required")),
        "review_reasons": body.get("review_reasons", []) or [],
        "auto_apply_safe": bool(collision_plan.get("auto_apply_safe")),
        "recommended_collider": collision_plan.get("recommended_collider"),
        "usd_approximation": collision_plan.get("usd_approximation"),
        "target_mesh_paths": collision_plan.get("target_mesh_paths", []) or [],
        "bbox": size.get("bbox"),
        "footprint": size.get("footprint"),
        "preserve_runtime": True,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pass a static furniture recommendation to omni-asset-cli top-drop with preserve-runtime enabled."
    )
    parser.add_argument("recommendation_json", help="Recommendation JSON from recommend_static_furniture_simready.py")
    parser.add_argument("--input", dest="input_usd", help="Override source USD path")
    parser.add_argument("--cli", default="omni-asset-cli", help="omni-asset-cli executable name or path")
    parser.add_argument("--output", help="Path to write smoke-test JSON report")
    parser.add_argument("--dry-run", action="store_true", help="Write the planned command without executing it")
    parser.add_argument("--force", action="store_true", help="Run even when review_required or auto_apply_safe=false")
    parser.add_argument(
        "--command-template",
        help=(
            "Override command. Supports {input}, {recommendation}, and {output}. "
            "Example: 'omni-asset-cli top-drop --asset {input} --config {recommendation} --preserve-runtime'"
        ),
    )
    args = parser.parse_args(argv)

    recommendation_path = os.path.abspath(args.recommendation_json)
    data = load_json(recommendation_path)
    source_usd_raw = _source_usd(data, args.input_usd)
    source_usd = os.path.abspath(source_usd_raw) if source_usd_raw else ""
    output_path = args.output or _default_report_path(recommendation_path)
    metadata = _smoke_metadata(data, source_usd)

    if not source_usd:
        metadata.update({"status": "blocked", "reason": "source_usd_missing"})
        save_json(output_path, metadata, pretty=True)
        print(output_path)
        return 2

    if not args.force and (metadata["review_required"] or not metadata["auto_apply_safe"]):
        metadata.update({"status": "blocked", "reason": "recommendation_requires_review_or_is_not_auto_apply_safe"})
        save_json(output_path, metadata, pretty=True)
        print(output_path)
        return 2

    command = (
        _build_template_command(args.command_template, source_usd, recommendation_path, output_path)
        if args.command_template
        else _build_default_command(args.cli, source_usd, recommendation_path)
    )
    metadata["command"] = command

    if args.dry_run:
        metadata["status"] = "dry_run"
        save_json(output_path, metadata, pretty=True)
        print(output_path)
        return 0

    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
    except FileNotFoundError as exc:
        metadata.update({"status": "error", "returncode": 127, "stderr": str(exc)})
        save_json(output_path, metadata, pretty=True)
        print(output_path)
        return 127

    metadata.update(
        {
            "status": "passed" if completed.returncode == 0 else "failed",
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    )
    save_json(output_path, metadata, pretty=True)
    print(output_path)
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
