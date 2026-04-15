#!/usr/bin/env python3
"""Convert existing inspector report JSON files into knowledge candidate JSON."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

from knowledge_candidate import build_knowledge_candidate


def _default_output_path(report_path: str) -> str:
    if report_path.endswith(".report.json"):
        return report_path[:-len(".report.json")] + ".knowledge_candidate.json"
    if report_path.endswith(".json"):
        return report_path[:-len(".json")] + ".knowledge_candidate.json"
    return report_path + ".knowledge_candidate.json"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Convert inspector report JSON into knowledge candidate JSON.")
    parser.add_argument("report_json", help="Path to detailed inspector report JSON")
    parser.add_argument("--output", help="Path to write knowledge candidate JSON")
    parser.add_argument(
        "--variant-role",
        default="auto",
        choices=["auto", "main", "base", "inst", "inst_base", "unknown"],
        help="Override asset variant role instead of inferring from file name",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    args = parser.parse_args(argv)

    with open(args.report_json, "r", encoding="utf-8") as handle:
        report: Dict[str, Any] = json.load(handle)

    knowledge = build_knowledge_candidate(
        report,
        variant_role_override=None if args.variant_role == "auto" else args.variant_role,
    )
    json_text = json.dumps(knowledge, ensure_ascii=False, indent=2 if args.pretty else None)

    output_path = args.output or _default_output_path(args.report_json)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(json_text)
    if not args.output:
        print(output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

