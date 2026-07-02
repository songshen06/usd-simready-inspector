#!/usr/bin/env python3
"""Generate customer-facing SimReady workflow HTML reports."""

from __future__ import annotations

import argparse
import base64
import html
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


LANG = {
    "zh": {
        "title": "SimReady 资产转化报告",
        "subtitle": "从普通 mesh 到可验证的 SimReady 资产",
        "value": "核心成果",
        "value_copy": "输入资产从只有视觉网格的普通 mesh，经过尺寸归一、坐标系校正、碰撞体 authoring、物理元数据补全和下游运行时验证，形成可进入仿真/机器人/合成数据流程的 SimReady 静态资产。",
        "workflow": "Workflow 做了什么",
        "validation": "检测与验证",
        "flywheel": "数据飞轮",
        "evidence": "检测视频证据",
        "asset": "资产",
        "source": "输入 mesh",
        "output": "输出 SimReady USD",
        "status": "状态",
        "passed": "通过",
        "warning": "需关注",
        "failed": "未通过",
        "blocked": "执行受阻",
        "unknown": "未知",
        "metric_stage": "Stage",
        "metric_scale": "尺寸缩放",
        "metric_orientation": "朝向校正",
        "metric_collider": "碰撞体",
        "metric_physics": "物理 Schema",
        "metric_runtime": "运行时验证",
        "ordinary": "普通 mesh",
        "simready": "SimReady 资产",
        "details": "关键证据",
        "issue_summary": "问题摘要",
        "no_video": "未提供检测视频。",
        "video_note": "视频已嵌入单页报告，可离线交付。",
        "video_link_note": "视频过大或压缩不可用，报告保留相对链接。",
        "generated": "生成时间",
    },
    "en": {
        "title": "SimReady Asset Conversion Report",
        "subtitle": "From ordinary mesh to validated SimReady asset",
        "value": "Outcome",
        "value_copy": "The input started as a visual mesh. The workflow normalized physical size, corrected orientation, authored collision and physics metadata, and validated the result downstream so the asset can enter simulation, robotics, and synthetic-data pipelines as a SimReady static asset.",
        "workflow": "What The Workflow Did",
        "validation": "Tests And Validation",
        "flywheel": "Data Flywheel",
        "evidence": "Validation Video Evidence",
        "asset": "Asset",
        "source": "Input mesh",
        "output": "Output SimReady USD",
        "status": "Status",
        "passed": "Passed",
        "warning": "Attention",
        "failed": "Failed",
        "blocked": "Blocked",
        "unknown": "Unknown",
        "metric_stage": "Stage",
        "metric_scale": "Scale",
        "metric_orientation": "Orientation",
        "metric_collider": "Collider",
        "metric_physics": "Physics Schema",
        "metric_runtime": "Runtime Validation",
        "ordinary": "Ordinary mesh",
        "simready": "SimReady asset",
        "details": "Evidence",
        "issue_summary": "Issue Summary",
        "no_video": "No validation video was provided.",
        "video_note": "Video is embedded into the single-page report for offline delivery.",
        "video_link_note": "Video was too large or compression was unavailable, so the report keeps a relative link.",
        "generated": "Generated",
    },
}


@dataclass
class ReportInputs:
    asset_name: str
    source_usd: Optional[str]
    output_usd: Optional[str]
    recommendation: Dict[str, Any]
    inspection_report: Dict[str, Any]
    omni_validate: Dict[str, Any]
    runtime_summary: Dict[str, Any]
    runtime_report: Dict[str, Any]
    proxy_report: Dict[str, Any]
    video_path: Optional[str]
    compressed_video_path: Optional[str]
    embed_video: bool
    max_embed_bytes: int


