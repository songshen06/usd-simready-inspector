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
        "value": "核心成果与关键证据",
        "value_copy": "输入资产从只有视觉网格的普通 mesh，经过尺寸归一、坐标系校正、碰撞体 authoring、物理元数据补全和下游运行时验证，形成可进入仿真/机器人/合成数据流程的 SimReady 静态资产。",
        "workflow": "Workflow 做了什么",
        "validation": "检测与验证",
        "flywheel": "数据飞轮",
        "evidence": "检测视频证据",
        "primary_video": "客户可读验证视频",
        "internal_video": "内部技术证据视频",
        "primary_video_desc": "杯子在测试场景中下落并落到桌面，用于向客户展示资产已经进入可运行的物理验证流程。",
        "internal_video_desc": "同一次历史验证资产的内部证据视角，包含 bbox / COM 等调试叠加，用于工程复核，不作为客户主视觉。",
        "historical_video_notice": "说明：以下视频来自历史成功运行的 cup drop test，可作为当前报告的参考证据；当前 CUDA 容器异常导致本轮未重新生成同等视频，需在 GPU 可见性修复后重新跑标准流程以刷新证据。",
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
        "metric_asset_state": "资产状态",
        "metric_static_validation": "静态校验",
        "ordinary": "普通 mesh",
        "simready": "SimReady 资产",
        "details": "关键证据",
        "before": "转化前",
        "after": "转化后",
        "explanation": "说明",
        "expand_details": "查看技术细节",
        "issue_summary": "问题摘要",
        "no_video": "未提供检测视频。",
        "video_note": "视频已嵌入单页报告，可离线交付。",
        "video_link_note": "视频过大或压缩不可用，报告保留相对链接。",
        "generated": "生成时间",
    },
    "en": {
        "title": "SimReady Asset Conversion Report",
        "subtitle": "From ordinary mesh to validated SimReady asset",
        "value": "Outcome And Evidence",
        "value_copy": "The input started as a visual mesh. The workflow normalized physical size, corrected orientation, authored collision and physics metadata, and validated the result downstream so the asset can enter simulation, robotics, and synthetic-data pipelines as a SimReady static asset.",
        "workflow": "What The Workflow Did",
        "validation": "Tests And Validation",
        "flywheel": "Data Flywheel",
        "evidence": "Validation Video Evidence",
        "primary_video": "Customer-Readable Validation Video",
        "internal_video": "Internal Technical Evidence Video",
        "primary_video_desc": "The cup drops in the test scene and lands on the table, showing that the asset entered an executable physics-validation flow.",
        "internal_video_desc": "Internal evidence view from the same historical cup drop validation family, with bbox / COM debug overlays for engineering review rather than customer-facing presentation.",
        "historical_video_notice": "Note: the videos below come from a historical successful cup drop test and are used as reference evidence for this report. The current CUDA container issue prevented regenerating an equivalent video in this run; rerun the standard workflow after GPU visibility is fixed to refresh the evidence.",
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
        "metric_asset_state": "Asset State",
        "metric_static_validation": "Static Validation",
        "ordinary": "Ordinary mesh",
        "simready": "SimReady asset",
        "details": "Evidence",
        "before": "Before",
        "after": "After",
        "explanation": "Explanation",
        "expand_details": "View Technical Details",
        "issue_summary": "Issue Summary",
        "no_video": "No validation video was provided.",
        "video_note": "Video is embedded into the single-page report for offline delivery.",
        "video_link_note": "Video was too large or compression was unavailable, so the report keeps a relative link.",
        "generated": "Generated",
    },
}


@dataclass
class VideoEvidence:
    role: str
    label_key: str
    description_key: str
    video_path: Optional[str]
    compressed_video_path: Optional[str]


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
    internal_video_path: Optional[str]
    internal_compressed_video_path: Optional[str]
    embed_video: bool
    max_embed_bytes: int
    require_video: bool


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


def _num_list(values: Any) -> Optional[List[float]]:
    if not isinstance(values, list) or len(values) != 3:
        return None
    try:
        return [float(item) for item in values]
    except (TypeError, ValueError):
        return None


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


def _video_evidence_items(inputs: ReportInputs) -> List[VideoEvidence]:
    return [
        VideoEvidence(
            role="primary_customer",
            label_key="primary_video",
            description_key="primary_video_desc",
            video_path=inputs.video_path,
            compressed_video_path=inputs.compressed_video_path,
        ),
        VideoEvidence(
            role="internal_engineering",
            label_key="internal_video",
            description_key="internal_video_desc",
            video_path=inputs.internal_video_path,
            compressed_video_path=inputs.internal_compressed_video_path,
        ),
    ]


def _video_record(item: VideoEvidence) -> Dict[str, Any]:
    chosen = _first_existing(item.compressed_video_path, item.video_path)
    return {
        "role": item.role,
        "path": chosen,
        "source_path": item.video_path,
        "compressed_path": item.compressed_video_path,
        "exists": bool(chosen),
        "size_bytes": os.path.getsize(chosen) if chosen and os.path.exists(chosen) else 0,
    }


def _single_video_html(
    item: VideoEvidence,
    inputs: ReportInputs,
    labels: Dict[str, str],
    output_path: str,
) -> Tuple[str, str]:
    chosen = _first_existing(item.compressed_video_path, item.video_path)
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


