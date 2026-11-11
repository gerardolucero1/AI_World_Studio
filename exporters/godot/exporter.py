"""Godot 4 exporter: chunked terrain GLBs (LOD + collision), water, cliff geometry, vegetation MultiMeshes,
materials, textures, World.tscn, manifest and validation. Target: Godot 4.7.x (scene format 3)."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from core.world.session import WorldSession
from exporters.godot.glb import write_glb
from exporters.godot.terrain_lod import chunk_mesh, lod_steps_for, pick_chunk_cells
from features.curves.curve import sample_curve
from biomes.masks.masks import water_mask
from materials.materials import COLORS, MATERIALS, final_weights

EXPORT_FORMAT_VERSION = 1
from exporters.godot.godot_bin import find_godot

GODOT_VERSION = "4.7.2"
GENERATED = ["Terrain", "Terrain3D", "Features", "Geometry", "Vegetation", "Materials", "Textures", "WorldData", "Collisions", "Navigation"]
MAX_INSTANCES = 300_000
LIBRARY = Path(__file__).parent / "library"
GREEN_TINTS = [((1.0, 1.0, 1.0), 0.52), ((1.25, 1.15, 0.45), 0.17), ((1.6, 0.85, 0.35), 0.11), ((0.88, 1.05, 0.95), 0.20)]   # autumn mix
# species -> baked meshes + per-instance colour palette (multiplies vertex colours) + visibility distance
SPECIES_LIBRARY = {
    "oak": dict(meshes=["round_tree_a", "round_tree_b", "round_tree_c"], material="foliage", tints=GREEN_TINTS, dist=900.0),
    "pine": dict(meshes=["pine_a", "pine_b"], material="foliage", tints=[((1, 1, 1), 0.7), ((0.92, 1.0, 0.9), 0.3)], dist=1100.0),
    "shrub": dict(meshes=["bush_a", "bush_b"], material="foliage", tints=[((1, 1, 1), 0.6), ((1.25, 1.1, 0.7), 0.25), ((1.0, 0.85, 1.1), 0.15)], dist=450.0),
    "reed": dict(meshes=["bush_b"], material="foliage", tints=[((1.3, 1.25, 0.7), 1.0)], dist=260.0),
    "rock": dict(meshes=["rock_a", "rock_b", "rock_c", "rock_d"], material="prop_rock", tints=[((1, 1, 1), 0.7), ((0.9, 0.96, 1.1), 0.3)], dist=900.0),
}
SPECIES_STYLE = {   # fallback primitives for unknown species names: (mesh kind, color, visible distance m)
    "blob": ("blob", (0.25, 0.5, 0.15), 350.0)}
DEFAULT_STYLE = ("cone", (0.2, 0.45, 0.2), 500.0)


@dataclass
class ExportSettings:
    chunk_cells: int | None = None                 # None = auto (64/32/16/...)
    lod_steps: tuple[int, ...] = (1, 2, 4, 8)
    lod_distance_chunks: tuple[float, ...] = (2.5, 5.0, 10.0)   # switch distances, in chunk sizes
    collision_step: int = 1                        # collision mesh = every Nth vertex of the chunk. 1 = same resolution as the visual/navigation mesh
                                                   # (coarser steps make carved trails bumpy: a character can get stuck on them); 2-4 for huge worlds
    res_prefix: str = "res://"                     # use e.g. "res://GodotWorld/" when embedding in an existing project
    write_project_file: bool = True
    max_instances: int = MAX_INSTANCES
    include_preview: bool = True                   # also write Preview.tscn (sun + sky + camera around World.tscn) for a quick look
    profile: str = "full"                          # "full" (Forward+/Mobile: depth-fade water, glow) | "compatible" (also runs on the Compatibility renderer)
    grass: bool = True                             # tall grass: GPU-placed tufts (one shared patch per chunk)
    grass_spacing: float = 1.5                     # metres between tufts (denser = more vertices near the camera)
    grass_distance: float = 68.0                   # tufts shrink to nothing between ~55% and 100% of this distance
    markers: bool = True                           # PlayerStart marker + trails/rivers as Path3D nodes
    navigation: str = "none"                       # "bake" (Godot's Recast baker, needs the Godot binary; reliable) | "python" (coarse mesh built here; narrow
                                                   # trails can disconnect) | "auto" (bake if Godot is found, else none) | "none"
    godot_binary: str | None = None                # for navigation bake; default: see godot_bin.find_godot
    nav_cell_m: float = 4.0                        # python navmesh cell
    nav_bake_cell: float | None = None             # Recast voxel size in m for the bake (None = auto: world/1500 clamped to 0.5..2); smaller = narrower trails stay connected, slower bake
    nav_max_slope_deg: float = 35.0                # walkable slope for the bake: margin below a character's floor angle (typically 45)
    terrain: str = "mesh"                          # "mesh" (GLB chunks + LOD + collision, no addon needed) | "terrain3d" (Terrain3D node + regions;
                                                   # needs the Terrain3D addon: in the host project, $AWS_TERRAIN3D_ADDON or terrain3d_addon)
    terrain3d_addon: str | None = None             # folder of the Terrain3D addon to bake with (default: the host project's addons/terrain_3d)
    terrain3d_texture_ids: dict | None = None      # our material -> host texture slot, e.g. {"grass": 0, "rock": 1, ...}; None = our 8 textures in order
    terrain3d_material: str | None = None          # res:// path of the host game's Terrain3DMaterial (e.g. its toon shader); None = Terrain3D/material.tres
    terrain3d_assets: str | None = None            # res:// path of the host game's Terrain3DAssets (texture list); None = Terrain3D/assets.tres
    terrain3d_collision: str = "full"              # "full" (whole terrain, works for NPCs anywhere) | "dynamic" (around the camera; cheaper on big maps)
    terrain3d_collision_layer: int = 1


SHADER_DIR = Path(__file__).parent / "shaders"
SHADER_FILES = ["common.gdshaderinc", "terrain.gdshader", "foliage.gdshader", "rock.gdshader", "water.gdshader", "water_simple.gdshader", "waterfall.gdshader", "grass.gdshader", "sky.gdshader"]


def _xform(pos, direction, look_dir: bool = True) -> str:
    """Godot Transform3D text for a node looking along `direction` (its -Z axis), positioned at `pos` (None = origin)."""
    z = -np.asarray(direction, np.float64); z /= np.linalg.norm(z)
    up = np.array([0.0, 1.0, 0.0]) if abs(z[1]) < 0.99 else np.array([0.0, 0.0, 1.0])
    x = np.cross(up, z); x /= np.linalg.norm(x); y = np.cross(z, x)
    o = np.zeros(3) if pos is None else np.asarray(pos, np.float64)
    vals = [x[0], y[0], z[0], x[1], y[1], z[1], x[2], y[2], z[2], *o]       # rows of the basis (axes are the columns)
    return "Transform3D(" + ", ".join(f"{v:.5g}" for v in vals) + ")"


def _color(c) -> str:
    return f"Color({c[0]:.4f}, {c[1]:.4f}, {c[2]:.4f}, 1)"


def _sha1(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


class _Tscn:
    """Tiny helper that collects ext/sub resources and nodes for a .tscn."""

    def __init__(self, prefix: str):
        self.prefix, self.ext, self.sub, self.nodes = prefix, [], [], []

    def ext_res(self, rid: str, rtype: str, rel_path: str) -> str:
        self.ext.append(f'[ext_resource type="{rtype}" path="{self.prefix}{rel_path}" id="{rid}"]')
        return rid

    def sub_res(self, rid: str, rtype: str, props: list[str]) -> str:
        self.sub.append(f'[sub_resource type="{rtype}" id="{rid}"]\n' + "\n".join(props) + "\n")
        return rid

    def node(self, name: str, ntype: str | None, parent: str | None, props: list[str] = (), instance: str | None = None,
             index: int | None = None, groups: list[str] | None = None) -> None:
        head = f'[node name="{name}"'
        if ntype:
            head += f' type="{ntype}"'
        if parent is not None:
            head += f' parent="{parent}"'
        if index is not None:
            head += f' index="{index}"'
        if instance:
            head += f' instance=ExtResource("{instance}")'
        if groups:
            head += ' groups=[' + ", ".join(f'"{g}"' for g in groups) + ']'
        self.nodes.append(head + "]\n" + "\n".join(props) + ("\n" if props else ""))

    def text(self) -> str:
        steps = len(self.ext) + len(self.sub) + 1
        return (f"[gd_scene load_steps={steps} format=3]\n\n" + "\n".join(self.ext) + "\n\n" + "\n".join(self.sub) + "\n" + "\n".join(self.nodes))


# ------------------------------------------------------------------------------------------------ materials
def _write_materials(out: Path, hf, prefix: str, profile: str = "full", grass_distance: float = 68.0) -> None:
    """Stylized materials (see exporters/godot/shaders). Shaders reference each other through the project prefix."""
    sub = prefix[len("res://"):] if prefix.startswith("res://") else prefix
    for f in SHADER_FILES:
        (out / "Materials" / f).write_text((SHADER_DIR / f).read_text().replace("res://AWS_PREFIX/", f"res://{sub}"))
    sx, sz = hf.size
    sh = lambda name, rid: f'[ext_resource type="Shader" path="{prefix}Materials/{name}" id="{rid}"]'
    tex = lambda i: f'[ext_resource type="Texture2D" path="{prefix}Textures/splat{i}.png" id="t{i}"]'
    (out / "Materials/terrain.tres").write_text(
        f'[gd_resource type="ShaderMaterial" load_steps=5 format=3]\n\n{sh("terrain.gdshader", "sh")}\n{tex(0)}\n{tex(1)}\n{tex(2)}\n\n'
        f'[resource]\nshader = ExtResource("sh")\nshader_parameter/splat0 = ExtResource("t0")\nshader_parameter/splat1 = ExtResource("t1")\n'
        f'shader_parameter/splat2 = ExtResource("t2")\nshader_parameter/world_size = Vector2({sx}, {sz})\n')
    (out / "Materials/rock.tres").write_text(
        f'[gd_resource type="ShaderMaterial" load_steps=2 format=3]\n\n{sh("rock.gdshader", "sh")}\n\n[resource]\nshader = ExtResource("sh")\n'
        'shader_parameter/tint = Color(0.50, 0.54, 0.62, 1)\n')
    (out / "Materials/foliage.tres").write_text(
        f'[gd_resource type="ShaderMaterial" load_steps=2 format=3]\n\n{sh("foliage.gdshader", "sh")}\n\n[resource]\nshader = ExtResource("sh")\n')
    (out / "Materials/prop_rock.tres").write_text(
        f'[gd_resource type="ShaderMaterial" load_steps=2 format=3]\n\n{sh("rock.gdshader", "sh")}\n\n[resource]\nshader = ExtResource("sh")\n')
    (out / "Materials/water.tres").write_text(
        f'[gd_resource type="ShaderMaterial" load_steps=2 format=3]\n\n{sh("water_simple.gdshader" if profile == "compatible" else "water.gdshader", "sh")}\n\n[resource]\nshader = ExtResource("sh")\n')
    (out / "Materials/waterfall.tres").write_text(
        f'[gd_resource type="ShaderMaterial" load_steps=2 format=3]\n\n{sh("waterfall.gdshader", "sh")}\n\n[resource]\nshader = ExtResource("sh")\n')
    if hf is not None:
        lo, hi = float(hf.data.min()), float(hf.data.max())
        (out / "Materials/grass.tres").write_text(
            f'[gd_resource type="ShaderMaterial" load_steps=5 format=3]\n\n{sh("grass.gdshader", "sh")}\n'
            f'[ext_resource type="Texture2D" path="{prefix}Textures/height_rg.tres" id="th"]\n{tex(0)}\n{tex(2)}\n\n'
            f'[resource]\nshader = ExtResource("sh")\nshader_parameter/height_tex = ExtResource("th")\nshader_parameter/splat0 = ExtResource("t0")\n'
            f'shader_parameter/splat2 = ExtResource("t2")\nshader_parameter/world_size = Vector2({sx}, {sz})\nshader_parameter/tex_size = Vector2({hf.nx}, {hf.ny})\n'
            f'shader_parameter/cell_size = {hf.cell_size}\nshader_parameter/h_min = {lo}\nshader_parameter/h_max = {hi}\n'
            f'shader_parameter/fade_start = {0.55 * grass_distance:.1f}\nshader_parameter/fade_end = {grass_distance:.1f}\n')
    (out / "Materials/sky.tres").write_text(
        f'[gd_resource type="ShaderMaterial" load_steps=2 format=3]\n\n{sh("sky.gdshader", "sh")}\n\n[resource]\nshader = ExtResource("sh")\n')


def _write_textures(out: Path, hf, log):
    """Splat maps (3 opaque RGB PNGs; blue of the 3rd = water mask), 16-bit heightmaps, and a lossless RG8 height texture for the grass shader.
    Returns (info, weights, water_mask)."""
    import base64
    w = final_weights(hf, log)
    wm = water_mask(hf, log)
    pad = np.concatenate([w, wm[..., None].astype(w.dtype)], -1)              # channel 8 = water mask (blue of splat2)
    for i in range(3):
        arr = (np.clip(pad[..., i * 3:i * 3 + 3], 0, 1) * 255 + 0.5).astype(np.uint8)
        Image.fromarray(arr, "RGB").save(out / f"Textures/splat{i}.png")
    lo, hi = float(hf.data.min()), float(hf.data.max())
    norm = (hf.data - lo) / max(hi - lo, 1e-6)
    h16 = (norm * 65535).astype(np.uint16)
    Image.fromarray(h16).save(out / "Textures/heightmap16.png")
    h16.astype("<u2").tofile(out / "Textures/heightmap.r16")                 # raw little-endian 16-bit: importable by terrain addons
    # RG8 (R = high byte, G = low byte) as an in-file ImageTexture: no import step, so Godot can never recompress it lossily
    rg = np.stack([(h16 >> 8).astype(np.uint8), (h16 & 255).astype(np.uint8)], -1)
    b64 = base64.b64encode(rg.tobytes()).decode()
    (out / "Textures/height_rg.tres").write_text(
        '[gd_resource type="ImageTexture" format=3]\n\n[sub_resource type="Image" id="Image_h"]\ndata = {\n"data": PackedByteArray("' + b64 + '"),\n'
        f'"format": "RedGreen",\n"height": {hf.ny},\n"mipmaps": false,\n"width": {hf.nx}\n}}\n\n[resource]\nimage = SubResource("Image_h")\n')
    info = {"heightmap": {"file": "Textures/heightmap16.png", "raw_r16": "Textures/heightmap.r16", "size": [hf.nx, hf.ny], "min": lo, "max": hi, "bits": 16,
                          "note": "height = min + v/65535*(max-min); the .r16 is raw little-endian rows, for terrain addons (e.g. Terrain3D/HTerrain import)"}}
    return info, w, wm


# ------------------------------------------------------------------------------------------------ water meshes
def _lake_mesh(r: dict):
    poly = r["polygon"]
    outline = sample_curve(poly + [poly[0]], 3.0)[:-1]
    c = outline.mean(0)
    pos = np.vstack([[c[0], r["level"], c[1]], np.column_stack([outline[:, 0], np.full(len(outline), r["level"]), outline[:, 1]])])
    n = len(outline)
    idx = np.array([[0, 1 + (i + 1) % n, 1 + i] for i in range(n)], np.uint32)     # CCW seen from above
    f = idx.reshape(-1, 3)
    if np.cross(pos[f[0, 1]] - pos[f[0, 0]], pos[f[0, 2]] - pos[f[0, 0]])[1] < 0:
        idx = idx[:, [0, 2, 1]]
    return pos, np.tile([0, 1, 0], (len(pos), 1)), idx.reshape(-1)


def _river_mesh(r: dict):
    """Water ribbon along the course, interrupted where the river falls over a cliff (those parts are waterfall sheets)."""
    pts = np.asarray(r["points"], np.float64); lvl = np.asarray(r["water_level"], np.float64)
    t = np.gradient(pts, axis=0); t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-9)
    nrm = np.stack([-t[:, 1], t[:, 0]], -1) * (r["width"] * 0.5)
    left, right = pts + nrm, pts - nrm
    pos = np.vstack([np.column_stack([left[:, 0], lvl, left[:, 1]]), np.column_stack([right[:, 0], lvl, right[:, 1]])])
    n = len(pts)
    in_fall = np.zeros(n - 1, bool)
    for a, e in r.get("fall_segments", []):
        in_fall[a:e] = True
    tris = []
    for i in range(n - 1):
        if not in_fall[i]:
            tris += [[i, n + i, i + 1], [i + 1, n + i, n + i + 1]]
    if not tris:
        return None
    tris = np.array(tris, np.uint32)
    if np.cross(pos[tris[0, 1]] - pos[tris[0, 0]], pos[tris[0, 2]] - pos[tris[0, 0]])[1] < 0:
        tris = tris[:, [0, 2, 1]]
    return pos, np.tile([0, 1, 0], (len(pos), 1)), tris.reshape(-1)


def _fall_mesh(f: dict, rows: int = 14):
    """Waterfall sheet from the lip to the pool: a strip that bulges away from the cliff face. UV: x across, y from lip (0) to pool (1)."""
    top, bot = np.asarray(f["top"], np.float64), np.asarray(f["bottom"], np.float64)
    d = (bot - top)[[0, 2]]
    d = d / np.linalg.norm(d) if np.linalg.norm(d) > 0.2 else np.array([0.0, 1.0])
    nrm = np.array([-d[1], d[0]]) * f["width"] * 0.42
    ts = np.linspace(0, 1, rows + 1)
    out = 0.45 + 0.9 * np.sin(np.pi * ts) * min(f["height"] / 20.0, 1.0)         # leaves the lip, arcs outward, hugs the pool
    cx = top[0] + (bot[0] - top[0]) * ts + d[0] * out
    cz = top[2] + (bot[2] - top[2]) * ts + d[1] * out
    cy = top[1] + (bot[1] - top[1]) * ts
    pos = np.vstack([np.column_stack([cx + nrm[0], cy, cz + nrm[1]]), np.column_stack([cx - nrm[0], cy, cz - nrm[1]])])
    uv = np.vstack([np.column_stack([np.zeros(rows + 1), ts]), np.column_stack([np.ones(rows + 1), ts])])
    nn = rows + 1
    tris = []
    for i in range(rows):
        tris += [[i, nn + i, i + 1], [i + 1, nn + i, nn + i + 1]]
    tris = np.array(tris, np.uint32)
    nrmv = np.tile([d[0], 0.0, d[1]], (len(pos), 1))
    return pos, nrmv, uv, tris.reshape(-1)


# ------------------------------------------------------------------------------------------------ main
def export_world(session: WorldSession, out_dir: str | Path, settings: ExportSettings | None = None) -> dict:
    s = settings or ExportSettings()
    out = Path(out_dir)
    if s.terrain not in ("mesh", "terrain3d"):
        raise ValueError("terrain must be 'mesh' or 'terrain3d'")
    t3d_godot = t3d_addon = None
    if s.terrain == "terrain3d":
        from exporters.godot.terrain3d import find_terrain3d_addon
        t3d_godot = _find_godot(s.godot_binary)
        t3d_addon = find_terrain3d_addon(out, s.terrain3d_addon)
        if t3d_godot is None:
            raise ValueError("terrain='terrain3d' needs the Godot binary to bake the regions (set godot_binary or $AWS_GODOT)")
        if t3d_addon is None:
            raise ValueError("terrain='terrain3d' needs the Terrain3D addon: install it in the Godot project (addons/terrain_3d), "
                             "or pass terrain3d_addon / set $AWS_TERRAIN3D_ADDON")
    with session.lock:
        hf, log = session.hf, list(session.op_log)
        if out.exists() and any(out.iterdir()):
            if not (out / "WorldData/manifest.json").exists():
                raise ValueError(f"{out} is not empty and is not a previous AI World Studio export; refusing to overwrite it")
            for d in GENERATED:
                shutil.rmtree(out / d, ignore_errors=True)
            (out / "World.tscn").unlink(missing_ok=True)
        for d in ["Terrain", "Features/Rivers", "Features/Lakes", "Features/Waterfalls", "Features/Paths", "Geometry", "Vegetation",
                  "Materials", "Textures", "WorldData"]:
            (out / d).mkdir(parents=True, exist_ok=True)

        cells = s.chunk_cells or pick_chunk_cells(hf)
        if (hf.nx - 1) % cells or (hf.ny - 1) % cells:
            raise ValueError(f"chunk_cells={cells} does not divide the heightfield ({hf.nx - 1}x{hf.ny - 1} cells)")
        steps = lod_steps_for(cells, s.lod_steps)
        ncx, ncy = (hf.nx - 1) // cells, (hf.ny - 1) // cells
        chunk_m = cells * hf.cell_size
        ranges = [chunk_m * f for f in s.lod_distance_chunks][: len(steps) - 1]
        sx, sz = hf.size
        P = s.res_prefix

        files: list[str] = []
        stats = {"triangles_lod0": 0, "chunks": ncx * ncy}
        tex, weights, wmask = _write_textures(out, hf, log); _write_materials(out, hf, P, s.profile, s.grass_distance)
        files += ["Textures/splat0.png", "Textures/splat1.png", "Textures/splat2.png", "Textures/heightmap16.png", "Textures/heightmap.r16", "Textures/height_rg.tres", "Materials/grass.tres", "Materials/terrain.tres", "Materials/rock.tres", "Materials/water.tres", "Materials/sky.tres", "Materials/foliage.tres", "Materials/prop_rock.tres", "Materials/waterfall.tres",
                  *[f"Materials/{f}" for f in SHADER_FILES]]

        T = _Tscn(P)
        T.ext_res("mat_terrain", "Material", "Materials/terrain.tres")
        T.ext_res("mat_rock", "Material", "Materials/rock.tres")
        T.ext_res("mat_water", "Material", "Materials/water.tres")
        T.ext_res("mat_waterfall", "Material", "Materials/waterfall.tres")
        T.node("World", "Node3D", None)
        T.node("Terrain", "Node3D", ".")

        # ---- terrain: Terrain3D regions (baked by Godot with the real addon) or our GLB chunks
        t3d_info: dict = {}
        if s.terrain == "terrain3d":
            from exporters.godot.terrain3d import bake_terrain3d, copy_addon
            t3d_info = bake_terrain3d(out, hf, weights, wmask, t3d_godot, t3d_addon, P, s.terrain3d_texture_ids)
            if not t3d_info.get("ok"):
                raise RuntimeError(f"Terrain3D bake failed: {t3d_info.get('error')}\n{t3d_info.get('tail', '')}")
            files += [f.relative_to(out).as_posix() for f in (out / "Terrain3D").rglob("*") if f.is_file()]
            if s.terrain3d_material and s.terrain3d_material.endswith(".tscn"):     # the game's material lives inside a scene: copy it out
                from exporters.godot.terrain3d import extract_material, host_project_root
                host = host_project_root(out)
                src = host / s.terrain3d_material[len("res://"):] if host else None
                if src is None or not src.exists():
                    raise ValueError(f"terrain3d_material {s.terrain3d_material} not found in the Godot project of {out}")
                mt = extract_material(src)
                mt = re.sub(r"(?m)^world_background = .*\n", "", mt) + "world_background = 0\n"
                (out / "Terrain3D/material.tres").write_text(mt, encoding="utf-8")       # no infinite ground plane around a generated world
                T.ext_res("t3d_mat", "Terrain3DMaterial", "Terrain3D/material.tres")
            elif s.terrain3d_material:
                T.ext.append(f'[ext_resource type="Terrain3DMaterial" path="{s.terrain3d_material}" id="t3d_mat"]')
            else:
                T.ext_res("t3d_mat", "Terrain3DMaterial", "Terrain3D/material.tres")
            if s.terrain3d_assets:
                T.ext.append(f'[ext_resource type="Terrain3DAssets" path="{s.terrain3d_assets}" id="t3d_assets"]')
            else:
                T.ext_res("t3d_assets", "Terrain3DAssets", "Terrain3D/assets.tres")
            T.node("Terrain3D", "Terrain3D", "Terrain", [
                f"region_size = {t3d_info['region_size']}", f"vertex_spacing = {hf.cell_size}", 'material = ExtResource("t3d_mat")',
                'assets = ExtResource("t3d_assets")', f'data_directory = "{P}Terrain3D/data"',
                f"collision_mode = {3 if s.terrain3d_collision == 'full' else 1}", f"collision_layer = {int(s.terrain3d_collision_layer)}"])
            if s.write_project_file and not (out / "addons/terrain_3d").exists():      # standalone project: ship the addon so it opens/runs
                copy_addon(t3d_addon, out, all_platforms=True)
        for cy in range(ncy if s.terrain == "mesh" else 0):
            for cx in range(ncx):
                name = f"Chunk_{cx:02d}_{cy:02d}"
                nodes = []
                for k, st in enumerate(steps):
                    p, n, uv, i = chunk_mesh(hf, cx, cy, cells, st, True)
                    nodes.append(dict(name=f"Terrain_LOD{k}", positions=p, normals=n, uvs=uv, indices=i, material="TerrainSurface"))
                    if k == 0:
                        stats["triangles_lod0"] += len(i) // 3
                cp, cn, _, ci = chunk_mesh(hf, cx, cy, cells, max(s.collision_step, 1) if cells % s.collision_step == 0 else 1, False)
                nodes.append(dict(name="Collision-colonly", positions=cp, normals=cn, uvs=None, indices=ci, material=None))
                rel = f"Terrain/{name}.glb"
                write_glb(out / rel, nodes); files.append(rel)
                rid = T.ext_res(f"c{cx}_{cy}", "PackedScene", rel)
                T.node(name, None, "Terrain", instance=rid)
                for k in range(len(steps)):
                    props = ['material_override = ExtResource("mat_terrain")']
                    if k > 0:
                        props.append(f"visibility_range_begin = {ranges[k - 1]:.1f}")
                    if k < len(steps) - 1:
                        props.append(f"visibility_range_end = {ranges[k]:.1f}")
                    T.node(f"Terrain_LOD{k}", None, f"Terrain/{name}", props, index=k, groups=["aws_nav_src"] if k == 0 else None)

        # ---- features & geometry
        T.node("Features", "Node3D", "."); T.node("Geometry", "Node3D", ".")
        counts = {"lakes": 0, "rivers": 0, "cliffs": 0}
        for op in log:
            r = op.result
            if not (op.enabled and r):
                continue
            if op.kind == "lake":
                pos, nrm, idx = _lake_mesh(r); nm = f"Lake_{counts['lakes']}"; rel = f"Features/Lakes/{nm}.glb"
                write_glb(out / rel, [dict(name="Water", positions=pos, normals=nrm, uvs=None, indices=idx, material="Water")]); files.append(rel)
                T.node(nm, None, "Features", instance=T.ext_res(f"lake{counts['lakes']}", "PackedScene", rel))
                T.node("Water", None, f"Features/{nm}", ['material_override = ExtResource("mat_water")'], index=0)
                counts["lakes"] += 1
            elif op.kind == "river":
                nm = f"River_{counts['rivers']}"; rel = f"Features/Rivers/{nm}.glb"
                T.node(nm, "Node3D", "Features")
                m = _river_mesh(r)
                if m is not None:
                    pos, nrm, idx = m
                    write_glb(out / rel, [dict(name="Water", positions=pos, normals=nrm, uvs=None, indices=idx, material="Water")]); files.append(rel)
                    T.node("Surface", None, f"Features/{nm}", instance=T.ext_res(f"river{counts['rivers']}", "PackedScene", rel))
                    T.node("Water", None, f"Features/{nm}/Surface", ['material_override = ExtResource("mat_water")'], index=0)
                for k, f in enumerate(r.get("falls", [])):
                    pos, nrm, uv, idx = _fall_mesh(f)
                    frel = f"Features/Rivers/{nm}_Fall_{k}.glb"
                    write_glb(out / frel, [dict(name="Sheet", positions=pos, normals=nrm, uvs=uv, indices=idx, material="Waterfall")]); files.append(frel)
                    T.node(f"Fall_{k}", None, f"Features/{nm}", instance=T.ext_res(f"{nm}_fall{k}", "PackedScene", frel))
                    T.node("Sheet", None, f"Features/{nm}/Fall_{k}", ['material_override = ExtResource("mat_waterfall")'], index=0)
                    counts["waterfalls"] = counts.get("waterfalls", 0) + 1
                counts["rivers"] += 1
            elif op.kind == "cliff_plateau" and "_mesh" in r:
                m = r["_mesh"]; nm = f"Cliff_{counts['cliffs']}"; rel = f"Geometry/{nm}.glb"
                write_glb(out / rel, [dict(name="Wall", positions=m.vertices, normals=m.vertex_normals(), uvs=m.uvs, indices=m.faces, material="Rock"),
                                      dict(name="Wall-colonly", positions=m.vertices, normals=None, uvs=None, indices=m.faces, material=None)])
                files.append(rel)
                T.node(nm, None, "Geometry", instance=T.ext_res(f"cliff{counts['cliffs']}", "PackedScene", rel))
                T.node("Wall", None, f"Geometry/{nm}", ['material_override = ExtResource("mat_rock")'], index=0)
                counts["cliffs"] += 1

        # ---- vegetation: MultiMeshInstance3D per (species, mesh variant, chunk); per-instance colour for variety
        T.node("Vegetation", "Node3D", ".")
        by_species: dict[str, list[np.ndarray]] = {}
        for op in log:
            r = op.result
            if not (op.enabled and op.kind == "scatter_vegetation" and r and "_instances" in r):
                continue
            inst = r["_instances"]
            for sid, spd in enumerate(op.params.get("species", [])):
                sel = inst[inst[:, 6].astype(int) == sid]
                if len(sel):
                    by_species.setdefault(spd.get("name", f"species{sid}"), []).append(sel)
        veg_summary: dict[str, int] = {}
        total_inst = 0
        (out / "Vegetation/Meshes").mkdir(parents=True, exist_ok=True)
        mesh_ids: set[str] = set(); mat_ids: set[str] = set(); prim_made: set[str] = set()
        for sname, parts in by_species.items():
            sel = np.vstack(parts)
            lib = SPECIES_LIBRARY.get(sname)
            rng = np.random.default_rng(sum(map(ord, sname)))   # deterministic per species name
            cxs = np.minimum((sel[:, 0] / chunk_m).astype(int), ncx - 1); cys = np.minimum((sel[:, 2] / chunk_m).astype(int), ncy - 1)
            if lib and all((LIBRARY / f"{m}.res").exists() for m in lib["meshes"]):
                for m in lib["meshes"]:
                    if m not in mesh_ids:
                        shutil.copy(LIBRARY / f"{m}.res", out / f"Vegetation/Meshes/{m}.res")
                        T.ext_res(f"mesh_{m}", "ArrayMesh", f"Vegetation/Meshes/{m}.res"); mesh_ids.add(m); files.append(f"Vegetation/Meshes/{m}.res")
                if lib["material"] not in mat_ids:
                    T.ext_res(f"mat_{lib['material']}", "Material", f"Materials/{lib['material']}.tres"); mat_ids.add(lib["material"])
                tints = np.array([t for t, _ in lib["tints"]], np.float32); tw = np.array([w for _, w in lib["tints"]], np.float64); tw /= tw.sum()
                variant = rng.integers(0, len(lib["meshes"]), len(sel))
                tint = tints[rng.choice(len(tints), len(sel), p=tw)] * rng.uniform(0.9, 1.08, (len(sel), 1)).astype(np.float32)
                for key in sorted(set(zip(cxs.tolist(), cys.tolist()))):
                    in_chunk = (cxs == key[0]) & (cys == key[1])
                    for vi, mname in enumerate(lib["meshes"]):
                        sel_idx = np.flatnonzero(in_chunk & (variant == vi))
                        if not len(sel_idx):
                            continue
                        g = sel[sel_idx]; tg = tint[sel_idx]
                        th = np.radians(g[:, 3]); c, sn = np.cos(th), np.sin(th)
                        rx, hy = g[:, 4] * 2.0, g[:, 5]                      # prototype meshes: diameter 1, height 1, base at y = 0
                        buf = np.zeros((len(g), 16), np.float32)
                        buf[:, 0], buf[:, 2], buf[:, 3] = c * rx, sn * rx, g[:, 0]
                        buf[:, 5], buf[:, 7] = hy, g[:, 1] - hy * 0.04
                        buf[:, 8], buf[:, 10], buf[:, 11] = -sn * rx, c * rx, g[:, 2]
                        buf[:, 12:15], buf[:, 15] = tg, 1.0
                        rid = f"mm_{sname}_{mname}_{key[0]}_{key[1]}"
                        arr = ", ".join(f"{v:.4g}" for v in buf.reshape(-1))
                        T.sub_res(rid, "MultiMesh", ["transform_format = 1", "use_colors = true", f"instance_count = {len(g)}",
                                                    f'mesh = ExtResource("mesh_{mname}")', f"buffer = PackedFloat32Array({arr})"])
                        T.node(f"{sname}_{mname}_{key[0]:02d}_{key[1]:02d}", "MultiMeshInstance3D", "Vegetation",
                               [f'multimesh = SubResource("{rid}")', f'material_override = ExtResource("mat_{lib["material"]}")',
                                f"visibility_range_end = {lib['dist']:.0f}.0"])
                        total_inst += len(g)
            else:                                                            # unknown species: coloured primitive
                kind, color, dist = SPECIES_STYLE.get(sname, DEFAULT_STYLE)
                if sname not in prim_made:
                    prim_made.add(sname)
                    T.sub_res(f"vmat_{sname}", "StandardMaterial3D", [f"albedo_color = {_color(color)}", "roughness = 0.9"])
                    if kind == "cone":
                        T.sub_res(f"vmesh_{sname}", "CylinderMesh", ["top_radius = 0.0", "bottom_radius = 1.0", "height = 1.0", "radial_segments = 8",
                                                                    "rings = 1", f'material = SubResource("vmat_{sname}")'])
                    else:
                        T.sub_res(f"vmesh_{sname}", "SphereMesh", ["radius = 0.5", "height = 1.0", "radial_segments = 8", "rings = 4",
                                                                  f'material = SubResource("vmat_{sname}")'])
                diam = 1.0 if kind == "cone" else 2.0
                for key in sorted(set(zip(cxs.tolist(), cys.tolist()))):
                    g = sel[(cxs == key[0]) & (cys == key[1])]
                    th = np.radians(g[:, 3]); c, sn = np.cos(th), np.sin(th)
                    rx, hy = g[:, 4] * diam, g[:, 5]
                    buf = np.zeros((len(g), 12), np.float32)
                    buf[:, 0], buf[:, 2], buf[:, 3] = c * rx, sn * rx, g[:, 0]
                    buf[:, 5], buf[:, 7] = hy, g[:, 1] + hy * 0.5
                    buf[:, 8], buf[:, 10], buf[:, 11] = -sn * rx, c * rx, g[:, 2]
                    rid = f"mm_{sname}_{key[0]}_{key[1]}"
                    arr = ", ".join(f"{v:.4g}" for v in buf.reshape(-1))
                    T.sub_res(rid, "MultiMesh", ["transform_format = 1", f"instance_count = {len(g)}", f'mesh = SubResource("vmesh_{sname}")',
                                                f"buffer = PackedFloat32Array({arr})"])
                    T.node(f"{sname}_{key[0]:02d}_{key[1]:02d}", "MultiMeshInstance3D", "Vegetation",
                           [f'multimesh = SubResource("{rid}")', f"visibility_range_end = {dist:.0f}.0"])
                    total_inst += len(g)
            veg_summary[sname] = veg_summary.get(sname, 0) + int(len(sel))

        # ---- tall grass: one shared patch of tufts per chunk; the shader places them on the ground (see grass.gdshader)
        grass_info: dict = {}
        if s.grass and (LIBRARY / "grass_tuft.res").exists():
            from materials.materials import IDX
            shutil.copy(LIBRARY / "grass_tuft.res", out / "Vegetation/Meshes/grass_tuft.res"); files.append("Vegetation/Meshes/grass_tuft.res")
            T.ext_res("mesh_grass_tuft", "ArrayMesh", "Vegetation/Meshes/grass_tuft.res"); T.ext_res("mat_grass", "Material", "Materials/grass.tres")
            T.node("Grass", "Node3D", ".")
            n_side = max(int(chunk_m / s.grass_spacing), 4)
            variants = []
            for v in range(3):                                   # 3 patches with different jitter so the pattern does not visibly tile
                rng = np.random.default_rng(1000 + v)
                gi, gj = np.meshgrid(np.arange(n_side), np.arange(n_side))
                gx = (gi.ravel() + rng.random(gi.size)) * chunk_m / n_side - chunk_m / 2
                gz = (gj.ravel() + rng.random(gj.size)) * chunk_m / n_side - chunk_m / 2
                yaw = rng.uniform(0, 2 * np.pi, gx.size); c, sn = np.cos(yaw), np.sin(yaw)
                buf = np.zeros((gx.size, 12), np.float32)
                buf[:, 0], buf[:, 2], buf[:, 3], buf[:, 5] = c, sn, gx, 1.0
                buf[:, 8], buf[:, 10], buf[:, 11] = -sn, c, gz
                arr = ", ".join(f"{q:.3g}" for q in buf.reshape(-1))
                T.sub_res(f"grass_patch_{v}", "MultiMesh", ["transform_format = 1", f"instance_count = {gx.size}", 'mesh = ExtResource("mesh_grass_tuft")',
                                                          f"buffer = PackedFloat32Array({arr})"])
                variants.append(f"grass_patch_{v}")
            covered = (weights[..., IDX["grass"]] > 0.5) & ~wmask
            gnodes = 0
            for cy in range(ncy):
                for cx in range(ncx):
                    sl = (slice(cy * cells, min((cy + 1) * cells + 1, hf.ny)), slice(cx * cells, min((cx + 1) * cells + 1, hf.nx)))
                    if covered[sl].mean() < 0.04:
                        continue
                    hseg = hf.data[sl]; y0 = float(hseg.min()) - 1.0; hh = float(hseg.max() - hseg.min()) + 4.0
                    T.node(f"Grass_{cx:02d}_{cy:02d}", "MultiMeshInstance3D", "Grass", [
                        f"transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {(cx + 0.5) * chunk_m:.1f}, 0, {(cy + 0.5) * chunk_m:.1f})",
                        f'multimesh = SubResource("{variants[(cx + cy * 3) % 3]}")', 'material_override = ExtResource("mat_grass")',
                        f"custom_aabb = AABB({-chunk_m / 2:.1f}, {y0:.1f}, {-chunk_m / 2:.1f}, {chunk_m:.1f}, {hh:.1f}, {chunk_m:.1f})",
                        f"visibility_range_end = {s.grass_distance + chunk_m * 0.75:.0f}.0", "cast_shadow = 0"])
                    gnodes += 1
            grass_info = {"chunks_with_grass": gnodes, "tufts_per_chunk": n_side * n_side, "spacing_m": s.grass_spacing, "fade_distance_m": s.grass_distance,
                          "note": "tufts are placed by Materials/grass.gdshader from Textures/height_rg.tres + splat maps; no per-tuft data is stored"}

        # ---- navigation: walkable mesh (Python-built NavigationMesh)
        nav_info: dict = {}
        if s.navigation == "python":
            from exporters.godot.navigation import build_navmesh, navmesh_tres
            (out / "Navigation").mkdir(exist_ok=True)
            nv, nq, nav_info = build_navmesh(hf, log, s.nav_cell_m, s.nav_max_slope_deg)
            if nav_info["polygons"]:
                (out / "Navigation/navmesh.tres").write_text(navmesh_tres(nv, nq)); files.append("Navigation/navmesh.tres")
                T.ext_res("navmesh", "NavigationMesh", "Navigation/navmesh.tres")
                T.node("Navigation", "NavigationRegion3D", ".", ['navigation_mesh = ExtResource("navmesh")'])

        # ---- markers + paths: things the host game can use (spawn point, NPC routes, river courses)
        marker_info: dict = {}
        if s.markers:
            T.node("Markers", "Node3D", ".")
            try:
                from features.paths.routing import traversability
                trav = traversability(hf, log)
                sxy = trav["start"]
                T.node("PlayerStart", "Marker3D", "Markers", [f"transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {sxy[0]:.2f}, {hf.sample(*sxy) + 1.0:.2f}, {sxy[1]:.2f})"])
                marker_info["player_start"] = [sxy[0], hf.sample(*sxy), sxy[1]]
                sm = hf.style.get("summit")
                marker_info["summit"] = ([sm[0], hf.sample(*sm), sm[1]] if sm else [trav["summit"]["x"], trav["summit"]["height"], trav["summit"]["y"]])
                if sm:
                    T.node("Summit", "Marker3D", "Markers", [f"transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, {sm[0]:.2f}, {hf.sample(*sm) + 1.0:.2f}, {sm[1]:.2f})"])
            except Exception:  # noqa: BLE001
                pass
            T.node("Paths", "Node3D", ".")
            n_trail = n_river = 0
            for op in log:
                r = op.result
                if not (op.enabled and r and op.kind in ("path", "river") and "points" in r):
                    continue
                pts = np.asarray(r["points"], np.float64)
                ys = np.asarray(r.get("elevation") or r.get("water_level") or [hf.sample(x, z) for x, z in pts], np.float64)
                step_i = max(len(pts) // 60, 1)                       # keep curves light
                idx = sorted(set(range(0, len(pts), step_i)) | {len(pts) - 1})
                flat = []
                for i in idx:
                    flat += [0, 0, 0, 0, 0, 0, pts[i][0], ys[i], pts[i][1]]
                nm = f"Trail_{n_trail}" if op.kind == "path" else f"River_{n_river}"
                n_trail += op.kind == "path"; n_river += op.kind == "river"
                cid = f"curve_{nm}"
                T.sub_res(cid, "Curve3D", ['_data = {\n"points": PackedVector3Array(' + ", ".join(f"{v:.3f}" for v in flat) + '),\n"tilts": PackedFloat32Array(' + ", ".join(["0"] * len(idx)) + ")\n}", f"point_count = {len(idx)}"])
                T.node(nm, "Path3D", "Paths", [f'curve = SubResource("{cid}")'])
            marker_info["trails"] = n_trail; marker_info["rivers"] = n_river
        (out / "World.tscn").write_text(T.text())

        # ---- optional environment resources + Preview.tscn (sun, sky, camera around the world)
        top = float(hf.data.max()); mean_h = float(hf.data.mean()); size = max(sx, sz)
        if s.include_preview:
            (out / "Environment").mkdir(exist_ok=True)
            (out / "Environment/stylized_environment.tres").write_text(
                '[gd_resource type="Environment" load_steps=3 format=3]\n\n'
                f'[ext_resource type="Material" path="{P}Materials/sky.tres" id="sky_mat"]\n\n'
                '[sub_resource type="Sky" id="sky"]\nsky_material = ExtResource("sky_mat")\n\n[resource]\n'
                'background_mode = 2\nsky = SubResource("sky")\nambient_light_source = 3\nambient_light_energy = 0.95\ntonemap_mode = 2\n'
                + ("glow_enabled = true\nglow_intensity = 0.55\nglow_bloom = 0.04\n" if s.profile == "full" else "")
                + "adjustment_enabled = true\nadjustment_contrast = 1.06\nadjustment_saturation = 1.18\n")
            files.append("Environment/stylized_environment.tres")
            PT = _Tscn(P)
            PT.ext_res("world", "PackedScene", "World.tscn"); PT.ext_res("env", "Environment", "Environment/stylized_environment.tres")
            PT.node("Preview", "Node3D", None)
            PT.node("World", None, ".", instance="world")
            sun_dir = np.array([-0.5, -0.62, -0.6]); sun_dir /= np.linalg.norm(sun_dir)
            PT.node("Sun", "DirectionalLight3D", ".", [f"transform = {_xform(None, sun_dir, look_dir=True)}", "light_color = Color(1, 0.96, 0.88, 1)",
                                                       "light_energy = 1.5", "shadow_enabled = true", "directional_shadow_mode = 2",
                                                       "directional_shadow_max_distance = 700.0", "shadow_blur = 1.5"])
            PT.node("WorldEnvironment", "WorldEnvironment", ".", ['environment = ExtResource("env")'])
            cam_pos = np.array([sx * 0.5, mean_h + size * 0.17 + (top - mean_h) * 0.3, sz * 0.97]); cam_tgt = np.array([sx * 0.5, mean_h, sz * 0.5])
            PT.node("Camera3D", "Camera3D", ".", [f"transform = {_xform(cam_pos, cam_tgt - cam_pos, look_dir=True)}", "fov = 55.0", f"far = {size * 6:.0f}.0"])
            (out / "Preview.tscn").write_text(PT.text()); files.append("Preview.tscn")

        # ---- manifest & project file
        manifest = {
            "format_version": EXPORT_FORMAT_VERSION, "generator": "AI World Studio", "godot_version": GODOT_VERSION,
            "world": {"name": session.name, "size_m": [sx, sz], "resolution": [hf.nx, hf.ny], "cell_size_m": hf.cell_size,
                      "min_height": float(hf.data.min()), "max_height": float(hf.data.max()), "awp_revision": session.revision},
            "coordinates": "Godot axes: X east, Y up, Z south. World origin = north-west corner. Meters.",
            "chunks": {"cells": cells, "size_m": chunk_m, "grid": [ncx, ncy], "lod_steps": steps, "lod_switch_distance_m": ranges,
                       "collision_step": s.collision_step, "naming": "Terrain/Chunk_<x>_<y>.glb (Terrain_LOD0..N + Collision-colonly)"},
            "terrain": ({"mode": "mesh"} if s.terrain == "mesh" else {
                "mode": "terrain3d", "addon_version": t3d_info.get("addon_version"), "region_size": t3d_info.get("region_size"),
                "vertex_spacing": hf.cell_size, "regions": t3d_info.get("region_files"), "data_directory": "Terrain3D/data",
                "texture_ids": s.terrain3d_texture_ids or {m: i for i, m in enumerate(MATERIALS)},
                "material": ("Terrain3D/material.tres" if not s.terrain3d_material or s.terrain3d_material.endswith(".tscn") else s.terrain3d_material),
                "material_source": s.terrain3d_material or "generated", "assets": s.terrain3d_assets or "Terrain3D/assets.tres",
                "collision": s.terrain3d_collision, "collision_layer": s.terrain3d_collision_layer,
                "note": "Terrain3D control map: base = dominant material, overlay = 2nd, blend = overlay share; navigation bit set outside water. "
                        "To use your game's look, point the Terrain3D node's material/assets to yours (or export with terrain3d_material/assets "
                        "and terrain3d_texture_ids)."}),
            "textures": tex, "materials": {"surface_order": MATERIALS, "splat0": "grass,dirt,rock (RGB)", "splat1": "snow,sand,mud (RGB)", "splat2": "wet_rock,gravel (RG)"},
            "features": counts, "grass": grass_info or "disabled", "vegetation": {"species": veg_summary, "total_instances": total_inst,
                                               "representation": "MultiMeshInstance3D per species per chunk (placeholder meshes)"},
            "stats": stats, "res_prefix": P, "profile": s.profile, "markers": marker_info, "navigation": "disabled",
            "usage": {"drop_in_scene": "World.tscn (world only: terrain, features, geometry, vegetation, markers, paths)", "quick_look_scene": "Preview.tscn (adds sun, sky, camera)",
                      "optional_environment": "Environment/stylized_environment.tres", "relocate": "python awstudio.py relocate <folder> --prefix res://new/path/"},
        }
        files += ["World.tscn"]
        (out / "WorldData/manifest.json").write_text(json.dumps(manifest, indent=2))
        if s.write_project_file and not (out / "project.godot").exists():
            (out / "project.godot").write_text(
                f'; Engine configuration file.\nconfig_version=5\n\n[application]\n\nconfig/name="{session.name}"\n'
                f'run/main_scene="res://Preview.tscn"\nconfig/features=PackedStringArray("4.7")\n\n[rendering]\n\n'
                f'renderer/rendering_method="forward_plus"\n'
                + ('\n[editor_plugins]\n\nenabled=PackedStringArray("res://addons/terrain_3d/plugin.cfg")\n' if s.terrain == "terrain3d" else ""))
        # ---- navigation: real Recast bake in Godot (reliable for narrow trails), when requested/available
        if s.navigation in ("auto", "bake"):
            gb = _find_godot(s.godot_binary)
            if gb is None:
                if s.navigation == "bake":
                    raise ValueError("navigation='bake' needs the Godot binary (set godot_binary or $AWS_GODOT)")
                nav_info = {"mode": "none", "note": "Godot binary not found: navigation not baked. Re-export with navigation='bake' or bake in the editor."}
            else:
                nav_info = bake_navigation(out, gb, size, s.nav_max_slope_deg, cell=s.nav_bake_cell, addon=t3d_addon)
                if nav_info.get("ok"):
                    T.ext_res("navmesh", "NavigationMesh", "Navigation/navmesh.res")
                    T.node("Navigation", "NavigationRegion3D", ".", ['navigation_mesh = ExtResource("navmesh")'])
                    (out / "World.tscn").write_text(T.text()); files.append("Navigation/navmesh.res")
        manifest["navigation"] = nav_info or "disabled"
        (out / "WorldData/manifest.json").write_text(json.dumps(manifest, indent=2))
        # checksums (after everything is written)
        manifest["files"] = {f: {"bytes": (out / f).stat().st_size, "sha1": _sha1(out / f)} for f in sorted(set(files)) if (out / f).exists()}
        (out / "WorldData/manifest.json").write_text(json.dumps(manifest, indent=2))
        report = validate_export(out, s)
        manifest["validation"] = report
        (out / "WorldData/manifest.json").write_text(json.dumps(manifest, indent=2))
        return {"ok": True, "path": str(out), "terrain": s.terrain, "chunks": ncx * ncy if s.terrain == "mesh" else 0, "lod_steps": steps, "files": len(files),
                "features": counts, "vegetation_instances": total_inst, "validation": report}


# ------------------------------------------------------------------------------------------------ validation
def validate_export(out_dir: str | Path, settings: ExportSettings | None = None) -> dict:
    """Export checks from the plan: chunks in bounds, collisions, materials, duplicates, instances, broken references."""
    out = Path(out_dir); s = settings or ExportSettings()
    findings: list[dict] = []

    def add(sev, code, msg):
        findings.append({"severity": sev, "code": code, "message": msg})

    mf_path = out / "WorldData/manifest.json"
    if not mf_path.exists():
        return {"ok": False, "findings": [{"severity": "error", "code": "no_manifest", "message": "WorldData/manifest.json missing"}]}
    mf = json.loads(mf_path.read_text())
    ncx, ncy = mf["chunks"]["grid"]
    t3d = mf.get("terrain", {}).get("mode") == "terrain3d"
    if t3d:
        if not list((out / "Terrain3D/data").glob("terrain3d*.res")):
            add("error", "missing_terrain3d_regions", "Terrain3D/data has no region files")
        for f in (mf["terrain"].get("material"), mf["terrain"].get("assets")):
            if f and not f.startswith("res://") and not (out / f).exists():
                add("error", "missing_terrain3d_resource", f"{f} missing")
    for cy in range(ncy if not t3d else 0):
        for cx in range(ncx):
            p = out / f"Terrain/Chunk_{cx:02d}_{cy:02d}.glb"
            if not p.exists():
                add("error", "missing_chunk", f"{p.name} missing")
            elif b"Collision-colonly" not in p.read_bytes()[:200000]:
                add("error", "missing_collision", f"{p.name} has no collision node")
    for m in ("terrain.tres", "terrain.gdshader", "rock.tres", "water.tres", "waterfall.tres", "sky.tres", "foliage.gdshader", "common.gdshaderinc"):
        if not (out / "Materials" / m).exists():
            add("error", "missing_material", f"Materials/{m} missing")
    for t in ("splat0.png", "splat1.png", "splat2.png"):
        if not (out / "Textures" / t).exists():
            add("error", "missing_texture", f"Textures/{t} missing")
    # broken references in tscn/tres
    prefix = mf.get("res_prefix", "res://")
    for f in [out / "World.tscn", *(out / "Materials").glob("*.tres"), *(out / "Terrain3D").glob("*.tres")]:
        for m in re.finditer(r'path="([^"]+)"', f.read_text()):
            rel = m.group(1)
            if rel.startswith(prefix) and not (out / rel[len(prefix):]).exists():
                add("error", "broken_reference", f"{f.name} -> {rel}")
    # duplicates (identical content under different names)
    seen: dict[str, str] = {}
    for g in (out / "Terrain").glob("*.glb"):
        h = _sha1(g)
        if h in seen:
            add("info", "duplicate_resource", f"{g.name} is identical to {seen[h]}")
        seen[h] = g.name
    if isinstance(mf.get("grass"), dict) and mf["grass"].get("chunks_with_grass"):
        for f in ("Materials/grass.tres", "Textures/height_rg.tres", "Vegetation/Meshes/grass_tuft.res"):
            if not (out / f).exists():
                add("error", "missing_grass_file", f"{f} missing")
    nav = mf.get("navigation")
    if isinstance(nav, dict) and nav.get("polygons") and not any((out / "Navigation" / f).exists() for f in ("navmesh.res", "navmesh.tres")):
        add("error", "missing_navmesh", "Navigation/navmesh.(res|tres) missing")
    if mf["vegetation"]["total_instances"] > s.max_instances:
        add("warning", "too_many_instances", f"{mf['vegetation']['total_instances']} instances (> {s.max_instances}); consider lower density")
    if mf["chunks"]["grid"][0] * mf["chunks"]["grid"][1] > 1024:
        add("warning", "many_chunks", "More than 1024 chunks in one scene; consider streaming")
    if not t3d and not mf["chunks"]["lod_steps"][1:]:
        add("warning", "no_lods", "Only one LOD level was generated")
    return {"ok": not any(f["severity"] == "error" for f in findings), "findings": findings}


# ------------------------------------------------------------------------------------------------ real Godot check
VERIFY_GD = """extends SceneTree
func _initialize():
	var res := {"errors": [], "classes": {}}
	var packed = load(OS.get_environment("AWS_SCENE"))
	if packed == null:
		print("VERIFY_JSON ", JSON.stringify({"errors": ["scene failed to load"]})); quit(1); return
	var root = packed.instantiate()
	get_root().add_child(root)                      # in the tree, so the navigation region registers with the server
	for i in 45: await physics_frame                # the navigation map needs a few frames to synchronise
	var stack = [root]
	var lod_ranges_ok = 0
	var mm_instances = 0
	var meshes_without_mesh = 0
	var colliders = 0
	var shapes_ok = 0
	var override_ok = 0
	var first_tf = null
	while stack.size() > 0:
		var n = stack.pop_back()
		var c = n.get_class()
		res["classes"][c] = res["classes"].get(c, 0) + 1
		if n is MeshInstance3D:
			if n.mesh == null: meshes_without_mesh += 1
			if n.material_override != null: override_ok += 1
			if n.visibility_range_end > 0.0 or n.visibility_range_begin > 0.0: lod_ranges_ok += 1
		if n is MultiMeshInstance3D:
			mm_instances += n.multimesh.instance_count
			if first_tf == null and n.multimesh.instance_count > 0:
				first_tf = n.multimesh.get_instance_transform(0)
		if n is StaticBody3D: colliders += 1
		if n is Path3D and n.curve != null and n.curve.point_count > 1: res["paths_ok"] = res.get("paths_ok", 0) + 1
		if n is Marker3D: res["markers"] = res.get("markers", 0) + 1
		if n is NavigationRegion3D and n.navigation_mesh != null:
			res["nav_polygons"] = n.navigation_mesh.get_polygon_count()
			var tgt = OS.get_environment("AWS_NAV_TARGET")
			var start = root.find_child("PlayerStart", true, false)
			if tgt != "" and start != null:
				var map = n.get_navigation_map()
				var waited = 0
				while NavigationServer3D.map_get_iteration_id(map) < 2 and waited < 900:   # big meshes need ~100 frames to synchronise
					await physics_frame
					waited += 1
				var v = tgt.split(",")
				var goal = Vector3(float(v[0]), float(v[1]), float(v[2]))
				var cgoal = NavigationServer3D.map_get_closest_point(map, goal)
				# map_get_path() silently stops after 4096 polygons: use a full query with the limit lifted
				var qp = NavigationPathQueryParameters3D.new()
				qp.map = map
				qp.start_position = start.global_position
				qp.target_position = cgoal
				qp.path_search_max_polygons = 100000000
				qp.path_search_max_distance = 0.0
				var qr = NavigationPathQueryResult3D.new()
				NavigationServer3D.query_path(qp, qr)
				var path = qr.path
				res["nav_path_points"] = path.size()
				res["nav_goal_gap_m"] = cgoal.distance_to(goal)           # nearest walkable point to the target
				var plen = 0.0
				for i in range(1, path.size()): plen += path[i].distance_to(path[i - 1])
				res["nav_path_length_m"] = plen
				res["nav_end_gap_m"] = path[path.size() - 1].distance_to(cgoal) if path.size() > 0 else -1.0
		if n is CollisionShape3D and n.shape != null: shapes_ok += 1
		for ch in n.get_children(): stack.append(ch)
	for t in root.find_children("*", "Terrain3D", true, false):
		res["terrain3d"] = {"regions": t.data.get_region_count(), "region_size": t.get_region_size(), "vertex_spacing": t.vertex_spacing,
			"collision_mode": t.collision_mode, "texture_arrays_ready": t.assets != null and t.assets.get_albedo_array_rid().is_valid()}   # at runtime Terrain3D frees the list after building the arrays
	var ps = root.find_child("PlayerStart", true, false)
	if ps != null:                                  # is there solid ground (collision) under the spawn point?
		var q = PhysicsRayQueryParameters3D.create(ps.global_position + Vector3(0, 30, 0), ps.global_position - Vector3(0, 60, 0))
		var hit = get_root().world_3d.direct_space_state.intersect_ray(q)
		res["ground_under_start"] = hit.size() > 0
		if hit.size() > 0: res["ground_gap_m"] = ps.global_position.y - hit["position"].y
	res["meshes_without_mesh"] = meshes_without_mesh
	res["material_overrides"] = override_ok
	res["lod_range_nodes"] = lod_ranges_ok
	res["multimesh_instances"] = mm_instances
	res["static_bodies"] = colliders
	res["collision_shapes"] = shapes_ok
	if first_tf != null:
		res["first_instance_basis_y"] = [first_tf.basis.y.x, first_tf.basis.y.y, first_tf.basis.y.z]
		res["first_instance_origin"] = [first_tf.origin.x, first_tf.origin.y, first_tf.origin.z]
	print("VERIFY_JSON ", JSON.stringify(res))
	quit(0)
