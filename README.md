# USD SimReady Inspector

CLI tools for inspecting USD assets and generating rule-based SimReady recommendations.

This repository currently covers two workflows:

- General USD inspection and `knowledge_candidate` generation
- Static furniture SimReady recommendation and collider authoring

## Version Notes

### 2026-04 Semantic Metadata Update

This update adds SimReady semantic metadata extraction and comparison utilities.

New in this version:

- Extracts authored `semantic:*` attributes from USD metadata
- Normalizes SimReady semantic signals into:
  - `qcodes`
  - `classes`
  - `hierarchies`
  - `label_tags`
  - `anchor_tags`
- Uses semantic metadata as additional furniture classification signals
- Improves physics inspection for static furniture workflows by recording:
  - mesh `purpose`
  - mesh `subdivisionScheme`
  - collider `approximation`
  - static vs dynamic collider membership
  - rigid-body and scene-level physics facts
- Adds `compare_reference_recommendations.py` to compare recommendation outputs between an old and a new reference library

Practical impact:

- SimReady furniture libraries with authored semantic tags produce broader and more stable furniture grouping
- Recommendation group sizes increase when the new semantic metadata helps recover furniture assets that naming-only heuristics previously missed
- New semantic metadata is now available in reference JSON output for downstream grouping and review

## What It Does

### General Inspection

- Opens USD/USDZ/USDA/USDC assets with `pxr`
- Extracts structured JSON reports for:
  - stage
  - geometry
  - materials
  - physics
  - metadata
- Builds a second-layer `knowledge_candidate.json` for downstream review and grouping

### Static Furniture Recommendation

- Extracts a furniture reference JSON from a USD library
- Uses `reference JSON + new USD` to produce:
  - Stage 1 furniture/decor classification
  - furniture class
  - review-required risk tag
  - material family
  - real-size `size.bbox` and `size.footprint` in centimeters
  - support structure features
  - collision plan with collider type, target mesh paths, and auto-apply safety
  - size recommendation
- Applies conservative static collider settings back to USD

## Requirements

- Python 3
- `pxr` USD Python bindings

Quick check:

```bash
python3 -c "from pxr import Usd; print('USD OK')"
```

## Main Scripts

### General Pipeline

- `usd_inspector.py`
- `knowledge_candidate.py`
- `report_to_knowledge_candidate.py`
- `reports_to_csv.py`
- `seed_taxonomy_from_csv.py`
- `build_group_reference_stats.py`

### Static Furniture Pipeline

- `usd_simready_cli.py`
- `static_furniture.py`
- `extract_static_furniture_reference.py`
- `recommend_static_furniture_simready.py`
- `apply_static_furniture_simready.py`
- `smoke_test_static_furniture_runtime.py`
- `compare_reference_recommendations.py`

## Quick Start

### 0. Unified one-step processing

For most assets, use the unified CLI. It generates the recommendation, applies
scale/orientation/collider/resource fixes, and can emit a final inspection
report:

```bash
python3 usd_simready_cli.py process \
  simready_furniture_reference_with_wikidata.json \
  /path/to/new_asset.usd \
  --output /path/to/new_asset.simready_static.usda \
  --emit-report
```

The `process` command writes a self-contained USD package: relative texture and
MDL references, copied dependencies, optional reference scale, optional
orientation correction, and static collision authoring.

### 0.1. Optional Codex Agent Skill

This repository includes a Codex skill for agents that should use the unified
CLI consistently:

```text
codex-skills/usd-simready-cli
```

To install it into a local Codex environment, copy the skill directory into the
Codex skills folder:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
cp -R codex-skills/usd-simready-cli "${CODEX_HOME:-$HOME/.codex}/skills/"
```

After installation, requests such as “process this USD for SimReady validation”
or “fix missing MDL/texture dependencies and validate this USD” should trigger
the skill. The skill expects:

- this repository checkout with `usd_simready_cli.py`
- Python 3 with USD `pxr` bindings available
- `simready_furniture_reference_with_wikidata.json` or another reference JSON
- read access to input USD assets and write access to the output directory

The skill tells agents to run `usd_simready_cli.py process`, then verify the
emitted report for `issues`, missing relative dependencies, orientation, scale,
and collision authoring.

### 1. Inspect a USD asset

```bash
python3 usd_inspector.py asset.usd --pretty --output asset.report.json
```

### 2. Generate a knowledge candidate

```bash
python3 report_to_knowledge_candidate.py asset.report.json --output asset.knowledge_candidate.json
```

### 3. Generate a static furniture recommendation

This repository includes a published furniture reference with SimReady semantic metadata:

- `simready_furniture_reference_with_wikidata.json`

This is now the recommended default reference because it includes authored
SimReady semantic metadata such as `qcodes`, `classes`, `hierarchies`, and
`label_tags`. In practice this improves furniture recovery and makes reference
grouping more stable than the older naming-driven sample reference.

Run:

```bash
python3 recommend_static_furniture_simready.py \
  simready_furniture_reference_with_wikidata.json \
  /path/to/new_asset.usd \
  --output new_asset.static_furniture_recommendation.json