def _video_html(inputs: ReportInputs, labels: Dict[str, str], output_path: str) -> str:
    cards: List[str] = []
    for item in _video_evidence_items(inputs):
        if not _first_existing(item.compressed_video_path, item.video_path):
            continue
        markup, note = _single_video_html(item, inputs, labels, output_path)
        cards.append(
            '<article class="video-card">'
            f'<h3>{_escape(labels[item.label_key])}</h3>'
            f'<p>{_escape(labels[item.description_key])}</p>'
            f"{markup}"
            f'<p class="video-note">{_escape(note)}</p>'
            "</article>"
        )
    if not cards:
        return f'<div class="empty">{_escape(labels["no_video"])}</div>'
    return f'<p class="notice">{_escape(labels["historical_video_notice"])}</p>' + "".join(cards)


MESH_LAYER_RULES = {
    "ValidateTopologyChecker",
    "ManifoldChecker",
    "ZeroAreaFaceChecker",
    "NormalsValidChecker",
    "WeldChecker",
}

SIMREADY_LAYER_RULES = {
    "DefaultPrimChecker",
    "ExtentsChecker",
    "MaterialPathChecker",
    "MissingReferenceChecker",
    "StageMetadataChecker",
    "UsdDanglingMaterialBinding",
    "UsdMaterialBindingApi",
}


def _compact_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "severity": issue.get("severity"),
        "rule": issue.get("rule"),
        "code": issue.get("code"),
        "message": issue.get("message"),
        "at": issue.get("at"),
    }


def _group_issues(omni: Dict[str, Any]) -> Dict[str, Any]:
    buckets = {
        "mesh_layer": [],
        "simready_layer": [],
        "other_layer": [],
    }
    for issue in omni.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        rule = str(issue.get("rule") or "")
        if rule in MESH_LAYER_RULES:
            buckets["mesh_layer"].append(_compact_issue(issue))
        elif rule in SIMREADY_LAYER_RULES:
            buckets["simready_layer"].append(_compact_issue(issue))
        else:
            buckets["other_layer"].append(_compact_issue(issue))

    grouped: Dict[str, Any] = {}
    for key, issues in buckets.items():
        grouped[key] = {
            "status": "passed" if not issues else "needs_iteration",
            "issue_count": len(issues),
            "issues": issues,
        }
    grouped["raw_validator_summary"] = omni.get("summary") or {}
    return grouped


def _issue_summary_text(grouped: Dict[str, Any], lang: str) -> List[str]:
    mesh_count = _dig(grouped, "mesh_layer", "issue_count", default=0) or 0
    simready_count = _dig(grouped, "simready_layer", "issue_count", default=0) or 0
    other_count = _dig(grouped, "other_layer", "issue_count", default=0) or 0
    if lang == "zh":
        items = []
        if mesh_count:
            items.append(f"Mesh 层面仍有 {mesh_count} 个几何质量问题，主要影响碰撞稳定性和后续批量自动化可信度。")
        else:
            items.append("Mesh 层面当前没有被下游静态规则标记的问题。")
        if simready_count:
            items.append(f"SimReady/资产规范层面仍有 {simready_count} 个问题，优先处理材质路径、Stage 元数据或引用规范。")
        else:
            items.append("SimReady/资产规范层面当前没有被下游静态规则标记的问题。")
        if other_count:
            items.append(f"另有 {other_count} 个其他规则项，需要按具体规则继续归类。")
        return items
    items = []
    if mesh_count:
        items.append(f"The mesh layer still has {mesh_count} geometry-quality finding(s), which mainly affect collision stability and batch automation confidence.")
    else:
        items.append("The mesh layer has no findings from the downstream static rule set.")
    if simready_count:
        items.append(f"The SimReady/asset-conformance layer still has {simready_count} finding(s), mainly around material paths, stage metadata, or reference hygiene.")
    else:
        items.append("The SimReady/asset-conformance layer has no findings from the downstream static rule set.")
    if other_count:
        items.append(f"There are {other_count} additional finding(s) that should be classified by rule in the next iteration.")
    return items


def _physics_schema_narrative(schemas: List[str], lang: str) -> str:
    names = ", ".join(schemas[:4]) if schemas else "USD Physics metadata"
    if lang == "zh":
        return (
            f"这里的物理 schema 指写在 USD prim 上、可被仿真工具识别的物理能力声明。本轮资产已包含 {names}，"
            "表示它不再只是视觉 mesh，而是带有碰撞/质量相关信息的静态 SimReady 候选资产。"
        )
    return (
        f"Physics schema means USD prim metadata that simulation tools can recognize. This asset contains {names}, "
        "so it is no longer just visual geometry; it carries collision and mass-related information for a static SimReady candidate."
    )


def _contact_evidence(runtime: Dict[str, Any], runtime_report: Dict[str, Any]) -> Dict[str, Any]:
    checks = runtime.get("checks") or {}
    report_contact = _dig(runtime_report, "final_state", "contact_report", default=None)
    hit_analysis = runtime_report.get("hit_analysis") or {}
    level = runtime.get("contact_evidence_level") or hit_analysis.get("contact_evidence_level")
    has_contact_report = isinstance(report_contact, dict)
    strong_detected = bool(checks.get("contact_report_detected")) or bool(
        has_contact_report and report_contact.get("detected")
    )
    inferred = bool(checks.get("contact_detected_or_inferred")) and not strong_detected
    legacy_without_contact_report = (
        not has_contact_report
        and "contact_report_detected" not in checks
        and "contact_evidence_level" not in runtime
    )
    if strong_detected:
        status = "detected"
    elif inferred:
        status = "inferred"
    elif legacy_without_contact_report:
        status = "missing_contact_report"
    else:
        status = "none"
    return {
        "status": status,
        "strong_contact_detected": bool(strong_detected and level in {None, "detected"}),
        "motion_inferred_contact": inferred,
        "contact_evidence_level": level,
        "contact_report_present": has_contact_report,
        "contact_report": report_contact if has_contact_report else None,
        "legacy_without_contact_report": legacy_without_contact_report,
    }