"""


SHOT_GD = """extends SceneTree
func _initialize():
	var packed = load(OS.get_environment("AWS_SCENE"))
	var root = packed.instantiate()
	get_root().add_child(root)
	var cam_env = OS.get_environment("AWS_CAM")
	if cam_env != "":
		var v = cam_env.split(",")
		var cam = root.get_node_or_null("Camera3D")
		if cam == null: cam = root.find_child("Camera3D", true, false)
		if cam != null: cam.look_at_from_position(Vector3(float(v[0]), float(v[1]), float(v[2])), Vector3(float(v[3]), float(v[4]), float(v[5])))
	await create_timer(2.0).timeout
	var img = get_root().get_texture().get_image()
	img.save_png(OS.get_environment("AWS_SHOT"))
	quit(0)
"""


def screenshot_with_godot(out_dir: str | Path, png_path: str | Path, godot: str | None = None,
                          size: str = "960x600", timeout: int = 300, camera: tuple | None = None,
                          project_dir: str | Path | None = None, scene: str | None = None) -> dict:
    """Render Preview.tscn (or `scene`) in a real windowed Godot and save a PNG: checks shaders/materials/LOD visually.
    camera = (x, y, z, tx, ty, tz) overrides the preview camera."""
    out = Path(out_dir)
    godot = find_godot(godot)
    if not godot:
        return {"ok": False, "error": "Godot not found (set $AWS_GODOT)"}
    if project_dir is None and _prefix_of(out) != "res://" and scene is None:   # embedded package: shoot a temp copy, never the host project
        with _temp_project(out) as tmp:
            return screenshot_with_godot(tmp, png_path, godot, size, timeout, camera)
    proj = _project_root(out, project_dir); prefix = _prefix_of(out)
    scene = scene or (f"{prefix}Preview.tscn" if (out / "Preview.tscn").exists() else f"{prefix}World.tscn")
    subprocess.run([godot, "--headless", "--path", str(proj), "--import"], capture_output=True, text=True, timeout=timeout)  # import assets first
    script = out / "WorldData/_shot.gd"; script.write_text(SHOT_GD)
    try:
        run = subprocess.run([godot, "--path", str(proj), "--resolution", size, "--script", f"{prefix}WorldData/_shot.gd"],
                             capture_output=True, text=True, timeout=timeout,
                             env={**os.environ, "AWS_SHOT": str(png_path), "AWS_SCENE": scene, "AWS_CAM": ",".join(map(str, camera)) if camera else ""})
    finally:
        script.unlink(missing_ok=True)
    errs = [l for l in (run.stdout + run.stderr).splitlines() if "ERROR" in l and "still in use" not in l and "leaked" not in l]
    return {"ok": Path(png_path).exists() and not errs, "godot_errors": errs[:20]}


NAV_BAKE_GD = """extends SceneTree
func _initialize():
	var root = load(OS.get_environment("AWS_SCENE")).instantiate()
	get_root().add_child(root)
	await physics_frame
	var nm = NavigationMesh.new()
	nm.cell_size = float(OS.get_environment("AWS_NAV_CELL"))
	nm.cell_height = nm.cell_size * 0.5
	nm.agent_height = 1.8
	nm.agent_radius = 0.6
	nm.agent_max_climb = 0.6
	nm.agent_max_slope = float(OS.get_environment("AWS_NAV_SLOPE"))
	nm.geometry_parsed_geometry_type = NavigationMesh.PARSED_GEOMETRY_MESH_INSTANCES
	nm.geometry_source_geometry_mode = NavigationMesh.SOURCE_GEOMETRY_GROUPS_EXPLICIT
	nm.geometry_source_group_name = "aws_nav_src"
	var src = NavigationMeshSourceGeometryData3D.new()
	NavigationServer3D.parse_source_geometry_data(nm, src, root)
	var ws = OS.get_environment("AWS_NAV_WORLD").split(",")
	for t in root.find_children("*", "Terrain3D", true, false):          # Terrain3D is not a MeshInstance3D: ask it for its faces
		var box = AABB(Vector3(-2, -10000, -2), Vector3(float(ws[0]) + 4, 20000, float(ws[1]) + 4))
		src.add_faces(t.generate_nav_mesh_source_geometry(box, true), Transform3D.IDENTITY)
	NavigationServer3D.bake_from_source_geometry_data(nm, src)
	var err = ResourceSaver.save(nm, OS.get_environment("AWS_NAV_OUT"))
	print("NAV_JSON ", JSON.stringify({"polygons": nm.get_polygon_count(), "vertices": nm.get_vertices().size(), "save_error": err}))
	quit(0)
