"""Profile-gated repair: only touch what the current profile actually rejects.

Repairs are licensed by requirement codes, not by heuristics. A repairer runs
only when the validator reported that code as failing for the profile under
test; everything else is reported as unsupported or unlicensed and left alone.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Set

from pxr import Usd, UsdGeom, UsdPhysics

NAMESPACE_PREFIXES = ("com.nvidia.simready.", "com.nvidia.usd.")
PRIM_PATTERN = re.compile(r"Prim '([^']+)'")

# code -> what this tool is willing to change
REPAIRERS: Dict[str, Dict[str, str]] = {
    "RB.COL.001": {
        "action": "remove_collision_api_from_non_gprim",
        "description": "CollisionAPI on a prim that is not a UsdGeom GPrim",
    },
    "RB.COL.002": {
        "action": "remove_mesh_collision_api_from_non_mesh",
        "description": "MeshCollisionAPI on a prim that is not a UsdGeom Mesh",
    },
}


def normalize_code(code: str) -> str:
    for prefix in NAMESPACE_PREFIXES:
        if code.startswith(prefix):
            return code[len(prefix):]
    return code


def _messages_by_code(feature_body: Dict[str, Any]) -> Dict[str, List[str]]:
    raw = feature_body.get("requirement_messages")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    if not isinstance(raw, dict):
        return {}
    return {normalize_code(str(code)): [str(item) for item in values]
            for code, values in raw.items()}


def derive_findings(conformance: Dict[str, Any]) -> Dict[str, Any]:
    """Turn validator output into repair findings, one per (code, prim) pair."""
    findings: List[Dict[str, Any]] = []
    seen: Set[tuple] = set()
    for feature_id, body in (conformance.get("features") or {}).items():
        if not body.get("blocking_failure"):
            continue
        for code, messages in _messages_by_code(body).items():
            if code not in REPAIRERS:
                continue
            for message in messages:
                match = PRIM_PATTERN.search(message)
                if not match:
                    continue
                key = (code, match.group(1))
                if key in seen:
                    continue
                seen.add(key)
                findings.append({
                    "rule_id": code,
                    "prim": match.group(1),
                    "feature": feature_id,
                    "message": message,
                    "repairable": True,
                })
    return {"findings": findings}


def plan(conformance: Dict[str, Any], findings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Split findings into licensed / unlicensed / unsupported buckets."""
    derived = findings or derive_findings(conformance)
    failing_codes = set()
    for feature_id, body in (conformance.get("features") or {}).items():
        if body.get("blocking_failure"):
            failing_codes.update(_messages_by_code(body).keys())

    allowed, unlicensed, unsupported = [], [], []
    for finding in derived.get("findings", []):
        code = normalize_code(str(finding.get("rule_id")))
        if code not in REPAIRERS:
            unsupported.append({**finding, "rule_id": code, "reason": "no repairer for this requirement"})
        elif code not in failing_codes:
            unlicensed.append({**finding, "rule_id": code, "reason": "requirement not failing under this profile"})
        else:
            allowed.append({**finding, "rule_id": code, "action": REPAIRERS[code]["action"]})

    return {
        "allowed": allowed,
        "unlicensed": unlicensed,
        "unsupported": unsupported,
        "failing_codes": sorted(failing_codes),
        "repairable_codes": sorted({item["rule_id"] for item in allowed}),
        "blocked_codes": sorted({item["rule_id"] for item in unsupported}),
    }


