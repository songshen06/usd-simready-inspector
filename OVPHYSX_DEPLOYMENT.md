# ovphysx Deployment

This project uses `ovphysx` as an optional runtime physics smoke-test backend.
Keep it separate from the main inspector environment.

## Why Separate

The main `usd-simready-inspector` environment uses `usd-core` for PXR-based USD
inspection and authoring. Current `ovphysx` builds have strict OpenUSD runtime
requirements and should not be imported into the same process as the
`usd-core`-based inspector.

The integration therefore uses two processes:

```text
usd_simready_cli.py ovphysx-smoke
  uses the main .venv and PXR to author a temporary smoke-test USD scene

ovphysx_runtime_runner.py
  runs in a separate Python environment with ovphysx installed
  loads the smoke-test scene, steps PhysX, and emits contact/pose JSON
```

## Create the ovphysx Environment

Use a separate virtual environment outside this repository's main `.venv`:

```bash
python3.10 -m venv ~/.venvs/ovphysx
~/.venvs/ovphysx/bin/python -m pip install --upgrade pip
~/.venvs/ovphysx/bin/python -m pip install -r requirements-ovphysx.txt
```

If `python3.10` is not available, use any Python version supported by your
installed `ovphysx` wheel.

Verify the runtime:

```bash
~/.venvs/ovphysx/bin/python -c "import ovphysx, numpy; print(ovphysx.__version__)"
```

## Configure the CLI

Either pass the runtime explicitly:

```bash
.venv/bin/python usd_simready_cli.py ovphysx-smoke \
  out/cup.simready_static.usda \
  --ovphysx-python ~/.venvs/ovphysx/bin/python \
  --output out/cup.ovphysx_smoke.json
```

Or set an environment variable:

```bash
export OVPHYSX_PYTHON="$HOME/.venvs/ovphysx/bin/python"

.venv/bin/python usd_simready_cli.py ovphysx-smoke \
  out/cup.simready_static.usda \
  --output out/cup.ovphysx_smoke.json
```

To isolate whether a failure is caused by the authored mesh collider or by the
runtime setup, run the same check with a temporary bbox proxy collider:

```bash
.venv/bin/python usd_simready_cli.py ovphysx-smoke \
  out/cup.simready_static.usda \
  --asset-collider-mode bbox-proxy \
  --output out/cup.ovphysx_bbox_proxy_smoke.json
```

If `bbox-proxy` passes while `authored` fails, the runtime and drop setup are
working, but the authored collider is too complex, unsupported, or otherwise
not effective in this ovphysx smoke harness.

## Dry Run

Use `--dry-run` to verify scene authoring and the planned runner command before
installing or invoking `ovphysx`:

```bash
.venv/bin/python usd_simready_cli.py ovphysx-smoke \
  out/cup.simready_static.usda \
  --dry-run \
  --output /tmp/cup.ovphysx.dryrun.json
```

The dry-run report includes:

- generated smoke-test scene path
- asset bbox and units
- drop box path and size
- collision filter paths used for contact binding
- exact external runner command

By default, the drop box size is computed in centimeters using the same
expectation-derived rule consumed by `diagnose`, then converted back to stage
units for the generated smoke-test scene. Pass `--box-size` only when you want a
manual stage-unit override.

## Expected Report States

`status = "passed"` means:

- the external `ovphysx` runner imported successfully
- the generated scene loaded
- simulation advanced
- a contact binding was created
- contact force exceeded the configured threshold

`status = "unavailable"` means the external runner could not import or execute
`ovphysx`. This is an environment/deployment problem, not an asset failure.

`status = "failed"` means the runtime executed but the physics smoke test did
not pass. Typical causes include missing/invalid collision schemas, wrong
contact filter paths, unstable collider approximations, or a drop setup that
misses the asset.

## Diagnose With ovphysx Output

`diagnose` can consume the ovphysx report:

```bash
.venv/bin/python usd_simready_cli.py diagnose \
  --recommendation out/cup.recommendation.json \
  --report out/cup.report.json \
  --runtime-report out/cup.ovphysx_smoke.json
```

If the ovphysx runtime is unavailable, `diagnose` records a warning and skips
motion/contact conclusions. If the runtime runs and reports no contact, the
diagnosis treats that as a physics authoring/runtime failure.

## Troubleshooting

- `No module named 'ovphysx'`: `--ovphysx-python` points to the wrong Python
  executable, or `ovphysx` is not installed in that environment.
- `No module named 'numpy'`: install `requirements-ovphysx.txt` into the
  ovphysx environment.
- `scene_authoring_failed`: the input USD could not be opened, lacks a
  `defaultPrim`, is not Z-up, or has no collision prims.
- `contact_not_detected`: inspect the generated scene in the report's
  `scene.scene_path`, then review collider schemas, contact filter paths, drop
  size, and drop height.
- authored collider fails but `--asset-collider-mode bbox-proxy` passes:
  simplify the authored collider, use a lighter proxy collider, or keep bbox
  proxy as the runtime gate for static/decor assets.
- GPU/device issues: use `--device cpu` first. Keep GPU mode for a later
  optimization once CPU smoke tests are stable.

## Role In The Pipeline

Use `ovphysx-smoke` as a fast local runtime gate after `process` or `apply`.
Keep the Isaac Sim Docker smoke test as the heavier downstream compatibility
check when available.
