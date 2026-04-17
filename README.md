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
  - furniture class
  - material family
  - size features
  - support structure features
  - collider recommendation
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

- `static_furniture.py`
- `extract_static_furniture_reference.py`
- `recommend_static_furniture_simready.py`
- `apply_static_furniture_simready.py`
- `compare_reference_recommendations.py`

## Quick Start

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

### 5. Build a reference with SimReady semantic metadata

```bash
python3 extract_static_furniture_reference.py \
  /path/to/simready_assets \
  --recursive \
  --output simready_furniture_reference_with_wikidata.json
```

### 6. Compare old vs new reference recommendations

```bash
python3 compare_reference_recommendations.py \
  sample_static_furniture_reference.json \
  simready_furniture_reference_with_wikidata.json \
  /path/to/chair.usd \
  /path/to/bottle.usd \
  --output-json reference_recommendation_diff.json \
  --output-csv reference_recommendation_diff.csv
```

## Static Furniture Output

The static furniture recommendation currently focuses on:

- `furniture_class`
- `material_family`
- `size`
- `support_structure`
- `recommended_collider`
- `size_recommendation`
- `authoring.target_mesh_paths`
- `semantic_metadata`

Current authoring behavior is intentionally conservative:

- applies `PhysicsCollisionAPI`
- applies `PhysicsMeshCollisionAPI`
- writes `physics:collisionEnabled = true`
- writes `physics:approximation = ...`

It does not currently auto-author:

- joints
- articulations
- vehicles
- friction, mass, density, restitution tuning
- geometry rescaling

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
- `sample_static_furniture_reference.json`
- `simready_furniture_reference_with_wikidata.json`

## Documentation

Additional project docs:

- [PROJECT_PROGRESS_REPORT.md](PROJECT_PROGRESS_REPORT.md)
- [ARCHITECTURE_AND_FLOW.md](ARCHITECTURE_AND_FLOW.md)

## License

MIT
