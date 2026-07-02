# Proxy Collider Notes

`proxy-collider` authors a lightweight bbox collider into an existing USD asset.
It is intended for static/decor assets where the visual mesh is too dense or
too irregular to be a reliable runtime collider.

## Why

The cup test showed this exact pattern:

- `out/cup.simready_static.usda` has a high-resolution visual mesh with
  `PhysicsCollisionAPI` and `PhysicsMeshCollisionAPI`.
- `ovphysx-smoke --asset-collider-mode authored` loaded the scene and advanced
  simulation, but the drop box passed through the authored mesh collider.
- `ovphysx-smoke --asset-collider-mode bbox-proxy` passed with strong contact
  force, proving the runtime setup was valid and the authored mesh collider was
  the weak point.

For static/decor SimReady checks, a simple proxy collider is often a better
runtime gate than a very high-poly visual mesh collider.

## Command

```bash
.venv/bin/python usd_simready_cli.py proxy-collider \
  out/cup.simready_static.usda \
  --output out/cup.simready_static.proxy_collider.usda \
  --report out/cup.simready_static.proxy_collider.report.json
```

By default this:

- keeps the visual asset unchanged
- adds `/World/PhysicsProxy/BBoxCollider`
- marks the proxy as `purpose = "proxy"`
- applies `PhysicsCollisionAPI` to the proxy cube
- sets `simready:proxyCollider = true`
- disables existing authored colliders so the proxy is the active runtime
  collider

Use `--keep-authored-colliders` only when you intentionally want both the visual
mesh collider and the proxy collider enabled.

## Verify

Run the authored-collider smoke test against the proxy output:

```bash
.venv/bin/python usd_simready_cli.py ovphysx-smoke \
  out/cup.simready_static.proxy_collider.usda \
  --ovphysx-python /home/horde/.venvs/ovphysx/bin/python \
  --asset-collider-mode authored \
  --output out/cup.proxy_collider.ovphysx.json
```

Expected result:

```text
status = passed
checks.contact_detected = true
```

## Tradeoff

A bbox proxy is conservative. It is good for fast static/decor validation and
for proving contact wiring works, but it does not capture concavity or fine
shape details. For assets that need accurate physical interaction, replace the
bbox with a small set of simpler shape proxies or a vetted low-poly collider.
