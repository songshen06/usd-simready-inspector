---
name: usd-simready-cli
description: Use when processing USD/USDZ/USDA/USDC assets with the usd-simready-inspector repository, especially to gate a modelling deliverable, derive a handoff of moving parts, check an asset against a SimReady profile, apply profile-licensed repairs, create self-contained SimReady-style static exports, fix missing MDL/texture dependencies, normalize relative asset paths, apply reference-based scale, correct Y-up/Z-up or lying-down geometry orientation, author static collision, apply safe primitive-collider repair findings, and validate output reports.
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

## Local environment

Use the repository virtualenv, not the system interpreter: it carries `pxr`
(`usd-core`), `simready-validate` and `pytest`.

```bash
.venv/bin/python usd_simready_cli.py inspect INPUT_USD --output report.json
export SIMREADY_VALIDATE="$PWD/.venv/bin/simready-validate"   # conformance/repair
```

Recreate it with:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt \
  "simready-validate>=2026.8.0,<2026.9.0" simready-foundation-tier-core
```

`simready-validate` requires Python < 3.13, so never install it into a newer
interpreter (for example Blender's bundled Python). If `.venv` is missing, run
`.venv/bin/python -c "from pxr import Usd"` first and create the venv before
claiming any result.

## Skill Boundary

Use this skill for:

- SimReady recommendation generation.
- Mesh-gated `process` runs that emit self-contained USD packages.
- Scale/orientation correction, dependency packaging, static collision authoring, and post-export report checks.
- Content Physics Agent supplement merging into recommendations.
- Controlled candidate export for safe `RB.COL.002` primitive-collider findings from `omni-asset-cli`.
- Visual-layer gating, handoff derivation, profile conformance and profile-licensed repair for static assets.

Call or hand off to the `omniverse-usd-asset-validator` skill for:

- Standalone source mesh validation with `omni-asset-cli validate --profile stage1-furniture`.
- Explaining validator rule failures such as manifold, topology, normals, missing references, or materials.
- Isaac Sim Docker runtime checks with `omni-asset-cli physics-hit-test`.
- Any runtime claim (contact, stability, articulation): the static pipeline only reports `deferred_to_runtime`.
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

## Static Contract Pipeline

The inspector is the boundary between modelling and SimReady authoring. Use this
chain when the task is "make this asset conform to a SimReady profile", not
merely "inspect it". It is static by design: runtime behaviour (contact,
stability, articulation) is validated downstream and must never be claimed here.

```bash
# 1. gate the modelling deliverable (read-only; the visual layer must have no Physics)
python3 usd_simready_cli.py inspect VISUAL.usda --gate visual --output inspection.json

# 2. merge declared intent with measured geometry
python3 usd_simready_cli.py handoff --report inspection.json --intent intent.json --output handoff.json

# 3. author rigid bodies, colliders and joints from the handoff
python3 usd_simready_cli.py author VISUAL.usda --handoff handoff.json --output AUTHORED.usda

# 4. check the authored asset against a profile (delegates to simready-validate)
python3 usd_simready_cli.py conformance AUTHORED.usda --profile auto --output conformance.json

# 5. freeze the static expectations for the downstream GPU harness
python3 usd_simready_cli.py runtime-request --conformance conformance.json \
  --handoff handoff.json --output runtime_request.json