def _load_json(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return payload if isinstance(payload, dict) else {}


def _dig(payload: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _fmt_number(value: Any, digits: int = 3) -> str:
    if isinstance(value, (int, float)):
        text = f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
        return text or "0"
    return _escape(value)


def _fmt_size(values: Any) -> str:
    if isinstance(values, list) and len(values) == 3:
        return " x ".join(_fmt_number(v, 2) for v in values)
    return "n/a"


def _status_key(*statuses: Any) -> str:
    lowered = {str(item or "").lower() for item in statuses}
    if lowered & {"passed", "pass", "success", "completed"}:
        return "passed"
    if lowered & {"warning", "warnings"}:
        return "warning"
    if lowered & {"failed", "failure", "error"}:
        return "failed"
    if lowered & {"blocked"}:
        return "blocked"
    return "unknown"


def _overall_status(*statuses: Any) -> str:
    keys = [_status_key(status) for status in statuses]
    if "blocked" in keys:
        return "blocked"
    if "failed" in keys and "passed" in keys:
        return "warning"
    if "failed" in keys:
        return "failed"
    if "warning" in keys:
        return "warning"
    if "passed" in keys:
        return "passed"
    return "unknown"


def _badge(status: str, text: str) -> str:
    return f'<span class="badge {status}">{_escape(text)}</span>'


def _as_list(items: Iterable[str]) -> str:
    return "".join(f"<li>{_escape(item)}</li>" for item in items if item)


def _first_existing(*paths: Optional[str]) -> Optional[str]:
    for path in paths:
        if path and os.path.exists(path):
            return path
    return None


def _compress_video(video_path: Optional[str], output_dir: str, max_width: int, crf: int) -> Optional[str]:
    if not video_path or not os.path.exists(video_path) or not shutil.which("ffmpeg"):
        return None
    source = Path(video_path)
    target = Path(output_dir) / f"{source.stem}.report-compressed.mp4"
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source),
        "-vf",
        f"scale='min({max_width},iw)':-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(crf),
        "-movflags",
        "+faststart",
        "-an",
        str(target),
    ]
    completed = subprocess.run(command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if completed.returncode == 0 and target.exists() and target.stat().st_size > 0:
        return str(target)
    return None


def _video_html(inputs: ReportInputs, labels: Dict[str, str], output_path: str) -> Tuple[str, str]:
    chosen = _first_existing(inputs.compressed_video_path, inputs.video_path)
    if not chosen:
        return f'<div class="empty">{_escape(labels["no_video"])}</div>', ""

    size = os.path.getsize(chosen)
    if inputs.embed_video and size <= inputs.max_embed_bytes:
        with open(chosen, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        return (
            f'<video controls preload="metadata" src="data:video/mp4;base64,{encoded}"></video>',
            labels["video_note"],
        )

    rel = os.path.relpath(chosen, os.path.dirname(os.path.abspath(output_path)) or ".")
    return (
        f'<video controls preload="metadata" src="{_escape(rel)}"></video><p><a href="{_escape(rel)}">{_escape(os.path.basename(chosen))}</a></p>',
        labels["video_link_note"],
    )


def _workflow_items(inputs: ReportInputs, lang: str) -> List[str]:
    rec = inputs.recommendation
    authoring = _dig(rec, "recommendation", "authoring", default={}) or {}
    size_rec = _dig(rec, "recommendation", "size_recommendation", default={}) or {}
    orientation = _dig(rec, "recommendation", "orientation_recommendation", default={}) or {}
    collider = _dig(rec, "recommendation", "collision_plan", default={}) or {}
    proxy_path = inputs.proxy_report.get("proxy_path")
    if lang == "zh":
        items = [
            f"识别资产类别和参考组：{_dig(rec, 'recommendation', 'furniture_class', default='unknown')}，参考组资产数 { _dig(rec, 'recommendation', 'reference_group_asset_count', default='n/a') }。",
            f"将源 bbox {_fmt_size(_dig(rec, 'recommendation', 'size', 'bbox_size'))} cm 归一到目标参考尺寸 {_fmt_size(size_rec.get('reference_target_bbox'))} cm，统一缩放系数 {_fmt_number(authoring.get('suggested_uniform_scale'))}。",
            f"将源 Stage 从 {_dig(rec, 'simready_expectations', 'units', 'source_up_axis', default='n/a')}-up 校正为 {_dig(rec, 'simready_expectations', 'units', 'expected_output_up_axis', default='n/a')}-up，旋转 {orientation.get('axis', 'n/a')} 轴 {orientation.get('degrees', 'n/a')} 度。",
            f"为目标 mesh author 碰撞能力：{collider.get('usd_approximation') or authoring.get('approximation') or 'n/a'}，scope={collider.get('scope') or authoring.get('collider_scope') or 'n/a'}。",
            "补全静态仿真需要的 USD Physics schema，包括 CollisionAPI、可选 MassAPI centerOfMass，以及稳定的 defaultPrim/component 元数据。",
        ]
        if proxy_path:
            items.append(f"针对下游 mesh 缺陷反馈，额外 author 轻量 Physics Proxy Collider：{proxy_path}，视觉 mesh 保持不变。")
    else:
        items = [
            f"Classified the asset and matched reference group: {_dig(rec, 'recommendation', 'furniture_class', default='unknown')}, reference assets={_dig(rec, 'recommendation', 'reference_group_asset_count', default='n/a')}.",
            f"Normalized source bbox {_fmt_size(_dig(rec, 'recommendation', 'size', 'bbox_size'))} cm toward target reference size {_fmt_size(size_rec.get('reference_target_bbox'))} cm with uniform scale {_fmt_number(authoring.get('suggested_uniform_scale'))}.",
            f"Corrected stage orientation from {_dig(rec, 'simready_expectations', 'units', 'source_up_axis', default='n/a')}-up to {_dig(rec, 'simready_expectations', 'units', 'expected_output_up_axis', default='n/a')}-up using {orientation.get('axis', 'n/a')} rotation at {orientation.get('degrees', 'n/a')} degrees.",
            f"Authored collision behavior for target mesh: {collider.get('usd_approximation') or authoring.get('approximation') or 'n/a'}, scope={collider.get('scope') or authoring.get('collider_scope') or 'n/a'}.",
            "Added static simulation metadata, including CollisionAPI, optional MassAPI centerOfMass, and stable defaultPrim/component metadata.",
        ]
        if proxy_path:
            items.append(f"Used downstream mesh-defect feedback to author a lightweight Physics Proxy Collider at {proxy_path}, while preserving the visual mesh.")
    return items


def _flywheel_items(lang: str) -> List[str]:
    if lang == "zh":
        return [
            "usd-simready-inspector 先把普通 mesh 转成候选 SimReady USD，并记录 recommendation、authoring 和 inspection 证据。",
            "omni-asset-cli 在下游按 Stage 1 profile、模板场景、物理命中测试和渲染视频进行验证。",
            "验证失败项会回流成下一轮规则、代理碰撞体、尺寸先验、材质路径或测试模板改进。",
            "每一轮都留下 JSON、MD、runtime report 和视频证据，方便客户审计，也方便持续扩大资产批量处理能力。",
        ]
    return [
        "usd-simready-inspector turns the ordinary mesh into a candidate SimReady USD and records recommendation, authoring, and inspection evidence.",
        "omni-asset-cli validates the result downstream with the Stage 1 profile, template scene checks, physics hit tests, and rendered video evidence.",
        "Failures become feedback for the next iteration: rules, proxy colliders, size priors, material paths, or validation harness changes.",
        "Every iteration leaves JSON, Markdown, runtime reports, and video artifacts for customer auditability and scalable batch improvement.",
    ]


def _metric_cards(inputs: ReportInputs, labels: Dict[str, str]) -> str:
    report = inputs.inspection_report
    rec = inputs.recommendation
    omni = inputs.omni_validate
    runtime = inputs.runtime_summary or inputs.runtime_report
    has_physics = bool(_dig(report, "summary", "has_any_physics", default=False))
    runtime_status = _status_key(runtime.get("result"), runtime.get("status"))
    omni_status = _status_key(omni.get("validation_status"), omni.get("status"), omni.get("execution_status"))
    cards = [
        (labels["metric_stage"], _dig(report, "stage", "up_axis", default="n/a"), _dig(report, "stage", "meters_per_unit", default="n/a"), "passed" if report else "unknown"),
        (labels["metric_scale"], _fmt_number(_dig(rec, "recommendation", "authoring", "suggested_uniform_scale")), _fmt_size(_dig(rec, "simready_expectations", "expected_authored_bbox_size_cm")), "passed" if _dig(rec, "recommendation", "authoring", "apply_reference_scale") else "warning"),
        (labels["metric_orientation"], _dig(report, "stage", "up_axis", default="n/a"), _dig(rec, "recommendation", "orientation_recommendation", "degrees", default="n/a"), "passed" if _dig(rec, "recommendation", "orientation_recommendation", "apply") else "unknown"),
        (labels["metric_collider"], _dig(rec, "recommendation", "collision_plan", "usd_approximation", default=_dig(rec, "recommendation", "authoring", "approximation", default="n/a")), _dig(rec, "recommendation", "collision_plan", "scope", default="n/a"), "passed" if _dig(rec, "recommendation", "collision_plan", "auto_apply_safe") else "warning"),
        (labels["metric_physics"], "present" if has_physics else "missing", f"schemas={len(_dig(report, 'physics', 'physics_schemas_detected', default=[]))}", "passed" if has_physics else "warning"),
        (labels["metric_runtime"], runtime.get("result") or runtime.get("status") or "n/a", f"omni={omni.get('validation_status') or omni.get('status') or 'n/a'}", runtime_status if runtime_status != "unknown" else omni_status),
    ]
    html_cards = []
    for title, primary, secondary, status in cards:
        html_cards.append(
            f'<article class="metric {status}"><div class="metric-title">{_escape(title)}</div>'
            f'<div class="metric-primary">{_escape(primary)}</div><div class="metric-secondary">{_escape(secondary)}</div></article>'
        )
    return "".join(html_cards)


def _issue_rows(payload: Dict[str, Any]) -> str:
    issues = payload.get("issues") or []
    if not issues:
        return '<tr><td colspan="4">No issues</td></tr>'
    rows = []
    for issue in issues[:8]:
        rows.append(
            "<tr>"
            f"<td>{_escape(issue.get('severity'))}</td>"
            f"<td>{_escape(issue.get('rule'))}</td>"
            f"<td>{_escape(issue.get('code'))}</td>"
            f"<td>{_escape(issue.get('message'))}</td>"
            "</tr>"
        )
    return "".join(rows)


def render_html(inputs: ReportInputs, lang: str, output_path: str) -> str:
    labels = LANG[lang]
    report = inputs.inspection_report
    rec = inputs.recommendation
    omni = inputs.omni_validate
    runtime = inputs.runtime_summary or inputs.runtime_report
    asset_id = inputs.asset_name or _dig(rec, "asset", "asset_id", default="asset")
    output_usd = inputs.output_usd or _dig(report, "file", default=_dig(rec, "simready_expectations", "output_usd"))
    source_usd = inputs.source_usd or _dig(rec, "asset", "file", default=_dig(rec, "simready_expectations", "source_usd"))
    final_status = _overall_status(runtime.get("result"), runtime.get("status"), omni.get("validation_status"), omni.get("status"))
    video_markup, video_note = _video_html(inputs, labels, output_path)
    workflow = _as_list(_workflow_items(inputs, lang))
    flywheel = _as_list(_flywheel_items(lang))
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status_text = labels.get(final_status, labels["unknown"])
    issue_count = _dig(omni, "summary", "issue_count", default=0)
    severity_counts = _dig(omni, "summary", "severity_counts", default={}) or {}
    runtime_checks = runtime.get("checks") or {}
    check_list = _as_list(f"{key}: {value}" for key, value in runtime_checks.items())

    return f"""<!doctype html>
<html lang="{lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(labels['title'])} - {_escape(asset_id)}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink:#172026; --muted:#5b6670; --line:#d8dee5; --bg:#f7f9fb; --panel:#ffffff;
      --green:#0f7b4f; --amber:#a45f00; --red:#b42318; --blue:#1d5f99;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Inter, "Segoe UI", Arial, sans-serif; color:var(--ink); background:var(--bg); }}
    header {{ padding:44px 48px 24px; background:#ffffff; border-bottom:1px solid var(--line); }}
    main {{ max-width:1180px; margin:0 auto; padding:28px 24px 48px; }}
    h1 {{ margin:0; font-size:34px; line-height:1.15; letter-spacing:0; }}
    h2 {{ margin:0 0 16px; font-size:22px; letter-spacing:0; }}
    h3 {{ margin:0 0 8px; font-size:16px; letter-spacing:0; }}
    p {{ line-height:1.58; color:var(--muted); }}
    .sub {{ margin:8px 0 0; font-size:18px; color:var(--muted); }}
    .topline {{ display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom:14px; }}
    .badge {{ display:inline-flex; align-items:center; border-radius:999px; padding:5px 10px; font-size:13px; font-weight:700; }}
    .passed {{ color:var(--green); background:#e7f5ee; }} .warning {{ color:var(--amber); background:#fff3dd; }}
    .failed,.blocked {{ color:var(--red); background:#ffebe8; }} .unknown {{ color:var(--blue); background:#eaf3fb; }}
    .grid {{ display:grid; grid-template-columns:repeat(12,1fr); gap:18px; }}
    section {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:22px; margin-bottom:18px; }}
    .span-7 {{ grid-column:span 7; }} .span-5 {{ grid-column:span 5; }} .span-12 {{ grid-column:span 12; }}
    .pathrow {{ display:grid; grid-template-columns:1fr auto 1fr; gap:14px; align-items:center; margin-top:18px; }}
    .pathbox {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fbfcfd; min-width:0; }}
    .pathbox strong {{ display:block; margin-bottom:6px; }} .pathbox code {{ word-break:break-all; color:var(--muted); }}
    .arrow {{ font-weight:800; color:var(--blue); }}
    .metrics {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
    .metric {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fbfcfd; }}
    .metric-title {{ font-size:13px; color:var(--muted); margin-bottom:8px; }}
    .metric-primary {{ font-size:20px; font-weight:800; word-break:break-word; }}
    .metric-secondary {{ font-size:13px; color:var(--muted); margin-top:6px; word-break:break-word; }}
    ul {{ margin:0; padding-left:20px; }} li {{ margin:9px 0; line-height:1.45; }}
    video {{ width:100%; max-height:520px; background:#0b1117; border-radius:8px; display:block; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }} th,td {{ text-align:left; border-bottom:1px solid var(--line); padding:10px; vertical-align:top; }}
    th {{ color:var(--muted); font-weight:700; }} .empty {{ color:var(--muted); padding:22px; border:1px dashed var(--line); border-radius:8px; }}
    .foot {{ text-align:right; color:var(--muted); font-size:12px; margin-top:12px; }}
    @media (max-width: 820px) {{ header {{ padding:28px 22px 18px; }} h1 {{ font-size:28px; }} .span-7,.span-5,.span-12 {{ grid-column:span 12; }} .metrics {{ grid-template-columns:1fr; }} .pathrow {{ grid-template-columns:1fr; }} .arrow {{ display:none; }} }}
  </style>
</head>
<body>
  <header>
    <div class="topline">{_badge(final_status, status_text)} <span>{_escape(labels['asset'])}: <strong>{_escape(asset_id)}</strong></span></div>
    <h1>{_escape(labels['title'])}</h1>
    <p class="sub">{_escape(labels['subtitle'])}</p>
  </header>
  <main>
    <div class="grid">
      <section class="span-7">
        <h2>{_escape(labels['value'])}</h2>
        <p>{_escape(labels['value_copy'])}</p>
        <div class="pathrow">
          <div class="pathbox"><strong>{_escape(labels['ordinary'])}</strong><code>{_escape(source_usd or 'n/a')}</code></div>
          <div class="arrow">-&gt;</div>
          <div class="pathbox"><strong>{_escape(labels['simready'])}</strong><code>{_escape(output_usd or 'n/a')}</code></div>
        </div>
      </section>
      <section class="span-5">
        <h2>{_escape(labels['details'])}</h2>
        <div class="metrics">{_metric_cards(inputs, labels)}</div>
      </section>
      <section class="span-7">
        <h2>{_escape(labels['workflow'])}</h2>
        <ul>{workflow}</ul>
      </section>
      <section class="span-5">
        <h2>{_escape(labels['validation'])}</h2>
        <ul>
          <li>omni-asset-cli: {labels.get(_status_key(omni.get('validation_status'), omni.get('status')), labels['unknown'])}; issue_count={_escape(issue_count)}; severity={_escape(severity_counts)}</li>
          <li>runtime: {labels.get(_status_key(runtime.get('result'), runtime.get('status')), labels['unknown'])}; frames={_escape(runtime.get('frames') or _dig(inputs.runtime_report, 'frames', default='n/a'))}</li>
          {check_list}
        </ul>
      </section>
      <section class="span-12">
        <h2>{_escape(labels['evidence'])}</h2>
        {video_markup}
        <p>{_escape(video_note)}</p>
      </section>
      <section class="span-7">
        <h2>{_escape(labels['flywheel'])}</h2>
        <ul>{flywheel}</ul>
      </section>
      <section class="span-5">
        <h2>{_escape(labels['issue_summary'])}</h2>
        <table>
          <thead><tr><th>Severity</th><th>Rule</th><th>Code</th><th>Message</th></tr></thead>
          <tbody>{_issue_rows(omni)}</tbody>
        </table>
      </section>
    </div>
    <div class="foot">{_escape(labels['generated'])}: {_escape(generated)}</div>
  </main>
</body>
</html>
"""


def build_report_inputs(args: argparse.Namespace, output_dir: str) -> ReportInputs:
    recommendation = _load_json(args.recommendation)
    inspection = _load_json(args.report)
    omni = _load_json(args.omni_validate)
    runtime_summary = _load_json(args.runtime_summary)
    runtime_report = _load_json(args.runtime_report)
    proxy_report = _load_json(args.proxy_report)
    compressed = args.compressed_video
    if args.video and args.compress_video and not compressed:
        compressed = _compress_video(args.video, output_dir, args.video_max_width, args.video_crf)
    max_embed_bytes = int(float(args.max_embed_mb) * 1024 * 1024)
    return ReportInputs(
        asset_name=args.asset_name or _dig(recommendation, "asset", "asset_id", default="asset"),
        source_usd=args.source_usd,
        output_usd=args.output_usd,
        recommendation=recommendation,
        inspection_report=inspection,
        omni_validate=omni,
        runtime_summary=runtime_summary,
        runtime_report=runtime_report,
        proxy_report=proxy_report,
        video_path=args.video,
        compressed_video_path=compressed,
        embed_video=not args.no_embed_video,
        max_embed_bytes=max_embed_bytes,
    )


def generate_reports(args: argparse.Namespace) -> List[str]:
    outputs: List[Tuple[str, str]] = []
    if args.output_zh:
        outputs.append(("zh", args.output_zh))
    if args.output_en:
        outputs.append(("en", args.output_en))
    if not outputs:
        base = args.output_base or "simready_customer_report"
        outputs = [("zh", f"{base}.zh.html"), ("en", f"{base}.en.html")]

    output_dir = os.path.dirname(os.path.abspath(outputs[0][1])) or "."
    os.makedirs(output_dir, exist_ok=True)
    inputs = build_report_inputs(args, output_dir)

    written: List[str] = []
    for lang, output_path in outputs:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
        html_text = render_html(inputs, lang, output_path)
        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write(html_text)
        written.append(output_path)
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate bilingual customer-facing SimReady workflow HTML reports.")
    parser.add_argument("--asset-name")
    parser.add_argument("--source-usd")
    parser.add_argument("--output-usd")
    parser.add_argument("--recommendation", required=True)
    parser.add_argument("--report", required=True, help="usd-simready-inspector inspection report JSON")
    parser.add_argument("--omni-validate", help="omni-asset-cli validate JSON")
    parser.add_argument("--runtime-summary", help="Downstream runtime summary.json")
    parser.add_argument("--runtime-report", help="Downstream runtime_report.json")
    parser.add_argument("--proxy-report", help="Optional proxy collider / mesh repair report JSON")
    parser.add_argument("--video", help="Validation video path, usually compressed mp4")
    parser.add_argument("--compressed-video", help="Pre-compressed validation video path")
    parser.add_argument("--compress-video", action="store_true", help="Compress --video with ffmpeg before embedding/linking")
    parser.add_argument("--video-max-width", type=int, default=960)
    parser.add_argument("--video-crf", type=int, default=32)
    parser.add_argument("--max-embed-mb", type=float, default=8.0)
    parser.add_argument("--no-embed-video", action="store_true")
    parser.add_argument("--output-base", help="Base output path when --output-zh/--output-en are omitted")
    parser.add_argument("--output-zh")
    parser.add_argument("--output-en")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    for path in generate_reports(args):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
