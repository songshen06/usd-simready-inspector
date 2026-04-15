# Project Progress Report

## Scope

当前项目已经分成两条链路：

1. 通用 USD 检查与知识候选链路
2. 面向“静态可交互家具”的精简 SimReady 推荐链路

本报告主要用于后续开发和试用对齐当前已实现状态，不包含新的规划扩展。

## Current Status

项目当前已经能完成以下工作：

- 对 USD 资产做本地离线检查，输出 detailed report JSON
- 在 detailed report 基础上生成规则驱动的 `knowledge_candidate.json`
- 将 report / knowledge 扁平化为 CSV，供人工审核
- 从 CSV 生成 taxonomy seed 和 group reference stats
- 针对静态家具资产提取一套更小的 reference JSON
- 使用 `reference JSON + 新 USD` 输出静态家具 recommendation JSON
- 在 recommendation JSON 中输出尺寸参考与缩放建议
- 根据 recommendation JSON 将静态 collider 参数写回 USD

## Implemented Scripts

### Existing General Pipeline

- [usd_inspector.py](/mnt/c/Users/songs/Downloads/usd_inspect/usd_inspector.py)
  - 打开 USD Stage
  - 提取 stage / geometry / materials / physics / metadata
  - 输出 detailed report JSON

- [knowledge_candidate.py](/mnt/c/Users/songs/Downloads/usd_inspect/knowledge_candidate.py)
  - 从 detailed report 构建规则驱动的 knowledge candidate
  - 包含语义、材质、结构模式、geometry features、physics values、collider recommendation

- [report_to_knowledge_candidate.py](/mnt/c/Users/songs/Downloads/usd_inspect/report_to_knowledge_candidate.py)
  - 将已有 `*.report.json` 转换为 `*.knowledge_candidate.json`

- [reports_to_csv.py](/mnt/c/Users/songs/Downloads/usd_inspect/reports_to_csv.py)
  - 输出 `asset_summary.csv`
  - 输出 `component_map.csv`
  - 输出 `candidate_review.csv`

- [seed_taxonomy_from_csv.py](/mnt/c/Users/songs/Downloads/usd_inspect/seed_taxonomy_from_csv.py)
  - 基于 CSV 做 review-friendly grouping 和 profile suggestion

- [build_group_reference_stats.py](/mnt/c/Users/songs/Downloads/usd_inspect/build_group_reference_stats.py)
  - 生成 group-level reference stats

### New Static Furniture Pipeline

- [static_furniture.py](/mnt/c/Users/songs/Downloads/usd_inspect/static_furniture.py)
  - 新增共享模块
  - 负责静态家具特征抽取、reference grouping、reference matching、recommendation 构建

- [extract_static_furniture_reference.py](/mnt/c/Users/songs/Downloads/usd_inspect/extract_static_furniture_reference.py)
  - 从 USD 文件或目录提取静态家具 reference JSON

- [recommend_static_furniture_simready.py](/mnt/c/Users/songs/Downloads/usd_inspect/recommend_static_furniture_simready.py)
  - 输入 `reference JSON + 新 USD`
  - 输出 recommendation JSON

- [apply_static_furniture_simready.py](/mnt/c/Users/songs/Downloads/usd_inspect/apply_static_furniture_simready.py)
  - 根据 recommendation JSON 将静态 collider 参数 author 到 USD

## Static Furniture Feature Set

静态家具链路当前只保留了较小的任务导向特征集：

- 家具语义
  - `chair`
  - `sofa`
  - `stool`
  - `bench`
  - `ottoman`
  - `table`
  - `desk`
  - `cabinet`
  - `shelf`
  - `storage`
  - `decor`
  - `non_furniture`

- 材质大类
  - `wood`
  - `metal`
  - `plastic`
  - `fabric`
  - `glass`
  - `stone`
  - `unknown`

- 尺寸特征
  - `bbox`
  - `footprint`
  - `height_band`
  - `size_bucket`
  - `aspect_ratio_hint`
  - `volume_estimate_bbox`

- 支撑结构特征
  - `ground_contact_likely`
  - `support_surface_likely`
  - `seat_like`
  - `storage_like`
  - `backrest_likely`
  - `legged_likely`
  - `narrow_tall_likely`

- 几何与 authoring 特征
  - `mesh_count`
  - `primary_mesh_count`
  - `auxiliary_mesh_count`
  - `points_count_total`
  - `face_count_total`
  - `target_mesh_paths`