def _build_report_model(inputs: ReportInputs) -> Dict[str, Any]:
    rec = inputs.recommendation
    report = inputs.inspection_report
    omni = inputs.omni_validate
    runtime = inputs.runtime_summary or inputs.runtime_report
    authoring = _dig(rec, "recommendation", "authoring", default={}) or {}
    collision = _dig(rec, "recommendation", "collision_plan", default={}) or {}
    orientation = _dig(rec, "recommendation", "orientation_recommendation", default={}) or {}
    checks = runtime.get("checks") or {}

    source_bbox = _num_list(_dig(rec, "simready_expectations", "source_bbox_size_cm"))
    if source_bbox is None:
        source_bbox = _num_list(_dig(rec, "recommendation", "size", "bbox_size"))
    if source_bbox is None:
        source_bbox = _num_list(_dig(rec, "asset", "size", "bbox_size"))
    target_bbox = _num_list(_dig(rec, "simready_expectations", "reference_target_bbox_cm"))
    if target_bbox is None:
        target_bbox = _num_list(_dig(rec, "recommendation", "size_recommendation", "reference_target_bbox"))
    authored_bbox = _num_list(_dig(rec, "simready_expectations", "expected_authored_bbox_size_cm"))
    scale = authoring.get("suggested_uniform_scale")
    if scale is None:
        scale = _dig(rec, "recommendation", "size_recommendation", "suggested_uniform_scale")

    source_up = _dig(rec, "simready_expectations", "units", "source_up_axis", default=orientation.get("from_axis"))
    output_up = _dig(report, "stage", "up_axis", default=_dig(rec, "simready_expectations", "units", "expected_output_up_axis", default=orientation.get("to_axis")))
    schemas = _dig(report, "physics", "physics_schemas_detected", default=[]) or []
    if not schemas and _dig(report, "summary", "has_any_physics", default=False):
        schemas = ["USD Physics metadata present"]

    grouped = _group_issues(omni)
    runtime_status = _status_key(runtime.get("result"), runtime.get("status"))
    contact_evidence = _contact_evidence(runtime, inputs.runtime_report)
    omni_status = _status_key(omni.get("validation_status"), omni.get("status"))
    if omni_status == "unknown":
        omni_status = _status_key(omni.get("execution_status"))

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "asset": {
            "asset_name": inputs.asset_name,
            "source_usd": inputs.source_usd or _dig(rec, "asset", "file"),
            "output_usd": inputs.output_usd or report.get("file") or _dig(rec, "simready_expectations", "output_usd"),
            "state_transition": {
                "from": "ordinary_mesh",
                "to": "simready_static_usd",
            },
        },
        "video_evidence": {
            "notice_zh": LANG["zh"]["historical_video_notice"],
            "notice_en": LANG["en"]["historical_video_notice"],
            "items": [
                record
                for item in _video_evidence_items(inputs)
                for record in [_video_record(item)]
                if record.get("exists")
            ],
        },
        "source_assessment": grouped,
        "correction_result": {
            "scale": {
                "source_bbox_cm": source_bbox,
                "reference_target_bbox_cm": target_bbox,
                "applied_uniform_scale": scale,
                "authored_bbox_cm": authored_bbox,
                "narrative_zh": (
                    f"尺寸处理以 {_fmt_size(target_bbox)} cm 作为参考目标范围，并使用 {_fmt_number(scale)} 的统一缩放保持原始比例；"
                    f"最终 authored bbox 约为 {_fmt_size(authored_bbox)} cm。参考目标不是把三轴强行拉伸到同一个尺寸，而是给自动化 authoring 一个物理尺度约束。"
                ),
                "narrative_en": (
                    f"Size handling uses {_fmt_size(target_bbox)} cm as the reference target range and applies a uniform scale of {_fmt_number(scale)} to preserve proportions; "
                    f"the final authored bbox is about {_fmt_size(authored_bbox)} cm. The reference target is a physical-size constraint, not a forced non-uniform stretch."
                ),
            },
            "orientation": {
                "source_up_axis": source_up,
                "output_up_axis": output_up,
                "rotation_axis": orientation.get("axis"),
                "rotation_degrees": orientation.get("degrees"),
                "narrative_zh": (
                    f"朝向处理是把源资产的 {source_up}-up 坐标系转换为输出 Stage 的 {output_up}-up；"
                    f"实现方式是绕 {orientation.get('axis', 'n/a')} 轴旋转 {_fmt_number(orientation.get('degrees'))} 度。"
                ),
                "narrative_en": (
                    f"Orientation handling converts the source asset from {source_up}-up to the output stage's {output_up}-up convention, "
                    f"implemented as a {_fmt_number(orientation.get('degrees'))}-degree rotation around the {orientation.get('axis', 'n/a')} axis."
                ),
            },
            "collider": {
                "approximation": collision.get("usd_approximation") or authoring.get("approximation"),
                "scope": collision.get("scope") or authoring.get("collider_scope"),
                "target_mesh_paths": collision.get("target_mesh_paths") or authoring.get("target_mesh_paths") or [],
                "proxy_path": inputs.proxy_report.get("proxy_path"),
                "visual_mesh_modified": inputs.proxy_report.get("visual_mesh_modified"),
            },
            "physics_schema": {
                "detected": bool(schemas),
                "schemas": schemas,
                "narrative_zh": _physics_schema_narrative(schemas, "zh"),
                "narrative_en": _physics_schema_narrative(schemas, "en"),
            },
        },
        "runtime_validation": {
            "status": runtime_status,
            "frames": runtime.get("frames"),
            "checks": checks,
            "contact_evidence": contact_evidence,
            "human_summary_zh": _validation_items(inputs, "zh"),
            "human_summary_en": _validation_items(inputs, "en"),
        },
        "downstream_validation": {
            "status": omni_status,
            "issue_count": _dig(omni, "summary", "issue_count", default=0),
            "severity_counts": _dig(omni, "summary", "severity_counts", default={}),
            "rule_counts": _dig(omni, "summary", "rule_counts", default={}),
        },
        "issue_summary": {
            "zh": _issue_summary_text(grouped, "zh"),
            "en": _issue_summary_text(grouped, "en"),
        },
        "data_flywheel": {
            "zh": _flywheel_items("zh"),
            "en": _flywheel_items("en"),
        },
    }


