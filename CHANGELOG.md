# 更新说明

## 2026-10-28 — SimReady 契约检查与定向修复（PR #1）

本次更新把工具从“只做静态检查与推荐”扩展为**可判定的契约检查 + 受控修复闭环**：
给定一个 SimReady profile，工具能回答“是否合规、差哪几条 requirement、能自动修哪些、修完是否真的合规”。
所有判定都委托给官方 `simready-validate`，不在本仓库重写规范；运行期（接触、稳定性、articulation）仍按设计留在下游。

四个提交（线性历史，`main` 上为 `b088c05` → `9401a1d` → `691d75d` → `00e2deb`）：

| 提交 | 主要内容 |
| --- | --- |
| `b088c05` | 去掉 O(n²) 物理扫描；关节/语义/collider 分类修正 |
| `9401a1d` | 新增 `conformance` 子命令与 profile 自动判定 |
| `691d75d` | 按 requirement code 授权修复并复验 |
| `00e2deb` | 标准候选目录与缩略图（复制 / 渲染，不伪造） |

---

### 1. 性能：移除物理检查中的平方级扫描

**问题**：2000 prims 的资产上，`inspect` 需要 7.2 秒，其中 5.5 秒耗在 `_nearest_physics_scene`。

**根因**（`usd_inspector.py`，三处叠加）：

1. `_nearest_physics_scene()` 在祖先链上找不到 `UsdPhysics.Scene` 时，会**遍历整个 stage**；该函数对每个 collider 调用一次 → O(n²)；
2. `_nearest_rigid_body_ancestor()` 收到的是 `list`（相邻位置其实已经建好了 `rigid_body_paths_set`），每次成员判断都是 O(n)；
3. 嵌套刚体判定对每个 collider 遍历全部刚体做字符串前缀比较 → O(n²)，仅这一步就产生约 400 万次 `_path_has_prefix` 调用。

**修复**：

- 预计算场景集合，改用 `_make_scene_resolver()` 闭包，按祖先链 O(depth) 解析，单场景时直接命中；
- 刚体祖先判定改传 `set`；
- 嵌套判定改为沿祖先链向上走（典型深度 2–3），不再扫描全部刚体。

**数据**：

| 资产 | 修复前 | 修复后 |
| --- | --- | --- |
| 2000 prims | 7.20 s | **1.53 s** |
| 31 prims（GURSKEN 柜体） | 1.63 s | **0.59 s** |

输出除下述有意修正外逐字段不变（2000 prims 场景 diff 为 0）。

### 2. 正确性修正

- **关节重复计数与类型**：`joint_schema_names` 从 `Joint` 到 `D6Joint` 逐个 `IsA` 判断，子类同时命中父类，导致 3 个棱柱关节被计成 6 个，且首个条目类型恒为 `UsdPhysics.Joint`。现在按 prim path 去重并保留最具体的 schema。
- **语义标签只认旧格式**：原实现仅解析 `semantic:<key>:semanticData/semanticType`，当前 SimReady 使用的 `SemanticsLabelsAPI`（`semantics:labels:<taxonomy>`）完全读不到。现已同时支持两种形式，并统一归一化为 `{path, taxonomy, labels, source}`。
- **三段式旧语义被静默丢弃**：`semantic:class:semanticData` 因 `len(parts) < 4` 的守卫被直接跳过；守卫放宽到 3 段，并在缺少 `semanticType` 时从 key 推导分类。
- **collider 分类缺一态**：原先只有 `static` / `dynamic_or_kinematic`，运动学柜体被算进动态侧，使 `static_collider_count` 对静态家具链路失真。现为 `static` / `kinematic` / `dynamic` 三态，新增 `kinematic_collider_count`、`is_kinematic_collider`。
- **多材质误报**：原判据 `material_count > 1 and not subsets` 会把“每个 mesh 各绑一个材质”这种完全合法的资产报成问题。现在仅当**同一 binding purpose 下同一 mesh 绑定多个材质**时才报 issue，常规逐 prim 绑定降级为 note。
- **`semantic_candidates` 恒为空**：该字段此前是占位符，现由归一化结果填充。

### 3. 新增 `conformance` 子命令

```bash
usd-simready conformance asset.usda                       # 自动判定 profile
usd-simready conformance asset.usda --profile auto --require-confidence 0.8
usd-simready conformance asset.usda --profile Robotics-Prop --version 3.3.0
```

**为什么用子进程**：`simready-validate` 要求 Python < 3.13，而 DCC 侧解释器可能是 3.13；进程内 import 会把两套 `pxr` 绑死。CLI 通过 `--validator` / `$SIMREADY_VALIDATE` / `PATH` 三级发现可执行文件。

**profile 判定链**（`profile_auto.py`）：

```
explicit   --profile <id>                       直接采信
declared   SimReady_Metadata.validation.profile 与上游 auto 对齐
heuristic  schema / 命名 / 语义标签 → 候选集(≤3)
validated  逐候选校验，只保留 required 全过的
advisor    LLM 仅在多个候选同时通过时排序（钩子已留，未接 CLI）
```

硬规则：**启发式候选必须通过校验才被采纳**；不通过则整体 `rejected`，不给“建议性质”的结论。

**退出码**：`0` 合规、`1` 不合规、`2` 未判定（找不到 validator、profile 无法确定、置信度不足）。

**报告新增字段**：

