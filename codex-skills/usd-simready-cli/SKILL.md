---
name: usd-simready-cli
description: Use when processing USD/USDZ/USDA/USDC assets with the usd-simready-inspector repository, especially to create self-contained SimReady-style static asset exports, fix missing MDL/texture dependencies, normalize relative asset paths, apply reference-based scale, correct Y-up/Z-up or lying-down geometry orientation, author static collision, apply safe primitive-collider repair findings, and validate output reports.
---

# USD SimReady CLI

Use the repository's unified CLI first. This skill owns SimReady preparation and
authoring; the separate `omniverse-usd-asset-validator` skill owns validator,
runtime, and flywheel checks.

```bash
python3 usd_simready_cli.py process REF_JSON INPUT_USD \
  --output OUTPUT_USD \
  --emit-report
```

Run commands from the `usd-simready-inspector` repository root unless the user gives another checkout path. The default reference is usually:

```text
simready_furniture_reference_with_wikidata.json
```

## Dependencies

Before running the workflow, verify these requirements:

- A checkout of `usd-simready-inspector` containing `usd_simready_cli.py`.
- Python 3 in an environment where `from pxr import Usd` works.
- The reference JSON, usually `simready_furniture_reference_with_wikidata.json`.
- Read access to the input USD/USDZ/USDA/USDC and its sidecar assets.
- Write access to the output directory so copied textures, recommendation JSON, and report JSON can be emitted.
- `~/omni-asset-cli/omni_asset_cli.py` for source mesh preflight before `process`; use `--omni-asset-cli` when the checkout is elsewhere.
- Downstream runtime validation must use Linux + Isaac Sim Docker through `omni-asset-cli physics-hit-test`; do not treat host Python or non-container runtimes as authoritative.
- Foundation-profile-dependent work additionally requires the separately pinned Foundation checkout documented in `FOUNDATION_PROFILE_DEPLOYMENT.md`. This skill does not execute a Foundation profile: `omni-asset-cli` owns `foundation-validate --official-cli --shadow`, upstream finding provenance, native PhysX evidence, and Docker contact acceptance.
- Current `usd_simready_cli.py apply/process` includes post-export bbox size validation by default. Use `--skip-size-validation` only when the user explicitly accepts bypassing scale/orientation validation.

If `pxr` is missing, stop and tell the user the USD Python bindings are required; do not fabricate report results.

## Skill Boundary

Use this skill for:

- SimReady recommendation generation.
- Mesh-gated `process` runs that emit self-contained USD packages.
- Scale/orientation correction, dependency packaging, static collision authoring, and post-export report checks.
- Content Physics Agent supplement merging into recommendations.
- Controlled candidate export for safe `RB.COL.002` primitive-collider findings from `omni-asset-cli`.

Call or hand off to the `omniverse-usd-asset-validator` skill for:

- Standalone source mesh validation with `omni-asset-cli validate --profile stage1-furniture`.
- Explaining validator rule failures such as manifold, topology, normals, missing references, or materials.
- Isaac Sim Docker runtime checks with `omni-asset-cli physics-hit-test`.
- End-to-end data-flywheel reports produced by `omni-asset-cli simready-flywheel`.

Do not duplicate validator logic in this skill. Treat validator output as an
upstream gate and as evidence for repair decisions.

## Foundation Profile Handoff

For the passive cart / physics-prop workflow, the upstream baseline is
`Prop-Robotics-Physx v1.0.0` from Foundation `v2026.04.1`. Receive its profile
name, pinned tag/commit, normalized finding artifact, and selected asset SHA
from `omni-asset-cli`. This inspector may only create a new candidate from a
safe repair contract; it must never label that candidate as Foundation-passing
without the downstream revalidation result.

Keep these conclusions separate in reports:

- Foundation approximation/profile result: upstream authoring conformance.
- `RB.COL.003` from `omni-asset-cli`: local risk that cooked convex shapes can
  diverge from visual mesh.
- Native PhysX view plus contact/A-B probe: runtime evidence of an actual
  air-wall.

## Primitive Collider Repair From Validator Findings

`omni-asset-cli` owns detection and revalidation. This skill owns only the
controlled repair of its `RB.COL.002` finding contract. The repair corrects a
non-mesh primitive collider carrying `PhysicsMeshCollisionAPI` and
`physics:approximation`; it is not a mesh-generation or joint-rebuild tool.