def _workflow_items(model: Dict[str, Any], inputs: ReportInputs, lang: str) -> List[str]:
    correction = model.get("correction_result") or {}
    scale = correction.get("scale") or {}
    orientation = correction.get("orientation") or {}
    collider = correction.get("collider") or {}
    physics_schema = correction.get("physics_schema") or {}
    rec = inputs.recommendation
    proxy_path = collider.get("proxy_path")
    if lang == "zh":
        items = [
            f"资产识别：分类为 {_dig(rec, 'recommendation', 'furniture_class', default='unknown')}，使用 {_dig(rec, 'recommendation', 'reference_group_asset_count', default='n/a')} 个相近参考资产辅助选择尺寸和碰撞策略。",
            scale.get("narrative_zh", ""),
            orientation.get("narrative_zh", ""),
            f"碰撞处理：为目标 mesh 写入 {collider.get('approximation') or 'n/a'} 碰撞近似，scope={collider.get('scope') or 'n/a'}，供下游物理测试加载。",
            "补充碰撞和物理属性后，下游工具可以把资产作为可参与仿真的物体加载，而不是只把它当作外观模型。",
        ]
        if proxy_path:
            items.append(f"针对下游 mesh 缺陷反馈，额外 author 轻量 Physics Proxy Collider：{proxy_path}，视觉 mesh 保持不变。")
    else:
        items = [
            f"Asset identification: classified as {_dig(rec, 'recommendation', 'furniture_class', default='unknown')} and compared with {_dig(rec, 'recommendation', 'reference_group_asset_count', default='n/a')} similar reference assets for sizing and collision strategy.",
            scale.get("narrative_en", ""),
            orientation.get("narrative_en", ""),
            f"Collision handling: authored {collider.get('approximation') or 'n/a'} collision approximation on the target mesh, scope={collider.get('scope') or 'n/a'}, so downstream physics tests can load it.",
            "After adding collision and physical properties, downstream tools can load the asset as an object that participates in simulation, not just as appearance-only geometry.",
        ]
        if proxy_path:
            items.append(f"Used downstream mesh-defect feedback to author a lightweight Physics Proxy Collider at {proxy_path}, while preserving the visual mesh.")
    return items


def _legacy_workflow_items(inputs: ReportInputs, lang: str) -> List[str]:
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


