# USD Inspector

一个纯本地、CLI-first 的 USD 资产体检脚本，使用 `pxr` 官方 USD Python API 提取结构化 JSON 摘要，适合在 SimReady 前置分析阶段快速了解几何、材质、物理 schema 和元数据情况。当前还额外支持从 detailed report 生成第二层 `knowledge_candidate`，用于入库、聚类和人工审核。

## 依赖

- Python 3
- `pxr` USD Python 绑定

如果你的环境已经能执行类似下面的代码，通常就可以运行：

```bash
python -c "from pxr import Usd; print('USD OK')"
```

## 用法

```bash
python usd_inspector.py <input_usd> [--pretty] [--output report.json] [--max-prims 0]
python usd_inspector.py <input_usd> --emit-knowledge-candidate [--knowledge-output asset.knowledge_candidate.json]
python usd_inspector.py <input_usd> --emit-knowledge-candidate --inline-knowledge-candidate --output report.json
```

示例：

```bash
python usd_inspector.py asset.usdz --pretty
python usd_inspector.py robot.usd --pretty --output robot_report.json
python usd_inspector.py scene.usda --max-prims 5000 --output scene_report.json
python usd_inspector.py forklift.usdz --pretty --emit-knowledge-candidate
python usd_inspector.py forklift.usdz --pretty --emit-knowledge-candidate --knowledge-output forklift.knowledge_candidate.json
python usd_inspector.py forklift.usdz --inline-knowledge-candidate --output forklift.report.json
```

## 输出内容

脚本会输出一个适合机器消费的 JSON，核心包括：

- `stage`：defaultPrim、upAxis、metersPerUnit、timeCodes、FPS
- `summary`：prim / mesh / xform / material / subset 统计
- `geometry`：每个 mesh 的点数、面索引数、extent、normals、st、bbox、shape hints
- `materials`：Material prim、material binding、GeomSubset、physics-purpose 绑定线索
- `physics`：RigidBody / Collision / Mass / Articulation / joints / PhysX schema
- `metadata`：kind、assetInfo、customData、documentation、variantSets、displayName
- `issues` / `notes`：轻量检查提示，不会中断流程

## Knowledge Candidate

`knowledge_candidate` 是建立在 detailed report 之上的第二层规则推断，不会重新打开 USD 文件，也不会覆盖原始报告字段。它强调：

- raw facts 与 inferred candidates 分离
- 每个候选都带 `source` / `basis` / `confidence`
- 适合写入 SQLite / DuckDB / JSONL / parquet 等分析流水线

核心字段包括：

- `asset_variant_role`：按文件名判断 `main/base/inst/inst_base`
- `semantic_candidates`：根据 identifier / display name / basename 生成语义候选
- `material_family_candidates`：根据 material name/path 推断 wood / metal / rubber 等
- `structure_pattern`：按 mesh / material / physics 关系判断结构模式
- `component_map`：mesh 视角的平铺结构，便于后续入库和聚类
- `geometry_features`：bbox 派生的尺寸、体积估计、接地性、多 mesh 等
- `physics_values`：physics 路径和值容器
- `collider_recommendation`：规则驱动的 collider 推荐
- `physics_profile_candidates`：如 `wheeled_rigid_vehicle` / `static_furniture` / `decorative_prop`
- `simready_completeness`：按 geometry / render / physics / semantics 做 completeness 打分
- `review_flags`：人工审核友好的短标记

### 第二轮修复说明

当前规则层增加了几项高优先级修复：

- `asset_id` 优先级调整为 `asset_info.identifier` → `model_metadata.asset_name` → `file.basename` → `default_prim`
- 新增 `asset_name_source`，用于追溯 `asset_id` 的最终来源
- mesh/component 增加 `is_auxiliary_mesh` 与 `mesh_role`，用于识别 `/Tagging/`、`/ThumbRig/`、`/Icon/`、`/Preview/`、`/Guide/`、`/Proxy/` 等辅助几何
- `geometry_features` 增加 `primary_mesh_count`、`auxiliary_mesh_count`、`bbox_may_include_auxiliary_mesh`
- material family 规则增强，额外覆盖 `stone`、`paper_fiber` 等，并区分显式 subset 绑定与普通 material name 命中的置信度
- physics value 容器会优先读取已存在的 authored 值；如果 detailed report 暂未提供，则计数字段为空或为 `0`，后续 inspector 增强后可自动受益