# 6. repair only the codes the profile reports as failing, then re-validate
python3 usd_simready_cli.py repair AUTHORED.usda --candidate-root out/candidates --render-thumbnail
```

| command | reads | writes | exit codes |
| --- | --- | --- | --- |
| `inspect --gate visual` | visual USD (+ optional handoff, baseline) | inspection JSON | 0 pass, 1 gate failed |
| `handoff` | inspection JSON + intent JSON | handoff JSON | 0 ok, 1 invalid intent, 2 unreadable input |
| `author` | visual USD + handoff | authored USD + audit | 0 ok, 1 refused, 2 unreadable input |
| `conformance` | USD | conformance JSON | 0 conformant, 1 non-conformant, 2 not evaluated |
| `repair` | USD (+ findings) | candidate USD + audit | 0 conformant after repair, 1 still failing / no licensed repair, 2 not evaluated |
| `runtime-request` | conformance JSON + handoff | runtime request JSON | 0 ok, 2 unreadable input |

Rules that keep the boundary stable:

- **Never infer moving parts from prim names.** They must be declared in the
  intent/handoff. Without a handoff, gate checks 07/08 report `skipped`, not a guess.
- **Derive, do not author numbers.** The handoff computes `pivot` (the bbox face
  the joint pulls away from) and `travel` (`bbox depth − reserve`). If the geometry
  changes, regenerate the handoff instead of editing values.
- **`facts_digest` pins the geometry** the handoff was built from; a mismatch means
  the asset was re-exported and the handoff is stale.
- **Authoring consumes the handoff and nothing else.** `author` takes pivot,
  travel, axis and mass policy from the handoff; it refuses an input that already
  carries physics. Rotated moving parts are supported: the rotation difference
  between the base body and the part is carried on the joint's body1 frame, and
  the applied offset is recorded as `rotation_offset_deg` in the audit. Non-unit
  scale is refused (AU.09) because it breaks joint frames. Refusals are explicit
  error codes, never a silent approximation.
- **Mass is volume x density.** Signed-tetrahedron volume keeps hollow parts
  honest; a solid proxy box will report a solid-box mass, so check the modelling
  deliverable, not the formula, when masses look wrong.
- **Conformance is not ours to decide.** It calls `simready-validate` in a
  subprocess (`--validator`, `$SIMREADY_VALIDATE`, or `PATH`); that package needs
  Python < 3.13, so never import it into the DCC interpreter.
- **Optional features are deferred, not failed.** Feature misses under an optional
  feature land in `deferred_to_runtime`, grouped by physx / newton / mujoco / isaac.
- **Repair needs authorization.** A repairer runs only when the active profile
  reports that requirement code as failing; otherwise the finding is reported as
  `unlicensed` (not applicable) or `unsupported` (no repairer) and left untouched.
- **Local conformance is a pre-check, never the authority.** The GPU harness
  (`omni-asset-cli foundation-validate`) runs a pinned Foundation tag in the
  runtime image; that verdict plus runtime contact evidence is authoritative.
  The runtime request therefore carries `"authoritative": false` and the exact
  versions the local check used.
- **The runtime request is frozen, not recomputed.** It records expected body
  count, joint count, kinematic bases, mass and max travel. `diagnose
  --runtime-request` re-checks those against the current inspection and fails
  when the asset changed underneath the request (stale request), instead of
  trusting a document nobody updated.
- **A thumbnail is never fabricated.** `--render-thumbnail` copies an existing PNG
  or renders one with `usdrecord`, then Blender; failure reports `unavailable` and
  leaves `SR.002` failing.

When asked "is this asset SimReady", report the profile, its version, the
conformance status, the failing required features, and the deferred runtime list.
Do not answer "yes" from inspection alone.

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

Gate a modelling deliverable (no Physics allowed in the visual layer):

```bash
python3 usd_simready_cli.py inspect VISUAL_USD --gate visual --output INSPECTION_JSON
```

Derive the authoring handoff:

```bash
python3 usd_simready_cli.py handoff --report INSPECTION_JSON --intent INTENT_JSON --output HANDOFF_JSON
```

Author physics from the handoff:

```bash
python3 usd_simready_cli.py author VISUAL_USD --handoff HANDOFF_JSON --output AUTHORED_USD --report AUDIT_JSON
```

Check against a profile:

```bash
python3 usd_simready_cli.py conformance INPUT_USD --profile auto --output CONFORMANCE_JSON
```

Freeze runtime expectations for the GPU harness:

```bash
python3 usd_simready_cli.py runtime-request --conformance CONFORMANCE_JSON \
  --handoff HANDOFF_JSON --foundation-tag v2026.04.1 --output RUNTIME_REQUEST_JSON
```

Diagnose with runtime evidence and the frozen request:

```bash
python3 usd_simready_cli.py diagnose --recommendation REC_JSON --report INSPECTION_JSON \
  --runtime-report RUNTIME_REPORT_JSON --runtime-request RUNTIME_REQUEST_JSON --output DIAGNOSIS_JSON
```

Profile-licensed repair with re-validation:

```bash
python3 usd_simready_cli.py repair INPUT_USD --candidate-root OUT_ROOT --render-thumbnail --report AUDIT_JSON
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