```

### 4. Apply static collider authoring

```bash
python3 apply_static_furniture_simready.py \
  /path/to/new_asset.usd \
  new_asset.static_furniture_recommendation.json \
  --output new_asset.simready_static.usda
```

The authoring step scans all authored USD asset-path fields, including MDL
shader fields such as `info:mdl:sourceAsset`. Resolvable relative dependencies
are copied next to the output USD with the same relative layout. If an upstream
asset references `gltf/pbr.mdl` but omitted the file, the bundled fallback MDL
is written to `gltf/pbr.mdl` in the output directory so downstream validators
can resolve the shader source asset. Other missing relative dependencies still
fail the command unless `--allow-missing-assets` is used. Exported USDA files
are normalized so copied assets, including textures, are authored as relative
paths such as `textures/name.png` instead of machine-local absolute paths.
When the recommendation includes `authoring.apply_reference_scale=true`, the
authoring step also applies `authoring.suggested_uniform_scale` to the default
prim so assets with incorrect source scale are normalized against the trusted
reference library.

### 5. Run a top-drop runtime smoke test

For assets where `recommendation.collision_plan.auto_apply_safe=true` and
`recommendation.review_required=false`, pass the recommendation to
`omni-asset-cli top-drop` with preserve-runtime enabled:

```bash
python3 smoke_test_static_furniture_runtime.py \
  new_asset.static_furniture_recommendation.json \
  --output new_asset.top_drop_smoke.json
```

Use `--dry-run` to inspect the command without invoking `omni-asset-cli`.
If your local CLI uses different argument names, provide `--command-template`;
the template supports `{input}`, `{recommendation}`, and `{output}`.

### 6. Build a reference with SimReady semantic metadata

```bash
python3 extract_static_furniture_reference.py \
  /path/to/simready_assets \
  --recursive \
  --output simready_furniture_reference_with_wikidata.json
```

### 7. Compare old vs new reference recommendations

```bash
python3 compare_reference_recommendations.py \
  legacy/sample_static_furniture_reference.json \
  simready_furniture_reference_with_wikidata.json \
  /path/to/chair.usd \
  /path/to/bottle.usd \
  --output-json reference_recommendation_diff.json \
  --output-csv reference_recommendation_diff.csv
```

## Static Furniture Output

The static furniture recommendation currently focuses on:

- `is_furniture`
- `furniture_class`
- `is_decor`
- `review_required`
- `size.bbox`
- `size.footprint`
- `support_structure`
- `collision_plan`
- `material_family`
- `recommended_collider`
- `size_recommendation`
- `authoring.target_mesh_paths`
- `semantic_metadata`

Current authoring behavior is intentionally conservative:

- applies `PhysicsCollisionAPI`
- applies `PhysicsMeshCollisionAPI`
- writes `physics:collisionEnabled = true`
- writes `physics:approximation = ...`
- only marks recommendations auto-apply safe when size, mesh targets, and Stage 1 classification are suitable for downstream runtime smoke testing

It does not currently auto-author:

- joints
- articulations
- vehicles
- friction, mass, density, restitution tuning

## Semantic Metadata

When SimReady assets contain authored semantic attributes such as:

- `semantic:QWQQ:params:semanticData`
- `semantic:QWQL:params:semanticData`
- `semantic:QWQC:params:semanticData`
- `semantic:LabelTags:params:semanticData`

the pipeline captures them under `semantic_metadata` and uses them as additional
signals for furniture classification and reference grouping.

## Sample Files

Included samples:

- `sample_report.json`
- `sample_knowledge_candidate.json`
- `sample_asset_summary.csv`
- `sample_component_map.csv`
- `sample_candidate_review.csv`
- `simready_furniture_reference_with_wikidata.json`

Legacy baseline kept only for regression comparison and historical reproducibility:

- `legacy/sample_static_furniture_reference.json`

Why the new reference is preferred:

- it contains more assets and more furniture assets
- it captures SimReady semantic metadata authored in the source USD assets
- it improves furniture classification coverage compared with the older sample reference

## Documentation

Additional project docs:

- [PROJECT_PROGRESS_REPORT.md](PROJECT_PROGRESS_REPORT.md)
- [ARCHITECTURE_AND_FLOW.md](ARCHITECTURE_AND_FLOW.md)

## License

MIT
