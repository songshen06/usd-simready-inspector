# USD SimReady Inspector

CLI tools for inspecting USD assets and generating rule-based SimReady recommendations.

This repository currently covers two workflows:

- General USD inspection and `knowledge_candidate` generation
- Static furniture SimReady recommendation and collider authoring

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

This repository includes a published sample furniture reference:

- `sample_static_furniture_reference.json`

Run:

```bash
python3 recommend_static_furniture_simready.py \
  sample_static_furniture_reference.json \
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

## Static Furniture Output

The static furniture recommendation currently focuses on:

- `furniture_class`
- `material_family`
- `size`
- `support_structure`
- `recommended_collider`
- `size_recommendation`
- `authoring.target_mesh_paths`

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

## Sample Files

Included samples:

- `sample_report.json`
- `sample_knowledge_candidate.json`
- `sample_asset_summary.csv`
- `sample_component_map.csv`
- `sample_candidate_review.csv`
- `sample_static_furniture_reference.json`

## Documentation

Additional project docs:

- [PROJECT_PROGRESS_REPORT.md](PROJECT_PROGRESS_REPORT.md)
- [ARCHITECTURE_AND_FLOW.md](ARCHITECTURE_AND_FLOW.md)

## License

MIT