- `decision_status`：`validated` / `needs_review` / `rejected` / `unresolved` / `not_evaluated`；
- `profile_decision`：`profile_id`、`profile_version`、`source`、`confidence`、`candidates`、`evidence`；
- `conformance`：`status`、`failing_required_features`、`deferred_to_runtime`（按 physx/newton/mujoco/isaac 分组）、`features`（validator 原始 `features_summary` 原样保留）；
- `runtime_handoff`：`units`、`gravity`、`bodies`、`joints`（含轴与上下限）、`colliders`、`reproduce` 命令。

可选 feature 未满足归入 `deferred_to_runtime`，不阻塞 CI；这是把“运行期能力未声明”与“契约不满足”分开的关键。

### 4. 新增 `repair` 子命令（按码授权）

```bash
usd-simready repair asset.usda --candidate-root out --render-thumbnail --report audit.json
usd-simready repair asset.usda --output fixed.usda --dry-run
```

修复不是启发式的，而是由**当前 profile 报出的 requirement code** 授权：

| 分类 | 含义 | 是否改动 |
| --- | --- | --- |
| `allowed` | 该码正在失败，且有注册修复器 | 改 |
| `unlicensed` | 有修复器，但当前 profile 没判它失败 | 不动 |
| `unsupported` | 失败但没有修复器 | 不动，明确报告 |

- findings 可直接从 validator 的 `requirement_messages` 中解析 prim 路径，无需外部 `omni-asset-cli` 审计文件；`--findings` 仍兼容外部结果。
- 当前注册修复器：`RB.COL.001`（非 GPrim 上的 CollisionAPI）、`RB.COL.002`（非 Mesh 上的 MeshCollisionAPI + `physics:approximation`），均为可逆的“移除非法 API”。
- repair 场景调用 `resolve_and_validate(require_clean=False)`：取失败最少的候选，标记 `source=inferred, confidence=0.5`，避免“为了能修就放宽判定”。
- 修复后自动复验，输出 `before` / `plan` / `repair.applied` / `after` / `status` 审计。

### 5. 标准候选目录与缩略图

`--candidate-root` 直接产出下游可直接消费的布局：

```
<root>/<asset>/simready_usd/<asset>.usda
<root>/<asset>/simready_usd/.thumbs/256x256/<asset>.usda.png
```

**缩略图来源链**：`--thumbnail` → 源资产目录 `.thumbs/256x256/*.png` → 资产目录 `.thumbs/...` → 同目录 `thumbnail.png` → 渲染。

**渲染回退**：`usdrecord --imageWidth 256`（Metal，快，正视）→ `Blender -b --factory-startup`（Workbench，45°，方形 256×256）→ `unavailable`。

安全约束：

- 安装前校验 PNG magic、IHDR 可解析、文件 > 512 字节；
- 先写临时文件再原子替换，避免半截文件进入资产包；
- **找不到就报 `unavailable`，绝不生成占位图**——`SR.002` 会继续保持失败，退出码 1。

### 6. 行为变更与迁移注意

- `dynamic_collider_count` **不再包含** kinematic 物体下的 collider，这部分移入 `kinematic_collider_count`。`knowledge_candidate.py` 内的消费逻辑已按旧语义兼容（`dynamic` / `kinematic` / `dynamic_or_kinematic` 均计入可动侧）。
- `semantic_candidates` 从恒为 `[]` 变为规范化列表；若下游依赖该字段为空，需要调整。
- 新增字段：`physics.kinematic_collider_count`、`physics.colliders[].is_kinematic_collider`、`materials.multi_material_meshes_without_subsets`、`metadata.semantic_candidates`。
- 原有命令（`inspect` / `recommend` / `apply` / `collider-repair` 等）**行为未变**，本次仅新增两个子命令与若干字段。

### 7. 验证

| 场景 | 结果 |
| --- | --- |
| 2000 prims 合成资产 | 7.20 s → 1.53 s |
| 31 prims 柜体 | joints 6 → 3，误报 1 → 0 |
| GURSKEN 柜体 `Robotics-Prop@3.3.0` | 修复物理材质标签后 **PASSED** |
| 注入 `RB.COL.001/002` → repair → 复验 | non_conformant → **conformant**（exit 0） |
| 缺缩略图 + `--render-thumbnail` | `rendered`（usdrecord 256×186）→ conformant |
| 缺缩略图、未开渲染 | `unavailable` → 仍 non_conformant（exit 1） |
| 无 validator | `not_evaluated`（exit 2） |
| `--require-confidence 0.99` | `needs_review`（exit 2） |

以上均在干净 clone 上按 `git apply` 顺序复现，并在合并后的 `main` 上重跑通过。

### 8. 已知限制

- 修复器仅覆盖 `RB.COL.001` / `RB.COL.002`；`SR.002`、`NP.005`、`VM.BIND.001` 等仍归入 `unsupported`。
- 复验依赖候选资产处于合法 SimReady 目录结构，否则 `NP.005` / `SR.002` 会误报；`--candidate-root` 即为此而设。
- `usdrecord` 输出高度由 stage 相机宽高比决定（本次为 256×186），Apple 版没有 `--imageHeight`；需要严格方形时走 Blender 分支。
- `declared` 路径（读取 `SimReady_Metadata.validation.profile`）已实现但缺少 fixture 覆盖。
- LLM profile advisor 仅有 `advisor=` 钩子，尚未接 CLI，也未做缓存与置信阈值落盘。

### 9. 后续计划

1. 补 3 个 fixture（合规 prop / 注入 RB.COL 违规 / 缺缩略图）与 pytest，锁住本次修复的回归；
2. 最小 CI：仅跑不依赖 `simready-validate` 的部分（`usd-core` 即可），避免把 Python < 3.13 约束引入主仓库；
3. 按“改动小、可验证”的顺序扩展修复器：`SR.002` → `NP.005` → `VM.BIND.001`。