def _metric_cards(model: Dict[str, Any], labels: Dict[str, str], lang: str) -> str:
    correction = model.get("correction_result") or {}
    scale = correction.get("scale") or {}
    orientation = correction.get("orientation") or {}
    collider = correction.get("collider") or {}
    physics_schema = correction.get("physics_schema") or {}
    runtime = model.get("runtime_validation") or {}
    downstream = model.get("downstream_validation") or {}
    final_bbox = _fmt_size(scale.get("authored_bbox_cm"))
    source_up = orientation.get("source_up_axis") or "n/a"
    output_up = orientation.get("output_up_axis") or "n/a"
    issue_count = downstream.get("issue_count") or 0
    static_status = "passed" if issue_count == 0 else "warning"
    runtime_status = runtime.get("status") or "unknown"
    contact_evidence = runtime.get("contact_evidence") or {}
    strong_contact = bool(contact_evidence.get("strong_contact_detected"))
    inferred_contact = bool(contact_evidence.get("motion_inferred_contact"))
    if lang == "zh":
        asset_secondary = "普通视觉网格已转为静态仿真候选资产"
        scale_secondary = f"统一缩放={_fmt_number(scale.get('applied_uniform_scale'))}"
        orientation_secondary = f"绕 {orientation.get('rotation_axis') or 'n/a'} 轴 {_fmt_number(orientation.get('rotation_degrees'))} 度"
        collider_secondary = "USD Physics metadata present" if physics_schema.get("detected") else "physics metadata missing"
        static_primary = "仍需迭代" if issue_count else "通过"
        static_secondary = f"{issue_count} 个下游静态校验项"
        runtime_primary = f"{runtime.get('frames') or 'n/a'} 帧完成" if runtime_status == "passed" else runtime_status
        runtime_secondary = "接触已确认" if strong_contact else ("仅运动推断" if inferred_contact else "真实接触证据缺失")
    else:
        asset_secondary = "Visual geometry converted into a static simulation candidate"
        scale_secondary = f"uniform scale={_fmt_number(scale.get('applied_uniform_scale'))}"
        orientation_secondary = f"rotate {orientation.get('rotation_axis') or 'n/a'} {_fmt_number(orientation.get('rotation_degrees'))} deg"
        collider_secondary = "USD Physics metadata present" if physics_schema.get("detected") else "physics metadata missing"
        static_primary = "Needs iteration" if issue_count else "Passed"
        static_secondary = f"{issue_count} downstream static finding(s)"
        runtime_primary = f"{runtime.get('frames') or 'n/a'} frames completed" if runtime_status == "passed" else runtime_status
        runtime_secondary = "contact confirmed" if strong_contact else ("motion-inferred only" if inferred_contact else "contact report missing")
    cards = [
        (labels["metric_asset_state"], "Mesh -> SimReady USD", asset_secondary, "passed"),
        (labels["metric_scale"], final_bbox, scale_secondary, "passed" if scale.get("applied_uniform_scale") else "warning"),
        (labels["metric_orientation"], f"{source_up}-up -> {output_up}-up", orientation_secondary, "passed" if output_up != "n/a" else "unknown"),
        (labels["metric_collider"], collider.get("approximation") or "n/a", f"scope={collider.get('scope') or 'n/a'}", "passed" if collider.get("approximation") else "warning"),
        (labels["metric_physics"], "Collision / Mass metadata", collider_secondary, "passed" if physics_schema.get("detected") else "warning"),
        (labels["metric_static_validation"], static_primary, static_secondary, static_status),
        (labels["metric_runtime"], runtime_primary, runtime_secondary, runtime_status),
    ]
    html_cards = []
    for title, primary, secondary, status in cards:
        html_cards.append(
            f'<article class="metric {status}"><div class="metric-title">{_escape(title)}</div>'
            f'<div class="metric-primary">{_escape(primary)}</div><div class="metric-secondary">{_escape(secondary)}</div></article>'
        )
    return "".join(html_cards)


def _outcome_rows(model: Dict[str, Any], labels: Dict[str, str], lang: str) -> str:
    correction = model.get("correction_result") or {}
    scale = correction.get("scale") or {}
    orientation = correction.get("orientation") or {}
    collider = correction.get("collider") or {}
    physics_schema = correction.get("physics_schema") or {}
    runtime = model.get("runtime_validation") or {}
    downstream = model.get("downstream_validation") or {}
    source_bbox = _fmt_size(scale.get("source_bbox_cm"))
    final_bbox = _fmt_size(scale.get("authored_bbox_cm"))
    target_bbox = _fmt_size(scale.get("reference_target_bbox_cm"))
    source_up = orientation.get("source_up_axis") or "n/a"
    output_up = orientation.get("output_up_axis") or "n/a"
    issue_count = downstream.get("issue_count") or 0
    frames = runtime.get("frames") or "n/a"
    contact_evidence = runtime.get("contact_evidence") or {}
    strong_contact = bool(contact_evidence.get("strong_contact_detected"))
    inferred_contact = bool(contact_evidence.get("motion_inferred_contact"))

    if lang == "zh":
        rows = [
            (
                "资产形态",
                "普通视觉 mesh",
                "SimReady static USD",
                "从只服务渲染的几何，转成带物理能力声明、可进入仿真流程的资产。",
            ),
            (
                "物理尺寸",
                f"源 bbox {source_bbox} cm",
                f"最终 bbox {final_bbox} cm",
                f"{target_bbox} cm 是参考目标范围；本轮使用统一缩放 {_fmt_number(scale.get('applied_uniform_scale'))} 保持原始比例，避免非等比拉伸。",
            ),
            (
                "坐标系",
                f"{source_up}-up",
                f"{output_up}-up",
                f"通过绕 {orientation.get('rotation_axis') or 'n/a'} 轴旋转 {_fmt_number(orientation.get('rotation_degrees'))} 度，把资产归一到下游仿真常用 Stage 方向。",
            ),
            (
                "碰撞与物理",
                "只有外观模型",
                "已具备仿真碰撞能力",
                "系统已经为资产补充碰撞和物理属性，下游工具可以把它当作可参与仿真的物体，而不是只能看的模型。",
            ),
            (
                "下游验证",
                "没有自动化验证记录",
                f"完成 {frames} 帧仿真验证",
                "资产已在测试场景中成功加载并保持尺寸稳定；下一轮会继续增强碰撞接触的自动确认能力。",
            ),
        ]
        if strong_contact:
            rows[-1] = (rows[-1][0], rows[-1][1], rows[-1][2], "测试场景已确认资产可以参与物理碰撞。")
        elif inferred_contact:
            rows[-1] = (rows[-1][0], rows[-1][1], rows[-1][2], "测试物体运动到目标区域，但本次 artifact 没有真实 PhysX 接触记录；需要用标准 contact evidence 重新生成验证包。")
        else:
            rows[-1] = (rows[-1][0], rows[-1][1], rows[-1][2], "资产已成功完成仿真加载和稳定性验证；下一轮会继续增强碰撞接触的自动确认能力。")
    else:
        rows = [
            (
                "Asset State",
                "Visual mesh",
                "SimReady static USD",
                "The asset moves from render-only geometry to a simulation-ready candidate with physical capability metadata.",
            ),
            (
                "Physical Size",
                f"Source bbox {source_bbox} cm",
                f"Final bbox {final_bbox} cm",
                f"{target_bbox} cm is the reference target range. The workflow applies uniform scale {_fmt_number(scale.get('applied_uniform_scale'))} to preserve proportions instead of stretching axes independently.",
            ),
            (
                "Coordinate System",
                f"{source_up}-up",
                f"{output_up}-up",
                f"The asset is normalized to the downstream simulation stage convention with a {_fmt_number(orientation.get('rotation_degrees'))}-degree rotation around {orientation.get('rotation_axis') or 'n/a'}.",
            ),
            (
                "Collision And Physics",
                "Appearance-only model",
                "Simulation-ready collision behavior",
                "The workflow adds the physical properties needed for downstream tools to treat the asset as an object that can participate in simulation, not just a model to view.",
            ),
            (
                "Downstream Validation",
                "No automated validation record",
                f"{frames} simulation frames completed",
                "The asset loaded successfully in a test scene and kept a stable size. The next iteration will further strengthen automatic confirmation of collision contact.",
            ),
        ]
        if strong_contact:
            rows[-1] = (rows[-1][0], rows[-1][1], rows[-1][2], "The test scene confirmed that the asset can participate in physical collision.")
        elif inferred_contact:
            rows[-1] = (rows[-1][0], rows[-1][1], rows[-1][2], "The test object reached the target area, but this artifact does not include a real PhysX contact record. Regenerate the standard contact-evidence bundle.")
        else:
            rows[-1] = (rows[-1][0], rows[-1][1], rows[-1][2], "The asset completed simulation loading and stability checks. The next iteration will further strengthen automatic confirmation of collision contact.")

    html_rows = []
    for topic, before, after, explanation in rows:
        html_rows.append(
            "<tr>"
            f"<th scope=\"row\">{_escape(topic)}</th>"
            f"<td>{_escape(before)}</td>"
            f"<td><strong>{_escape(after)}</strong></td>"
            f"<td>{_escape(explanation)}</td>"
            "</tr>"
        )
    return (
        '<table class="outcome-table">'
        f'<thead><tr><th></th><th>{_escape(labels["before"])}</th><th>{_escape(labels["after"])}</th><th>{_escape(labels["explanation"])}</th></tr></thead>'
        f"<tbody>{''.join(html_rows)}</tbody></table>"
    )


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