def apply(input_usd: str, actions: List[Dict[str, Any]], output_usd: str) -> Dict[str, Any]:
    """Apply the licensed repairs and export a candidate asset."""
    input_path = os.path.abspath(input_usd)
    output_path = os.path.abspath(output_usd)
    if input_path == output_path:
        raise ValueError("output must differ from input; source assets are never modified")

    stage = Usd.Stage.Open(input_path)
    if stage is None:
        raise ValueError(f"could not open input USD: {input_path}")

    applied: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for action in actions:
        code = action["rule_id"]
        prim = stage.GetPrimAtPath(action["prim"])
        if not prim or not prim.IsValid():
            skipped.append({"rule_id": code, "prim": action["prim"], "reason": "prim not found"})
            continue

        removed: List[str] = []
        if code == "RB.COL.001" and not prim.IsA(UsdGeom.Gprim):
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                prim.RemoveAPI(UsdPhysics.CollisionAPI)
                removed.append("PhysicsCollisionAPI")
        elif code == "RB.COL.002" and not prim.IsA(UsdGeom.Mesh):
            if prim.HasAPI(UsdPhysics.MeshCollisionAPI):
                prim.RemoveAPI(UsdPhysics.MeshCollisionAPI)
                prim.RemoveProperty("physics:approximation")
                removed.append("PhysicsMeshCollisionAPI")
                removed.append("physics:approximation")

        if removed:
            applied.append({"rule_id": code, "prim": action["prim"],
                            "prim_type": str(prim.GetTypeName()), "removed": removed})
        else:
            skipped.append({"rule_id": code, "prim": action["prim"],
                            "reason": "already compliant or prim type changed"})

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if not stage.GetRootLayer().Export(output_path):
        raise ValueError(f"failed to export repaired USD: {output_path}")

    return {
        "schema_version": "1.0",
        "operation": "profile-gated-repair",
        "input_usd": input_path,
        "output_usd": output_path,
        "applied": applied,
        "skipped": skipped,
        "repairable_rules": sorted(REPAIRERS),
    }


# ---------------------------------------------------------------- packaging helpers
def candidate_layout(root: str, asset_stem: str) -> Dict[str, str]:
    """Standard SimReady layout: <root>/<asset>/simready_usd/<asset>.usda + .thumbs."""
    asset_dir = os.path.join(os.path.abspath(root), asset_stem)
    usd_dir = os.path.join(asset_dir, "simready_usd")
    thumb_dir = os.path.join(usd_dir, ".thumbs", "256x256")
    return {
        "asset_dir": asset_dir,
        "usd_dir": usd_dir,
        "usd_path": os.path.join(usd_dir, f"{asset_stem}.usda"),
        "thumb_dir": thumb_dir,
        "thumb_path": os.path.join(thumb_dir, f"{asset_stem}.usda.png"),
    }