- 静态 collider 推荐
  - `approximation`
  - `scope`
  - `confidence`
  - `basis`

- 尺寸建议
  - `reference_target_bbox`
  - `axis_scale_to_target_bbox`
  - `suggested_uniform_scale`
  - `size_warning`

## Reference Grouping

静态家具 reference 当前按以下维度构建 group：

- `furniture_class`
- `material_family`
- `size_bucket`
- `seat_like / nonseat`
- `storage_like / nonstorage`

示例：

- `chair__fabric__medium__seat__nonstorage`
- `bench__metal__medium__seat__nonstorage`
- `cabinet__wood__large__nonseat__storage`

这一步的目标是给新资产找“最像的参考组”，再从组内最常见的 collider recommendation 回推建议值。

## Current Authoring Behavior

`apply_static_furniture_simready.py` 当前只做保守的静态 collider authoring：

- 对目标 mesh prim 应用 `PhysicsCollisionAPI`
- 对目标 mesh prim 应用 `PhysicsMeshCollisionAPI`
- 写入 `physics:collisionEnabled = true`
- 写入 `physics:approximation = ...`

当前不会自动做：

- joint authoring
- articulation authoring
- vehicle 相关 authoring
- dynamic rigid body 流程
- mass / density / friction / restitution 推荐与 authoring

## Validation Completed

### Environment Validation

已确认当前环境可用：

- `pxr` 可正常导入
- 新增脚本可通过 `py_compile`

### Single Asset Validation

已对以下链路做实际验证：

- `armchair_inst.usd` -> reference JSON
- `armchair.usd + reference JSON` -> recommendation JSON
- `armchair.usd + recommendation JSON` -> 导出新的 `.usda`

在导出的 `.usda` 中已确认存在：

- `PhysicsCollisionAPI`
- `PhysicsMeshCollisionAPI`
- `physics:collisionEnabled`
- `physics:approximation`

### Batch Validation

已对以下目录批量生成 reference：

- `/mnt/c/Users/songs/Downloads/SimReady_Furniture_Misc_01_NVD@10010/Assets/simready_content/common_assets/props`

实际结果：

- 扫描到资产 `808`
- 识别为家具资产 `192`
- 生成 group `49`

## Known Data Characteristics

当前这套 SimReady 资产有一个非常重要的结构特征：

- 很多 `main` / `base` 变体本身不直接带 primary mesh
- 真实可 author 的几何通常在 `*_inst.usd` 或 `*_inst_base.usd`

为此，静态家具链路已经加入兜底逻辑：

- 如果输入 USD 没有 primary mesh
- 会尝试自动切换到对应的 inst 变体作为 authoring source

## Known Limitations

- 静态家具分类仍然主要是规则驱动，不是学习式分类
- `support_structure` 目前部分依赖语义和文件命名，不是纯几何识别
- 材质目前只保留材质大类，没有进入 physics material recommendation
- recommendation 当前重点是 collider 和尺寸参考，不包含摩擦、质量、密度等参数推荐
- recommendation 会输出尺寸建议，但不会自动修改 USD 的几何尺度
- reference 批量构建时会看到部分外部引用缺失 warning，但不阻断处理
- 项目目录不是 git 仓库，当前状态无法通过提交历史回溯

## Recommended Trial Workflow

后续开发或试用建议按以下顺序：

1. 先对家具资产目录生成 reference JSON
2. 选几个新资产跑 recommendation JSON
3. 抽查 recommendation 的 `furniture_class`、`size`、`support_structure`、`recommended_collider`
4. 再运行 apply 脚本导出 `.usda`
5. 在 USD 工具链里检查 collider authoring 是否符合预期

示例命令：

```bash
python3 extract_static_furniture_reference.py "<assets_dir>" --recursive --output furniture_reference.json
python3 recommend_static_furniture_simready.py furniture_reference.json /path/to/new_asset.usd --output new_asset.recommendation.json
python3 apply_static_furniture_simready.py /path/to/new_asset.usd new_asset.recommendation.json --output new_asset.simready_static.usda
```

## Handoff Notes

当前实现适合用于：

- 家具静态 SimReady 推荐试用
- 后续规则微调
- 后续小规模人工复核

当前实现不适合直接视为：

- 完整的通用 SimReady 自动 authoring 系统
- 动态家具或关节家具处理系统
- 高保真 physics material 参数自动配置系统

## Documentation

新链路的基本用法已补充到 [README.md](/mnt/c/Users/songs/Downloads/usd_inspect/README.md)。