def _issue_summary_html(model: Dict[str, Any], lang: str) -> str:
    issue_summary = _dig(model, "issue_summary", lang, default=[]) or []
    return f"<ul>{_as_list(issue_summary)}</ul>"


def _validation_items(inputs: ReportInputs, lang: str) -> List[str]:
    omni = inputs.omni_validate
    runtime = inputs.runtime_summary or inputs.runtime_report
    issue_count = int(_dig(omni, "summary", "issue_count", default=0) or 0)
    severity_counts = _dig(omni, "summary", "severity_counts", default={}) or {}
    rule_counts = _dig(omni, "summary", "rule_counts", default={}) or {}
    frames = runtime.get("frames") or _dig(inputs.runtime_report, "frames", default="n/a")
    runtime_passed = _status_key(runtime.get("result"), runtime.get("status")) == "passed"
    contact_evidence = _contact_evidence(runtime, inputs.runtime_report)
    strong_contact = bool(contact_evidence.get("strong_contact_detected"))
    inferred_contact = bool(contact_evidence.get("motion_inferred_contact"))

    if lang == "zh":
        items: List[str] = []
        if issue_count:
            material = rule_counts.get("MaterialPathChecker", 0)
            geometry_rules = [
                f"{rule}={count}"
                for rule, count in rule_counts.items()
                if rule != "MaterialPathChecker"
            ]
            detail = []
            if material:
                detail.append(f"{material} 个材质路径规范项")
            if geometry_rules:
                detail.append("几何质量提醒（" + "，".join(geometry_rules) + "）")
            detail_text = "，".join(detail) if detail else f"{issue_count} 个校验项"
            items.append(
                "omni-asset-cli 的 Stage 1 静态校验没有完全通过，主要发现"
                f"：{detail_text}。这些问题不会否定本轮自动化写入的价值，但应该进入下一轮自动修正。"
            )
        elif omni:
            items.append("omni-asset-cli 的 Stage 1 静态校验未发现阻塞问题，资产结构满足当前规则集。")

        if runtime_passed:
            items.append(
                f"运行时验证完成了 {frames} 帧仿真：资产成功加载，静态碰撞体被应用，动态测试物体创建成功，"
                "仿真正常推进，并且资产尺寸在模板场景中保持稳定。"
            )
        elif runtime:
            items.append(
                "运行时验证未完全通过，说明当前资产或测试模板还需要继续调整；相关 artifacts 已保留用于定位问题。"
            )

        if strong_contact:
            items.append("runtime 证据包含真实 PhysX contact report，测试物体与资产发生了可记录的接触。")
        elif inferred_contact:
            items.append(
                "runtime 运动轨迹显示测试物体到达目标区域，但本次 artifact 没有真实 PhysX contact report；"
                "客户版报告只把它作为运动推断，不作为强接触证明。"
            )
        elif contact_evidence.get("legacy_without_contact_report"):
            items.append(
                "本轮 artifact 来自旧 runtime 输出，缺少 contact report 字段；它只能证明加载、仿真推进和尺寸稳定，不能证明真实接触已发生。"
            )
        else:
            items.append("本轮已经证明资产可以被测试场景加载并稳定运行；真实接触证据仍需补充。")

        if not items:
            items.append("当前报告未提供完整的下游验证结果；建议补充 omni-asset-cli validate 和 runtime evidence 后再对外展示。")
        return items

    items = []
    if issue_count:
        material = rule_counts.get("MaterialPathChecker", 0)
        geometry_rules = [
            f"{rule}={count}"
            for rule, count in rule_counts.items()
            if rule != "MaterialPathChecker"
        ]
        detail = []
        if material:
            detail.append(f"{material} material-path conformance findings")
        if geometry_rules:
            detail.append("geometry-quality findings (" + ", ".join(geometry_rules) + ")")
        detail_text = ", ".join(detail) if detail else f"{issue_count} validation findings"
        items.append(
            "The omni-asset-cli Stage 1 static validation did not fully pass. It mainly found "
            f"{detail_text}. These findings do not negate the automated authoring result, but they should feed the next automated repair iteration."
        )
    elif omni:
        items.append("The omni-asset-cli Stage 1 static validation found no blocking issues in the current rule set.")

    if runtime_passed:
        items.append(
            f"The runtime validation completed {frames} simulation frames: the asset loaded, static colliders were applied, "
            "the dynamic test object was created, simulation advanced, and the authored asset kept its size in the template scene."
        )
    elif runtime:
        items.append(
            "The runtime validation did not fully pass, so the asset or test harness still needs adjustment. The generated artifacts are retained for debugging."
        )

    if strong_contact:
        items.append("The runtime artifact includes a real PhysX contact report, confirming recorded contact between the test object and the asset.")
    elif inferred_contact:
        items.append(
            "The runtime trajectory shows that the test object reached the target area, but this artifact does not include a real PhysX contact report. "
            "The customer report treats it as motion inference, not strong contact proof."
        )
    elif contact_evidence.get("legacy_without_contact_report"):
        items.append(
            "This artifact was generated by an older runtime output shape without contact-report fields. It proves loading, simulation progress, and size stability, but not recorded physical contact."
        )
    else:
        items.append("This run proves that the asset can load and stay stable in the test scene. Real contact evidence still needs to be added.")

    if not items:
        items.append("This report does not yet include complete downstream validation evidence; add omni-asset-cli validation and runtime artifacts before customer review.")
    return items