First obtain the read-only findings from the validator project:

```bash
cd ~/omni-asset-cli
.venv/bin/python omni_asset_cli.py physics-collider-audit INPUT_USD \
  --out out/<name>_collider_audit
```

Then create a separate candidate USD here:

```bash
.venv/bin/python usd_simready_cli.py collider-repair INPUT_USD \
  --findings /absolute/path/primitive_collider_audit.json \
  --output OUTPUT_CANDIDATE.usda \
  --report OUTPUT_CANDIDATE.collider_repair.json
```

Safety contract:

- Require `rule_id=RB.COL.002`, `repairability=safe`, and repair owner
  `usd-simready-inspector`; reject unrelated or duplicate finding paths.
- Never overwrite `INPUT_USD`; `--output` must be a different path.
- Remove only `PhysicsMeshCollisionAPI` and `physics:approximation` from the
  selected non-mesh collider prims.
- Preserve `PhysicsCollisionAPI`, geometry, transforms, materials, rigid
  bodies, and joints. Do not use this command to resolve nested rigid bodies or
  infer joint design intent.
- Re-run `omni-asset-cli physics-collider-audit` on the output. A zero finding
  count confirms schema cleanup only, not runtime collision behavior.

## Main Workflow

1. Confirm the input path exists and identify an output path. Prefer `.simready_static.usdc` for large assets and `.simready_static.usda` when the user needs a human-readable text layer.
2. Run `usd_simready_cli.py process` with the reference JSON, input USD, output USD, and `--emit-report`. Add `--output-format usdc` when writing a compact binary `.usdc`; the command runs the default source mesh preflight through `omni-asset-cli` before recommendation/apply.
3. If preflight blocks on topology, manifold, zero-area face, normal, or weld defects, stop and ask for source mesh repair before collider/parameter authoring. Use `--allow-mesh-defects` only when the user explicitly accepts that risk.
4. Read the emitted report and verify:
   - `issues` is empty or explain remaining issues.
   - `asset_dependencies.missing_relative_count == 0`.
   - All asset dependencies are relative when portability is required.
   - `stage.up_axis` is `Z` for downstream SimReady/Omniverse workflows unless the user requested otherwise.
   - `geometry.bbox.world.size` has plausible dimensions for the semantic class. For scaled assets, compare it to `recommendation.size_recommendation.reference_target_bbox` after orientation correction, not just to the source bbox.
   - Physics collision was authored on intended mesh targets.
5. Report the output USD, mesh preflight JSON, recommendation JSON, report JSON, and the key validation facts.
6. If the user asks for NVIDIA Content Agents Physics Agent, run `usd_simready_cli.py physics-agent INPUT_USD --dry-run` first. Only run the full command when a VLM API key and render backend are configured.
7. When Physics Agent predictions are available, merge them back into the rule-based recommendation with `usd_simready_cli.py physics-supplement`. Treat this as supplemental review evidence; do not claim it automatically overrides `recommendation.authoring`.

## Useful Commands

Inspect only:

```bash
python3 usd_simready_cli.py inspect INPUT_USD --output REPORT_JSON --pretty
```

Recommend only:

```bash
python3 usd_simready_cli.py recommend REF_JSON INPUT_USD --output RECOMMENDATION_JSON
```

Apply an existing recommendation:

```bash
python3 usd_simready_cli.py apply INPUT_USD RECOMMENDATION_JSON --output OUTPUT_USD
```

Apply an existing recommendation with compact binary output:

```bash
python3 usd_simready_cli.py apply INPUT_USD RECOMMENDATION_JSON \
  --output OUTPUT_USDC \
  --output-format usdc
```

Apply validator-selected primitive-collider repair:

```bash
python3 usd_simready_cli.py collider-repair INPUT_USD \
  --findings primitive_collider_audit.json \
  --output OUTPUT_CANDIDATE.usda \
  --report OUTPUT_CANDIDATE.collider_repair.json
```

One-step process:

```bash
python3 usd_simready_cli.py process REF_JSON INPUT_USD \
  --output OUTPUT_USD \
  --recommendation-output RECOMMENDATION_JSON \
  --report-output REPORT_JSON \
  --emit-report
```

## What Process Fixes

The `process` command can:

- Run source mesh preflight using `omni-asset-cli validate --profile stage1-furniture` before recommendation/apply.
- Block collider and parameter authoring when mesh quality defects would make collision unreliable.
- Generate a recommendation from the trusted reference library.
- Copy resolvable texture and asset dependencies next to the output.
- Preserve Omniverse built-in glTF MDL paths such as `gltf/pbr.mdl` by default so rendered PBR materials keep the native Omniverse/Isaac Sim shader behavior.
- Rewrite copied asset paths to explicit relative paths such as `./textures/name.png`.
- Apply `authoring.suggested_uniform_scale` when `apply_reference_scale=true`.
- Apply orientation correction when `apply_orientation_correction=true`, including Y-up to Z-up conversion and lying-down geometry fixes.
- Validate the exported default prim bbox against the recommendation's scale/orientation expectations. A mismatch fails the command unless `--skip-size-validation` is provided.
- Author static collision using the recommended USD approximation.

The `physics-agent` command can:

- Generate a per-asset NVIDIA Content Agents Physics Agent YAML config.
- Invoke an external `physics-agent run CONFIG`.
- Record expected outputs for predictions, HTML report, and physics-authored USD.

The `physics-supplement` command can:

- Read an existing rule-based recommendation JSON.
- Read Content Agents `predictions.jsonl`.
- Add rule-first constraints for class, target size, scale context, collider policy, and allowed mass range.
- Add `supplements.content_agent_physics` with component material, density, mass, friction, restitution, confidence, and reasoning.
- Add review flags for conflicts or outliers, while leaving static authoring values unchanged.
- Treat mass as unaccepted evidence when `mass_assessment.status` is not `usable`; use `mass_for_authoring_kg`, not raw VLM mass, for any downstream authoring decision.

Example:

```bash
python3 usd_simready_cli.py physics-supplement RECOMMENDATION_JSON \
  --physics-predictions PREDICTIONS_JSONL \
  --source-usd INPUT_USD \
  --output RECOMMENDATION_WITH_PHYSICS_JSON
```

## Size And Runtime Cautions

- Do not assume a downstream runtime report is authoritative for asset size. First verify the exported USD directly with the repository report or USD BBoxCache. A downstream template or hit-test harness can misread a referenced asset's composed transform and report the unscaled source bbox.
- For assets with `size_recommendation.status=scale`, the final output bbox should reflect `authoring.suggested_uniform_scale`; for Y-up sources with orientation correction, the tall/source-up axis should become Z in the report.
- If a rendered physics video shows only the asset and no drop object, check whether the runtime harness used the exported USD bbox or the unscaled source bbox. A tiny drop box and a huge reported bbox are signs of downstream bbox misuse, not necessarily a bad SimReady export.
- If downstream Docker runtime fails or only reports inferred contact, feed it back into upstream authoring. Prioritize collider generation, target mesh selection, bbox/scale normalization, template placement, and contact-report evidence until `checks.contact_report_detected=true`.
- Treat files generated before the current recommendation/apply run as stale until their timestamp and report path match the latest output. Re-run `process --emit-report` when in doubt.

## Interpreting Common Results

- `missing_relative_count > 0`: output is not self-contained; inspect `asset_dependencies.missing_relative`.
- `orientation_recommendation.apply=true`: geometry orientation is corrected during apply; verify final bbox has height on Z.
- `size_recommendation.status=scale`: source scale is corrected from the reference library.
- `review_required=true`: do not claim the asset is automatically safe; explain `review_reasons`.
- `auto_apply_safe=true`: the recommendation is suitable for automatic static authoring under current rules.
- `supplements.content_agent_physics`: VLM-derived supplemental evidence from NVIDIA Content Agents; use it to review material, mass, and friction assumptions. If `mass_for_authoring_kg=null`, do not report the raw mass as the recommended mass.

## Validation Snippet

Use this after `--emit-report` when summarizing results:

```bash
python3 - <<'PY'
import json
r=json.load(open("REPORT_JSON", encoding="utf-8"))
print("up=", r["stage"]["up_axis"])
print("bbox=", r["geometry"]["bbox"]["world"]["size"])
print("missing=", r["asset_dependencies"]["missing_relative_count"])
print("all_relative=", all(i["is_relative"] for i in r["asset_dependencies"]["all"]))
print("issues=", r["issues"])
PY
```

Replace `REPORT_JSON` with the emitted report path.