"""


def _uses_terrain3d(out: Path) -> bool:
    try:
        return json.loads((out / "WorldData/manifest.json").read_text()).get("terrain", {}).get("mode") == "terrain3d"
    except (OSError, ValueError, AttributeError):
        return (out / "Terrain3D/data").exists()


@contextlib.contextmanager
def _temp_project(out: Path, addon: Path | None = None):
    """A throw-away standalone copy of the package (prefix res://, own project.godot). Lets us run Godot (import, bake, verify) without ever
    touching the project the package is installed in. Terrain3D packages get a copy of the addon (found next to the package if not given)."""
    td = tempfile.mkdtemp(prefix="aws_pkg_")
    tmp = Path(td) / "pkg"
    try:
        shutil.copytree(out, tmp, ignore=shutil.ignore_patterns(".godot", "*.import", "*.uid", "project.godot"))
        if _prefix_of(out) != "res://":
            relocate_package(tmp, "res://")
        from exporters.godot.terrain3d import copy_external_deps, host_project_root
        copy_external_deps(out, tmp, host_project_root(out), _prefix_of(out))     # e.g. the game's own terrain shader + textures
        if _uses_terrain3d(out) and not (tmp / "addons/terrain_3d").exists():
            from exporters.godot.terrain3d import copy_addon, find_terrain3d_addon
            addon = addon or find_terrain3d_addon(out)
            if addon is not None:
                copy_addon(addon, tmp)
        (tmp / "project.godot").write_text('config_version=5\n[application]\nconfig/name="aws_temp"\n')
        yield tmp
    finally:
        shutil.rmtree(td, ignore_errors=True)


def _find_godot(explicit: str | None = None) -> str | None:
    return find_godot(explicit)


def bake_navigation(out: Path, godot: str, world_size: float, max_slope_deg: float = 40.0, timeout: int = 1800, cell: float | None = None,
                    addon: Path | None = None) -> dict:
    """Bake a NavigationMesh with Godot's Recast baker from the LOD0 terrain meshes (group 'aws_nav_src'); saves Navigation/navmesh.res.
    Runs in a temporary copy of the package, so the host project is never opened or modified."""
    import time as _t
    (out / "Navigation").mkdir(exist_ok=True)
    cell = float(cell) if cell else float(np.clip(world_size / 1500.0, 0.5, 2.0))   # bigger worlds: coarser voxels (bounded bake time)
    t0 = _t.time()
    with _temp_project(out, addon) as tmp:
        subprocess.run([godot, "--headless", "--path", str(tmp), "--import"], capture_output=True, text=True, timeout=timeout)
        (tmp / "Navigation").mkdir(exist_ok=True)
        script = tmp / "WorldData/_nav.gd"; script.write_text(NAV_BAKE_GD)
        run = subprocess.run([godot, "--headless", "--path", str(tmp), "--script", "res://WorldData/_nav.gd"], capture_output=True, text=True, timeout=timeout,
                             env={**os.environ, "AWS_SCENE": "res://World.tscn", "AWS_NAV_OUT": "res://Navigation/navmesh.res",
                                  "AWS_NAV_CELL": f"{cell}", "AWS_NAV_SLOPE": f"{max_slope_deg}", "AWS_NAV_WORLD": f"{world_size},{world_size}"})
        line = next((l for l in run.stdout.splitlines() if l.startswith("NAV_JSON ")), None)
        if line is None or not (tmp / "Navigation/navmesh.res").exists():
            return {"ok": False, "mode": "bake", "error": "navigation bake produced no mesh", "tail": (run.stdout + run.stderr)[-600:]}
        shutil.copy(tmp / "Navigation/navmesh.res", out / "Navigation/navmesh.res")
    st = json.loads(line[len("NAV_JSON "):])
    return {"ok": st["polygons"] > 0, "mode": "bake (Godot Recast)", "polygons": st["polygons"], "vertices": st["vertices"], "cell_m": cell,
            "max_slope_deg": max_slope_deg, "bake_seconds": round(_t.time() - t0, 1)}


def _prefix_of(out: Path) -> str:
    return json.loads((out / "WorldData/manifest.json").read_text()).get("res_prefix", "res://")


def _project_root(out: Path, project_dir: str | Path | None) -> Path:
    """The Godot project that contains the package: given, or the nearest parent with project.godot (standalone = the package itself)."""
    if project_dir:
        return Path(project_dir)
    for p in [out, *out.parents]:
        if (p / "project.godot").exists():
            return p
    return out


def verify_with_godot(out_dir: str | Path, godot: str | None = None, timeout: int = 600,
                      project_dir: str | Path | None = None) -> dict:
    """Import the package with the real Godot (headless) and inspect the instantiated World.tscn.
    Works for standalone packages and for packages embedded in an existing project (project_dir, or auto-detected)."""
    out = Path(out_dir)
    godot = find_godot(godot)
    if not godot:
        return {"ok": False, "error": "Godot not found (set $AWS_GODOT)"}
    if project_dir is None and _prefix_of(out) != "res://":       # embedded package: verify a temporary copy, never the host project
        with _temp_project(out) as tmp:
            return verify_with_godot(tmp, godot, timeout)
    proj = _project_root(out, project_dir); prefix = _prefix_of(out)
    imp = subprocess.run([godot, "--headless", "--path", str(proj), "--import"], capture_output=True, text=True, timeout=timeout)
    script = out / "WorldData/_verify.gd"
    script.write_text(VERIFY_GD)
    try:
        mf = json.loads((out / "WorldData/manifest.json").read_text()); summit = mf.get("markers", {}).get("summit")
        run = subprocess.run([godot, "--headless", "--path", str(proj), "--script", f"{prefix}WorldData/_verify.gd"], capture_output=True, text=True,
                             timeout=timeout, env={**os.environ, "AWS_SCENE": f"{prefix}World.tscn",
                                                   "AWS_NAV_TARGET": ",".join(f"{v:.2f}" for v in summit) if summit else ""})
    finally:
        script.unlink(missing_ok=True)
    line = next((l for l in run.stdout.splitlines() if l.startswith("VERIFY_JSON ")), None)
    benign = ("leaked at exit", "Plugin is not attached to debugger", "still in use at exit", "Pages in use exist at exit",   # headless shutdown noise
              "Cannot find clipmap target or active camera")                                                                 # Terrain3D without a camera (headless)
    errs = [l for l in (imp.stdout + imp.stderr + run.stdout + run.stderr).splitlines()
            if ("ERROR" in l or "SCRIPT ERROR" in l) and not any(b in l for b in benign)]
    if line is None:
        return {"ok": False, "error": "verification script produced no result", "godot_errors": errs[:20], "stdout_tail": run.stdout[-800:]}
    res = json.loads(line[len("VERIFY_JSON "):])
    res["godot_errors"] = errs[:20]
    res["ok"] = not res.get("errors") and not errs and res.get("meshes_without_mesh", 1) == 0
    if _uses_terrain3d(out):
        t3 = res.get("terrain3d", {})
        res["ok"] = res["ok"] and t3.get("regions", 0) > 0 and bool(t3.get("texture_arrays_ready"))
    if "ground_under_start" in res:
        res["ok"] = res["ok"] and res["ground_under_start"]
    if "nav_path_points" in res:                      # can a walker get from PlayerStart to (the walkable point nearest) the summit?
        res["nav_reaches_summit"] = bool(res["nav_path_points"] > 1 and 0 <= res["nav_end_gap_m"] < 6.0 and res["nav_goal_gap_m"] < 15.0)
    return res


# ------------------------------------------------------------------------------------------------ relocation
def relocate_package(package_dir: str | Path, new_prefix: str) -> dict:
    """Rewrite the res:// prefix of a package so it can live in any folder of a Godot project (e.g. 'res://addons/my_world/').
    Run it BEFORE Godot imports the package (stale .import files are removed so Godot regenerates them)."""
    out = Path(package_dir)
    mf_path = out / "WorldData/manifest.json"
    if not mf_path.exists():
        raise ValueError(f"{out} is not an AI World Studio package")
    if not new_prefix.startswith("res://") or not new_prefix.endswith("/"):
        raise ValueError("new_prefix must look like 'res://some/folder/' (or 'res://')")
    mf = json.loads(mf_path.read_text()); old = mf.get("res_prefix", "res://")
    changed = 0
    for f in list(out.rglob("*")):
        if "addons" in f.relative_to(out).parts:          # a bundled addon (Terrain3D) keeps its own res://addons/ paths
            continue
        if f.suffix in (".tscn", ".tres", ".gdshader", ".gdshaderinc") and f.is_file():
            t = f.read_text(); n = t.replace(f'"{old}', f'"{new_prefix}') if old != "res://" else re.sub(r'"res://(?!/)', f'"{new_prefix}', t)
            if n != t:
                f.write_text(n); changed += 1
        elif f.suffix == ".import" and f.is_file():
            f.unlink()                                  # stale: Godot recreates it with the new source path
    mf["res_prefix"] = new_prefix
    mf.pop("files", None)                              # hashes of rewritten text files no longer match
    mf_path.write_text(json.dumps(mf, indent=2))
    import shutil as _sh
    _sh.rmtree(out / ".godot", ignore_errors=True)
    return {"ok": True, "old_prefix": old, "new_prefix": new_prefix, "files_rewritten": changed}