## 独立转换脚本

如果你已经有大量 `*.report.json`，可以直接做第二层转换：

```bash
python report_to_knowledge_candidate.py asset.report.json --pretty
python report_to_knowledge_candidate.py asset.report.json --output asset.knowledge_candidate.json
python report_to_knowledge_candidate.py asset.report.json --variant-role inst
```

默认会输出为同目录下的 `*.knowledge_candidate.json`。

## 批处理友好建议

如果你现有批量脚本已经在遍历资产并调用 `usd_inspector.py`：

- 可以直接加 `--emit-knowledge-candidate`
- 如果希望 detailed report 内联知识层，再加 `--inline-knowledge-candidate`
- 如果已经先生成了 detailed report，则批量调用 `report_to_knowledge_candidate.py` 即可

这种拆分方式更适合后续把 raw report 和 knowledge candidate 分开入库。

## CSV 导出

如果你想把一批 `*.report.json` / `*.knowledge_candidate.json` 扁平化给 Excel 人工审核，可以使用：

```bash
python reports_to_csv.py \
  --input-dir inspection_reports/simready_content \
  --output-dir csv_exports \
  --recursive \
  --include-component-map \
  --include-candidate-review
```

输出文件：

- `asset_summary.csv`：一行一个资产，适合做总览、筛选、排序
- `component_map.csv`：一行一个 mesh/component，适合看 mesh 与 material/collider 的关系
- `candidate_review.csv`：一行一个资产，保留人工填写列，如 `human_semantic_label` 和 `approved`

新增关键列：

- `asset_summary.csv`：`asset_name_source`、`primary_mesh_count`、`auxiliary_mesh_count`、`bbox_may_include_auxiliary_mesh`、`mass_value_count`、`density_value_count`、`collision_approximation_count`、`physics_material_param_count`
- `component_map.csv`：`is_auxiliary_mesh`、`mesh_role`、`collider_authored_approximation`、`collision_enabled`、`physics_material_param_summary`
- `candidate_review.csv`：`asset_name_source`、`auxiliary_mesh_present`、`human_structure_pattern`、`human_collider_choice`、`human_variant_keep`、`review_status`

### Excel 使用建议

- 直接用 Excel 打开 `csv_exports/asset_summary.csv`
- 先按 `simready_overall`、`review_flags`、`issues_count` 筛选
- 再打开 `candidate_review.csv`，填写人工复核列
- 所有 CSV 使用 UTF-8 with BOM 输出，Windows 下 Excel 打开中文和路径更稳定
- 注意：CSV 重新导出时默认会重写人工列；若需要保留人工填写内容，建议后续做 merge 策略

## Taxonomy Seeding

如果你已经拿到了：

- `asset_summary.csv`
- `candidate_review.csv`
- `component_map.csv`（可选）

可以进一步做“先分组、再建议 profile”的 review 辅助：

```bash
python seed_taxonomy_from_csv.py \
  --asset-summary csv_exports/simready_content/asset_summary.csv \
  --candidate-review csv_exports/simready_content/candidate_review.csv \
  --component-map csv_exports/simready_content/component_map.csv \
  --output-dir taxonomy_seed_output
```

输出文件：

- `candidate_review_enriched.csv`：在原始 review 表上追加 `semantic_bucket`、`physics_bucket`、`size_bucket`、`auto_group_key`、`auto_profile_suggestion`
- `taxonomy_seed.csv`：一行一个组，适合先看“组”而不是先看“资产”
- `group_samples.csv`：按组展开样本，便于在 Excel 中抽样检查

推荐人工使用方式：

1. 先打开 `taxonomy_seed.csv` 看哪些组最大、建议最集中
2. 再打开 `group_samples.csv` 看该组内的样本是否一致
3. 最后在 `candidate_review_enriched.csv` 里填写 `human_profile` 等人工列

