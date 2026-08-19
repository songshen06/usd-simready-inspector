# Foundation profile integration deployment

This repository participates in Foundation-profile-dependent workflows but does
not install or execute Foundation profiles itself. `omni-asset-cli` is the
profile executor and evidence owner; this repository is the controlled repair
writer.

## Shared pinned dependency

Deploy NVIDIA SimReady Foundation separately from both project environments.
The currently supported passive cart / physics-prop combination is:

- Foundation tag: `v2026.04.1`
- Foundation commit: `a1e9dd68ee2d107f74dc6cd6da875b54ad3f8fd3`
- Python: 3.12
- validator: `simready-validate==2026.4.8`
- selected profile: `Prop-Robotics-Physx v1.0.0`

```bash
git clone --branch v2026.04.1 https://github.com/NVIDIA/simready-foundation.git \
  ~/simready-foundation-v2026.04.1
git -C ~/simready-foundation-v2026.04.1 rev-parse HEAD
python3.12 -m venv ~/simready-foundation-v2026.04.1/.venv
~/simready-foundation-v2026.04.1/.venv/bin/python -m pip install --upgrade pip
~/simready-foundation-v2026.04.1/.venv/bin/python -m pip install \
  'simready-validate==2026.4.8' 'omniverse-asset-validator>=1.18' numpy
```

Run Foundation only from `omni-asset-cli` in read-only shadow mode. Keep the
Foundation Python 3.12 environment separate from the inspector environment.

```bash
cd ~/omni-asset-cli
.venv/bin/python omni_asset_cli.py foundation-validate INPUT_USD \
  --package physics-prop \
  --foundation-tag v2026.04.1 \
  --foundation-root ~/simready-foundation-v2026.04.1 \
  --foundation-python ~/simready-foundation-v2026.04.1/.venv/bin/python \
  --official-cli --shadow --out out/asset_foundation
```

## Inspector handoff and acceptance boundary

Pass the profile name/tag/commit, source asset SHA, and normalized findings to
the Inspector repair step. For `RB.COL.002`, the Inspector can create a new
candidate USD, never overwrite the source, and preserve `CollisionAPI`, visual
geometry, transforms, rigid bodies, and joints. Re-run the same Foundation
profile and `omni-asset-cli physics-collider-audit` on the candidate.

Do not treat any of the following as another:

- Foundation profile result: upstream authoring conformance.
- `RB.COL.003`: local warning that convex cooking can diverge from visual mesh.
- PhysX viewport or A/B contact result: runtime evidence of actual collision
  clearance or an air-wall.

The full host, Docker, and runtime deployment instructions remain in
`~/omni-asset-cli/DEPLOYMENT.md`.
