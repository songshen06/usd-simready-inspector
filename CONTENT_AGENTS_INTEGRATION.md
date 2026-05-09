# NVIDIA Content Agents Integration

## Current Deployment

NVIDIA Content Agents was cloned to:

```text
/home/horde/content-agents
```

The local CLI environment was created with `uv` and Python 3.12:

```bash
/home/horde/.local/bin/uv python install 3.12
/home/horde/.local/bin/uv venv --python 3.12 .venv
/home/horde/.local/bin/uv pip install -e .
/home/horde/.local/bin/uv pip install -e apps/physics_agent
```

Verified:

```bash
/home/horde/content-agents/.venv/bin/physics-agent --help
/home/horde/content-agents/.venv/bin/physics-agent run apps/physics_agent/configs/lightbulb.yaml --dry-run
```

## Docker Service Deployment

The Docker service path was built and started from `/home/horde/content-agents`:

```bash
docker compose --env-file .env \
  -f apps/physics_agent_service/docker-compose.yml up -d --build
```

Verified service health:

```text
physics-agent-service: healthy on http://127.0.0.1:8000
ovrtx-rendering-api: healthy on http://127.0.0.1:8001
OVRTX gpu_initialized=true
GPU: NVIDIA A40, 48 GB class
VLM backend: nim / qwen/qwen3.5-397b-a17b
```

The `.env` file contains the local NVIDIA NIM API key. Do not commit `.env`.

## Service Smoke Test

A self-contained cube USD was submitted to the service:

```text
/home/horde/content-agents/apps/physics_agent_service/tests/test_data/simple_cube.usda
```

Result:

```text
session_id: 349d4286-255a-491c-a38e-362bf3a8b130
status: completed
duration_seconds: 148
prims_processed: 1
images_generated: 4
predictions_made: 1
output_usd: /artifacts/349d4286-255a-491c-a38e-362bf3a8b130/output-usd
```

The downloaded output USD was saved temporarily as:

```text
/tmp/physics_agent_scene_physics.usda
```

Note: the bundled `test_live.sh` lightbulb sample failed because it uploads only
`light_bulb_01.usda`, while that layer references `Payload/Contents.usda`.
When uploaded as a single file, the payload is missing and the service sees zero
mesh prims. Use self-contained USD/USDC/USDZ inputs for service uploads.

## Cup Service Test

The user asset was submitted directly to the running service:

```text
/home/horde/new_3D/cup.usd
```

Result:

```text
session_id: 27188751-e2a9-40f7-8745-37ca4bd64a33
status: completed
duration_seconds: 138
prims_processed: 1
images_generated: 4
predictions_made: 1
identified_asset: French press coffee maker
predicted_component: beaker body
predicted_material: plastic
density: 1100 kg/m^3
raw_unscaled_estimated_mass: 117 kg
mass_for_authoring: null
static_friction: 0.4
dynamic_friction: 0.35
restitution: 0.4
```

Downloaded artifacts:

```text
out/content_physics/cup_service_27188751-e2a9-40f7-8745-37ca4bd64a33/results.json
out/content_physics/cup_service_27188751-e2a9-40f7-8745-37ca4bd64a33/predictions.jsonl
out/content_physics/cup_service_27188751-e2a9-40f7-8745-37ca4bd64a33/report.html
out/content_physics/cup_service_27188751-e2a9-40f7-8745-37ca4bd64a33/scene_physics.usda
```

USD sanity check:

```text
stage_opened: true
traversed_prims: 10
rigid_body_api_prims: 1
collision_api_prims: 1
```

Current portability caveat: the downloaded `scene_physics.usda` may keep service
container texture paths such as `/var/physics-agent/.../textures/...`. Before
treating the service output as a final distributable asset, rewrite or package
those asset references into project-relative paths.

The predictions were also merged back into the existing rule-based cup
recommendation as supplemental review evidence:

```bash
python3 usd_simready_cli.py physics-supplement out/cup.recommendation.json \
  --physics-predictions out/content_physics/cup_service_27188751-e2a9-40f7-8745-37ca4bd64a33/predictions.jsonl \
  --source-usd /home/horde/new_3D/cup.usd \
  --output out/cup.recommendation.with_content_physics.json
```

The rule-based recommendation remains primary. For this cup asset, the merged
output keeps `recommendation.furniture_class=decor` and adds
`supplements.content_agent_physics.rule_constraints`, which fixes the rule-side
class, target size `[8, 8, 10]` cm, 0.04 scale correction, decor mass range
`0.05-1.5 kg`, and collider policy before evaluating VLM output. The same
supplement records `asset_type=appliance` plus plastic material and friction
evidence. The Physics Agent raw mass estimate is retained only as unaccepted
evidence because it was computed from the unscaled source geometry. The merge
sets `mass_for_authoring_kg=null` and adds
`content_agent_mass_from_unscaled_geometry` plus
`content_agent_mass_outlier_for_decor`, because the agent estimated 117 kg from
the observed 2 m source scale while the rule layer already recommends a 0.04
uniform scale.

## usd-simready-inspector Bridge

`content_physics_agent.py` generates a Physics Agent config from the official
`apps/physics_agent/configs/lightbulb.yaml` template and calls the external
`physics-agent` CLI.

Dry run:

```bash
python3 usd_simready_cli.py physics-agent /home/horde/new_3D/cup.usd \
  --output-dir /tmp/usd_si_physics_agent \
  --dry-run
```

Expected summary output:

```text
/tmp/usd_si_physics_agent/cup.physics_agent.summary.json
```

Expected full-run outputs:

```text
<working_dir>/predictions/predictions.jsonl
<working_dir>/predictions/report.html
<working_dir>/physics/<input-stem>_physics.usda
```

## Recommended Integration Shape

Keep Content Agents as an external optional tool instead of vendoring its code.

Use this flow:

1. `usd_simready_cli.py process` creates the static SimReady USD/USDC.
2. `usd_simready_cli.py physics-agent --dry-run` verifies config generation.
3. When VLM and rendering are configured, run without `--dry-run`.
4. Consume the Physics Agent `predictions.jsonl` and `<stem>_physics.usda`.
5. Optionally pass the physics-authored USD into runtime smoke tests.