def _is_png(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(8) == b"\x89PNG\r\n\x1a\n"
    except OSError:
        return False


def thumbnail_candidates(input_usd: str, explicit: Optional[str] = None) -> List[str]:
    """Places a thumbnail may already exist, most explicit first."""
    stem = os.path.splitext(os.path.basename(input_usd))[0]
    usd_dir = os.path.dirname(os.path.abspath(input_usd))
    asset_dir = os.path.dirname(usd_dir)
    options = [explicit] if explicit else []
    options += [
        os.path.join(usd_dir, ".thumbs", "256x256", f"{stem}.png"),
        os.path.join(usd_dir, ".thumbs", "256x256", f"{stem}.usda.png"),
        os.path.join(asset_dir, ".thumbs", "256x256", f"{stem}.usda.png"),
        os.path.join(usd_dir, "thumbnail.png"),
    ]
    return [path for path in options if path and os.path.isfile(path)]


def install_thumbnail(input_usd: str, destination: str,
                      explicit: Optional[str] = None) -> Dict[str, Any]:
    """Copy an existing thumbnail into the candidate layout.

    A thumbnail is never fabricated: if no representative image exists the
    repair reports ``unavailable`` and SR.002 stays failing.
    """
    for source in thumbnail_candidates(input_usd, explicit):
        if not _is_png(source):
            continue
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copyfile(source, destination)
        return {"status": "installed", "source": source, "destination": destination}
    return {
        "status": "unavailable",
        "reason": "no PNG thumbnail found; pass --thumbnail or render one downstream",
        "checked": thumbnail_candidates(input_usd, explicit) or ["<none>"],
    }


# ---------------------------------------------------------------- thumbnail rendering
_BLENDER_SCRIPT = r'''
import bpy, sys, math
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
asset, out_path, size = argv[0], argv[1], int(argv[2])

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.wm.usd_import(filepath=asset)

points = []
for obj in bpy.context.scene.objects:
    if obj.type == "MESH":
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
if not points:
    raise SystemExit("no geometry to render")

low = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
high = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
center = (low + high) / 2
radius = max((high - low).length / 2, 0.05)

scene = bpy.context.scene
scene.render.engine = "BLENDER_WORKBENCH"
scene.display.shading.light = "STUDIO"
scene.display.shading.color_type = "MATERIAL"
scene.display.shading.show_shadows = True
scene.display.shading.show_cavity = True
scene.render.resolution_x = scene.render.resolution_y = size
scene.render.film_transparent = False
scene.world = bpy.data.worlds.new("W")
scene.world.color = (0.82, 0.82, 0.82)

cam_data = bpy.data.cameras.new("cam")
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
cam.location = center + Vector((1.0, -1.35, 0.75)).normalized() * radius * 3.2
direction = center - cam.location
cam.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
cam_data.lens = 52

scene.render.image_settings.file_format = "PNG"
scene.render.filepath = out_path
bpy.ops.render.render(write_still=True)
'''


def _png_dimensions(path: str):
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
        if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            return None
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        return width, height
    except OSError:
        return None


def _renderers(blender: Optional[str] = None, usdrecord: Optional[str] = None):
    found = []
    for exe in (usdrecord, os.environ.get("USDRECORD"), shutil.which("usdrecord"), "/usr/bin/usdrecord"):
        if exe and os.path.isfile(exe) and os.access(exe, os.X_OK):
            found.append(("usdrecord", exe))
            break
    blender_candidates = (
        blender,
        os.environ.get("BLENDER_BIN"),
        shutil.which("blender"),
        "/Applications/Blender.app/Contents/MacOS/Blender",
    )
    for exe in blender_candidates:
        if exe and os.path.isfile(exe) and os.access(exe, os.X_OK):
            found.append(("blender", exe))
            break
    return found


def render_thumbnail(asset: str, destination: str, *, size: int = 256,
                     blender: Optional[str] = None, usdrecord: Optional[str] = None,
                     timeout: int = 300) -> Dict[str, Any]:
    """Render a thumbnail with the first available renderer.

    A renderer failure is reported, never replaced by a placeholder image.
    """
    errors: List[Dict[str, Any]] = []
    renderers = _renderers(blender, usdrecord)
    if not renderers:
        return {"status": "unavailable", "reason": "no renderer found (usdrecord or Blender)"}

    os.makedirs(os.path.dirname(destination), exist_ok=True)
    for kind, executable in renderers:
        temp_png = destination + f".{kind}.tmp.png"
        if os.path.exists(temp_png):
            os.remove(temp_png)
        try:
            if kind == "usdrecord":
                command = [executable, "--imageWidth", str(size), asset, temp_png]
            else:
                script = destination + ".blender_thumb.py"
                with open(script, "w", encoding="utf-8") as handle:
                    handle.write(_BLENDER_SCRIPT)
                command = [executable, "-b", "--factory-startup", "--python", script,
                           "--", asset, temp_png, str(size)]
            subprocess.run(command, check=False, capture_output=True, text=True, timeout=timeout)
            dimensions = _png_dimensions(temp_png)
            if dimensions and os.path.getsize(temp_png) > 512:
                os.replace(temp_png, destination)
                return {"status": "rendered", "renderer": kind, "executable": executable,
                        "destination": destination,
                        "dimensions": {"width": dimensions[0], "height": dimensions[1]}}
            errors.append({"renderer": kind, "reason": "no usable PNG produced"})
        except (OSError, subprocess.SubprocessError) as error:
            errors.append({"renderer": kind, "reason": str(error)})
        finally:
            if os.path.exists(temp_png):
                os.remove(temp_png)
    return {"status": "unavailable", "reason": "all renderers failed", "errors": errors}
