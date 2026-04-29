---
name: usd-simready-cli
description: Use when processing USD/USDZ/USDA/USDC assets with the usd-simready-inspector repository, especially to create self-contained SimReady-style static asset exports, fix missing MDL/texture dependencies, normalize relative asset paths, apply reference-based scale, correct Y-up/Z-up or lying-down geometry orientation, author static collision, and validate output reports.
---

# USD SimReady CLI

Use the repository's unified CLI first:

```bash
python3 usd_simready_cli.py process REF_JSON INPUT_USD \
  --output OUTPUT_USDA \
  --emit-report
```

Run commands from the `usd_inspect` repository root unless the user gives another checkout path. The default reference is usually:

```text
simready_furniture_reference_with_wikidata.json
```

## Dependencies

Before running the workflow, verify these requirements:

- A checkout of `usd-simready-inspector` containing `usd_simready_cli.py`.
- Python 3 in an environment where `from pxr import Usd` works.
- The reference JSON, usually `simready_furniture_reference_with_wikidata.json`.
- Read access to the input USD/USDZ/USDA/USDC and its sidecar assets.
- Write access to the output directory so copied textures, `gltf/pbr.mdl`, recommendation JSON, and report JSON can be emitted.
- Optional: Omniverse Asset Validator or `omni-asset-cli` if the user asks for downstream validation beyond the repository's local report.

If `pxr` is missing, stop and tell the user the USD Python bindings are required; do not fabricate report results.

## Main Workflow

1. Confirm the input path exists and identify an output path. Prefer a `.simready_static.usda` output under the user's requested output directory.
2. Run `usd_simready_cli.py process` with the reference JSON, input USD, output USDA, and `--emit-report`.
3. Read the emitted report and verify:
   - `issues` is empty or explain remaining issues.
   - `asset_dependencies.missing_relative_count == 0`.
   - All asset dependencies are relative when portability is required.
   - `stage.up_axis` is `Z` for downstream SimReady/Omniverse workflows unless the user requested otherwise.
   - `geometry.bbox.world.size` has plausible dimensions for the semantic class.
   - Physics collision was authored on intended mesh targets.
4. Report the output USD, recommendation JSON, report JSON, and the key validation facts.

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
python3 usd_simready_cli.py apply INPUT_USD RECOMMENDATION_JSON --output OUTPUT_USDA
```

One-step process:

```bash
python3 usd_simready_cli.py process REF_JSON INPUT_USD \
  --output OUTPUT_USDA \
  --recommendation-output RECOMMENDATION_JSON \
  --report-output REPORT_JSON \
  --emit-report
```

## What Process Fixes

The `process` command can:

- Generate a recommendation from the trusted reference library.
- Copy resolvable texture and asset dependencies next to the output.
- Bundle fallback `gltf/pbr.mdl` when source assets reference it but omit it.
- Rewrite exported USDA asset paths to relative paths such as `textures/name.png`.
- Apply `authoring.suggested_uniform_scale` when `apply_reference_scale=true`.
- Apply orientation correction when `apply_orientation_correction=true`, including Y-up to Z-up conversion and lying-down geometry fixes.
- Author static collision using the recommended USD approximation.

## Interpreting Common Results

- `missing_relative_count > 0`: output is not self-contained; inspect `asset_dependencies.missing_relative`.
- `orientation_recommendation.apply=true`: geometry orientation is corrected during apply; verify final bbox has height on Z.
- `size_recommendation.status=scale`: source scale is corrected from the reference library.
- `review_required=true`: do not claim the asset is automatically safe; explain `review_reasons`.
- `auto_apply_safe=true`: the recommendation is suitable for automatic static authoring under current rules.

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
