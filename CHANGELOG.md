# 更新说明

## 2026-05-12

### Isaac Sim Docker Runtime

- `smoke_test_static_furniture_runtime.py` 改为调用 `omni-asset-cli physics-hit-test`，并要求使用 Linux + Isaac Sim Docker。
- 默认命令使用 Stage 1 `top-drop`、`replace-table`、`preserve` 策略，输出 runtime artifacts 目录用于后续诊断。
- 移除对 `static_furniture` 模块的 import 依赖，使 `--help` 和基础阻塞报告不再要求本地已加载 USD/pxr 环境。

### Contact Evidence Diagnosis

- `simready_diagnosis.py` 新增 `runtime_physx_contact_report` 检查。
- 当下游 runtime 没有 `contact_report_detected` 或 `hit_analysis.contact_detected` 时，诊断会记录 `runtime_contact_report_missing`。
- 诊断建议会把失败回灌到 `upstream_static_furniture_authoring`，要求检查 collider generation、target mesh paths、bbox placement 和 contact instrumentation。

### Agent Guidance

- README 和 Codex skill 文档明确：权威 runtime 验证必须在 Linux + Isaac Sim Docker 中运行。
- 下游 runtime fail 不应只作为单次失败处理，而应作为 data flywheel 反馈，用于改进上游 SimReady 推荐、collider authoring 和资产包导出策略。