## Group Reference Stats

在完成 `candidate_review_enriched.csv` 之后，还可以进一步按 `auto_group_key` 聚合出组级参考统计：

```bash
python build_group_reference_stats.py \
  --candidate-review-enriched taxonomy_seed_output/candidate_review_enriched.csv \
  --component-map csv_exports/simready_content/component_map.csv \
  --output-dir group_reference_output
```

输出：

- `group_reference_stats.json`：保留嵌套统计结构，适合后续 recommendation 规则读取
- `group_reference_stats.csv`：扁平化版本，适合 Excel 或快速浏览

这一步的目标不是替代人工判断，而是为后续 recommendation 提供“这个组里常见 collider / material / mesh complexity 是什么”的参考基线。

## Static Furniture Reference

如果当前阶段只做“静态可交互家具”的 SimReady 推荐，可以走一条更收缩的新链路：

1. 从一批家具 USD 提取 reference JSON
2. 用 `reference JSON + 新 USD` 生成 recommendation JSON
3. 按 recommendation 把 collider 参数写回到 USD

### 1) 提取静态家具 reference

```bash
python3 extract_static_furniture_reference.py \
  "/mnt/c/Users/songs/Downloads/SimReady_Furniture_Misc_01_NVD@10010/Assets/simready_content/common_assets/props" \
  --recursive \
  --output furniture_props.static_furniture_reference.json
```

输出 JSON 会聚焦于：

- 家具语义：`chair / sofa / table / desk / cabinet / shelf / ...`
- 尺寸：`bbox`、`footprint`、`height_band`、`size_bucket`
- 支撑结构：`seat_like`、`support_surface_likely`、`storage_like`、`ground_contact_likely`
- 静态 collider 推荐：`approximation` 与 `scope`

### 2) 对新资产生成 recommendation

```bash
python3 recommend_static_furniture_simready.py \
  sample_static_furniture_reference.json \
  /path/to/new_asset.usd \
  --output new_asset.static_furniture_recommendation.json
```

推荐输出会包含：

- 当前资产抽取出来的静态家具特征
- 匹配到的 reference group
- 推荐的 collider approximation / scope
- authoring 所需的 `target_mesh_paths`

如果输入的 `main` / `base` 变体本身不带 primary mesh，脚本会尽量自动切换到对应的 `*_inst.usd` / `*_inst_base.usd` 作为 authoring 源。

### 3) 按 recommendation 写回 USD

```bash
python3 apply_static_furniture_simready.py \
  /path/to/new_asset.usd \
  new_asset.static_furniture_recommendation.json \
  --output new_asset.simready_static.usda
```

当前写回逻辑会对目标 mesh prim author：

- `PhysicsCollisionAPI`
- `PhysicsMeshCollisionAPI`
- `physics:collisionEnabled = true`
- `physics:approximation = ...`

目前这是一个保守版本，重点覆盖“静态家具 collider authoring”；不会自动写 joint、vehicle 或主动运动相关参数。

## Before / After

- `asset_id` 不再轻易掉成 `RootNode`：现在文件 basename 会先于通用 default prim 名称参与选择，因此主键更稳定
- auxiliary mesh 不再污染主分析：`/Tagging/`、`/ThumbRig/`、`/Icon/` 等会被标成辅助 mesh，主统计优先基于 primary mesh
- material family 命中率更高：除了 `material_prims.name` 之外，还会综合 `render_materials`、subset 绑定、material path 和 component map
- physics values 统计更细：除了 presence，还会单独统计 `mass_value_count`、`density_value_count`、`collision_approximation_count`、`physics_material_param_count`

## 设计说明

- 不依赖 Omniverse Kit
- 不依赖 GUI
- 不依赖 Isaac Sim
- 对不同 USD Python 绑定版本做了兼容性容错
- `--max-prims` 可用于大场景快速抽样检查
- knowledge candidate 只做规则驱动、可解释的启发式推断，不假装做强 AI 语义理解

## 适用场景

- SimReady 资产入库前体检
- 普通 USD 资产几何 / 材质 / 物理可用性扫描
- pipeline 推断前的数据基线采样