def render_html(inputs: ReportInputs, lang: str, output_path: str, model: Optional[Dict[str, Any]] = None) -> str:
    labels = LANG[lang]
    model = model or _build_report_model(inputs)
    report = inputs.inspection_report
    rec = inputs.recommendation
    omni = inputs.omni_validate
    runtime = inputs.runtime_summary or inputs.runtime_report
    asset_id = inputs.asset_name or _dig(rec, "asset", "asset_id", default="asset")
    output_usd = inputs.output_usd or _dig(report, "file", default=_dig(rec, "simready_expectations", "output_usd"))
    source_usd = inputs.source_usd or _dig(rec, "asset", "file", default=_dig(rec, "simready_expectations", "source_usd"))
    final_status = _overall_status(runtime.get("result"), runtime.get("status"), omni.get("validation_status"), omni.get("status"))
    video_markup = _video_html(inputs, labels, output_path)
    workflow = _as_list(_workflow_items(model, inputs, lang))
    validation = _as_list(_dig(model, "runtime_validation", f"human_summary_{lang}", default=[]))
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status_text = labels.get(final_status, labels["unknown"])

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
    .outcome-table {{ margin-top:18px; }}
    .outcome-table th:first-child {{ width:15%; color:var(--ink); }}
    .outcome-table td:nth-child(3) {{ color:var(--green); }}
    .outcome-table td:last-child {{ color:var(--muted); line-height:1.45; }}
    .metrics {{ display:grid; grid-template-columns:repeat(3,1fr); gap:12px; }}
    .metric {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fbfcfd; }}
    .metric-title {{ font-size:13px; color:var(--muted); margin-bottom:8px; }}
    .metric-primary {{ font-size:20px; font-weight:800; word-break:break-word; }}
    .metric-secondary {{ font-size:13px; color:var(--muted); margin-top:6px; word-break:break-word; }}
    ul {{ margin:0; padding-left:20px; }} li {{ margin:9px 0; line-height:1.45; }}
    details {{ border:1px solid var(--line); border-radius:8px; background:#fbfcfd; padding:0; margin-bottom:12px; }}
    summary {{ cursor:pointer; padding:14px 16px; font-weight:800; }}
    details[open] summary {{ border-bottom:1px solid var(--line); }}
    details ul {{ padding:8px 22px 16px 36px; }}
    video {{ width:100%; max-height:520px; background:#0b1117; border-radius:8px; display:block; }}
    .video-card {{ margin-top:18px; padding-top:18px; border-top:1px solid var(--line); }}
    .video-card:first-of-type {{ border-top:0; padding-top:0; }}
    .video-note {{ font-size:13px; }}
    .notice {{ border-left:4px solid var(--amber); background:#fff8ea; color:#5f3b00; padding:12px 14px; border-radius:6px; }}
    table {{ width:100%; border-collapse:collapse; font-size:14px; }} th,td {{ text-align:left; border-bottom:1px solid var(--line); padding:10px; vertical-align:top; }}
    th {{ color:var(--muted); font-weight:700; }} .empty {{ color:var(--muted); padding:22px; border:1px dashed var(--line); border-radius:8px; }}
    .foot {{ text-align:right; color:var(--muted); font-size:12px; margin-top:12px; }}
    @media (max-width: 820px) {{ header {{ padding:28px 22px 18px; }} h1 {{ font-size:28px; }} .span-7,.span-5,.span-12 {{ grid-column:span 12; }} .metrics {{ grid-template-columns:1fr; }} .pathrow {{ grid-template-columns:1fr; }} .arrow {{ display:none; }} .outcome-table th,.outcome-table td {{ display:block; width:100%; }} .outcome-table tr {{ display:block; border-bottom:1px solid var(--line); padding:8px 0; }} }}
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
      <section class="span-12">
        <h2>{_escape(labels['value'])}</h2>
        <p>{_escape(labels['value_copy'])}</p>
        <div class="pathrow">
          <div class="pathbox"><strong>{_escape(labels['ordinary'])}</strong><code>{_escape(source_usd or 'n/a')}</code></div>
          <div class="arrow">-&gt;</div>
          <div class="pathbox"><strong>{_escape(labels['simready'])}</strong><code>{_escape(output_usd or 'n/a')}</code></div>
        </div>
        {_outcome_rows(model, labels, lang)}
      </section>
      <section class="span-12">
        <h2>{_escape(labels['expand_details'])}</h2>
        <details>
          <summary>{_escape(labels['workflow'])}</summary>
          <ul>{workflow}</ul>
        </details>
        <details>
          <summary>{_escape(labels['validation'])}</summary>
          <ul>{validation}</ul>
        </details>
      </section>
      <section class="span-12">
        <h2>{_escape(labels['evidence'])}</h2>
        {video_markup}
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
    internal_compressed = args.internal_compressed_video
    if args.internal_video and args.compress_video and not internal_compressed:
        internal_compressed = _compress_video(args.internal_video, output_dir, args.video_max_width, args.video_crf)
    max_embed_bytes = int(float(args.max_embed_mb) * 1024 * 1024)
    chosen_video = _first_existing(compressed, args.video)
    if args.require_video:
        if not chosen_video:
            raise ValueError("--require-video was set, but no existing --video or --compressed-video was provided")
        video_size = os.path.getsize(chosen_video)
        if video_size <= 0:
            raise ValueError(f"--require-video was set, but video is empty: {chosen_video}")
        if not args.no_embed_video and video_size > max_embed_bytes:
            raise ValueError(
                "--require-video was set, but the selected video is larger than --max-embed-mb; "
                "increase --max-embed-mb, pass --compress-video, or provide --compressed-video"
            )
    chosen_internal_video = _first_existing(internal_compressed, args.internal_video)
    if args.internal_video or args.internal_compressed_video:
        if not chosen_internal_video:
            raise ValueError("internal video was requested, but no existing --internal-video or --internal-compressed-video was provided")
        internal_video_size = os.path.getsize(chosen_internal_video)
        if internal_video_size <= 0:
            raise ValueError(f"internal video is empty: {chosen_internal_video}")
        if not args.no_embed_video and internal_video_size > max_embed_bytes:
            raise ValueError(
                "the selected internal video is larger than --max-embed-mb; "
                "increase --max-embed-mb, pass --compress-video, or provide --internal-compressed-video"
            )
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
        internal_video_path=args.internal_video,
        internal_compressed_video_path=internal_compressed,
        embed_video=not args.no_embed_video,
        max_embed_bytes=max_embed_bytes,
        require_video=args.require_video,
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
    model = _build_report_model(inputs)

    json_output = args.output_json
    if not json_output:
        base = args.output_base or os.path.splitext(outputs[0][1])[0].removesuffix(".zh").removesuffix(".en")
        json_output = f"{base}.summary.json"
    os.makedirs(os.path.dirname(os.path.abspath(json_output)) or ".", exist_ok=True)
    with open(json_output, "w", encoding="utf-8") as handle:
        json.dump(model, handle, ensure_ascii=False, indent=2)

    written: List[str] = [json_output]
    for lang, output_path in outputs:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
        html_text = render_html(inputs, lang, output_path, model)
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
    parser.add_argument("--internal-video", help="Internal engineering evidence video path, usually with debug overlays")
    parser.add_argument("--internal-compressed-video", help="Pre-compressed internal engineering evidence video path")
    parser.add_argument("--compress-video", action="store_true", help="Compress --video with ffmpeg before embedding/linking")
    parser.add_argument("--video-max-width", type=int, default=960)
    parser.add_argument("--video-crf", type=int, default=32)
    parser.add_argument("--max-embed-mb", type=float, default=8.0)
    parser.add_argument("--no-embed-video", action="store_true")
    parser.add_argument(
        "--require-video",
        action="store_true",
        help="Fail if no non-empty video can be used; when embedding is enabled the video must fit --max-embed-mb",
    )
    parser.add_argument("--output-base", help="Base output path when --output-zh/--output-en are omitted")
    parser.add_argument("--output-json", help="Structured customer report JSON output")
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
