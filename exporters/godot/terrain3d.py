"""Terrain3D export: the heightfield + material weights become Terrain3D regions, so the world uses the Terrain3D addon
(its LOD clipmap, collision, sculpt/paint tools and whatever material/shader the host game already uses for Terrain3D).

Region files are binary resources whose layout depends on the addon version, so they are not written from Python: Godot bakes them
with the real addon in a throw-away project (the host project is never opened). Python prepares the inputs:
  height.f32   raw float32 metres, nx*ny, rows north->south          -> Image.FORMAT_RF
  control.u32  Terrain3D control bits (base id <<27, overlay <<22, blend <<14, nav <<1) -> Image.FORMAT_RF (bit pattern)
  tex_<i>_{albedo,normal}.rgba  procedural tileable textures (albedo+height in A, OpenGL normal+roughness in A)
Godot then writes Terrain3D/data/terrain3d_*.res, Terrain3D/textures/*.res (ImageTexture with mipmaps: exact, no import step),
Terrain3D/assets.tres (Terrain3DAssets) and Terrain3D/material.tres (Terrain3DMaterial, world background off)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

from materials.materials import COLORS, MATERIALS

ADDON_NAME = "terrain_3d"
REGION_SIZES = (64, 128, 256, 512, 1024, 2048)
MAX_REGIONS_PER_AXIS = 16          # Terrain3D region map is 32x32 (-16..15); the world starts at the origin, so 0..15 are usable
TEX_SIZE = 256
# per material: (noise contrast, height-noise scale, roughness, uv_scale = repeats per metre)
TEX_STYLE = {"grass": (0.10, 6, 0.85, 0.10), "dirt": (0.14, 10, 0.9, 0.12), "rock": (0.20, 4, 0.8, 0.07), "snow": (0.05, 3, 0.55, 0.08),
             "sand": (0.07, 14, 0.9, 0.12), "mud": (0.12, 8, 0.6, 0.12), "wet_rock": (0.16, 4, 0.45, 0.07), "gravel": (0.22, 20, 0.85, 0.15)}

BAKE_GD = """extends SceneTree
var t
var frames := 0
func env(k): return OS.get_environment(k)
func _initialize():
	t = Terrain3D.new()
	t.vertex_spacing = float(env("AWS_T3D_SPACING"))
	var mat = Terrain3DMaterial.new()
	mat.world_background = 0                       # NONE: nothing is drawn outside the imported regions
	t.material = mat
	t.assets = Terrain3DAssets.new()
	get_root().add_child(t)
func _process(_d):
	frames += 1
	if frames == 3: _bake()
	return false
func _bake():
	var src = env("AWS_T3D_SRC"); var dst = env("AWS_T3D_DST")
	var nx = int(env("AWS_T3D_NX")); var ny = int(env("AWS_T3D_NY"))
	var h = Image.create_from_data(nx, ny, false, Image.FORMAT_RF, FileAccess.get_file_as_bytes(src + "height.f32"))
	var c = Image.create_from_data(nx, ny, false, Image.FORMAT_RF, FileAccess.get_file_as_bytes(src + "control.u32"))
	t.change_region_size(int(env("AWS_T3D_REGION")))
	var imgs: Array[Image] = [h, c, null]
	t.data.import_images(imgs, Vector3.ZERO, 0.0, 1.0)
	t.data.calc_height_range(true)
	DirAccess.make_dir_recursive_absolute(dst + "data")
	DirAccess.make_dir_recursive_absolute(dst + "textures")
	t.data.save_directory(dst + "data")
	var texs = JSON.parse_string(FileAccess.get_file_as_string(src + "textures.json"))
	var ts = int(env("AWS_T3D_TEX"))
	for i in texs.size():
		var e = texs[i]
		var paths = []
		for kind in ["albedo", "normal"]:
			var img = Image.create_from_data(ts, ts, false, Image.FORMAT_RGBA8, FileAccess.get_file_as_bytes(src + "tex_%d_%s.rgba" % [i, kind]))
			img.generate_mipmaps()
			var p = dst + "textures/" + e["name"] + "_" + kind + ".res"
			ResourceSaver.save(ImageTexture.create_from_image(img), p)
			paths.append(p)
		var ta = Terrain3DTextureAsset.new()
		ta.name = e["name"]
		ta.id = i
		ta.albedo_texture = load(paths[0])
		ta.normal_texture = load(paths[1])
		ta.uv_scale = float(e["uv_scale"])
		ta.roughness = 0.0
		t.assets.set_texture_asset(i, ta)
	var e1 = ResourceSaver.save(t.assets, dst + "assets.tres")
	var e2 = ResourceSaver.save(t.material, dst + "material.tres")
	var probes = []
	for pr in env("AWS_T3D_PROBES").split(";", false):
		var v = pr.split(",")
		probes.append(t.data.get_height(Vector3(float(v[0]), 0.0, float(v[1]))))
	var enums = {}
	for en in ["CollisionMode"]:
		for k in ClassDB.class_get_enum_constants("Terrain3DCollision", en): enums[k] = ClassDB.class_get_integer_constant("Terrain3DCollision", k)
	print("T3D_JSON ", JSON.stringify({"godot_region_size": t.get_region_size(), "regions": t.data.get_region_count(), "region_locations": t.data.get_region_locations(), "height_range": [t.data.get_height_range().x, t.data.get_height_range().y],
		"textures": t.assets.get_texture_count(), "save_assets": e1, "save_material": e2, "probes": probes, "version": t.get_version(), "enums": enums}))
	quit(0)
"""


# ------------------------------------------------------------------------------------------------ addon discovery
def find_terrain3d_addon(package_dir: str | Path | None = None, explicit: str | Path | None = None) -> Path | None:
    """The Terrain3D addon folder to bake with: explicit path -> $AWS_TERRAIN3D_ADDON -> <package>/addons/terrain_3d
    -> addons/terrain_3d of the Godot project that contains the package."""
    cands: list[Path] = []
    if explicit:
        cands.append(Path(explicit))
    if os.environ.get("AWS_TERRAIN3D_ADDON"):
        cands.append(Path(os.environ["AWS_TERRAIN3D_ADDON"]))
    if package_dir is not None:
        p = Path(package_dir).resolve()
        cands.append(p / "addons" / ADDON_NAME)
        for parent in p.parents:
            if (parent / "project.godot").exists():
                cands.append(parent / "addons" / ADDON_NAME)
                break
    for c in cands:
        if (c / "terrain.gdextension").exists():
            return c
    return None


def addon_version(addon: Path) -> str:
    for line in (addon / "plugin.cfg").read_text(encoding="utf-8").splitlines():
        if line.startswith("version="):
            return line.split("=", 1)[1].strip().strip('"')
    return "unknown"


def _platform_tag() -> str:
    return "windows" if sys.platform == "win32" else ("macos" if sys.platform == "darwin" else "linux")


def copy_addon(addon: Path, project_dir: Path, all_platforms: bool = False) -> Path:
    """Copy the addon into project_dir/addons/terrain_3d (only this platform's binaries unless all_platforms)."""
    dst = project_dir / "addons" / ADDON_NAME
    tag = _platform_tag()

    def ignore(d, names):
        skip = {".godot"} & set(names)
        if not all_platforms and Path(d).name == "bin":
            skip |= {n for n in names if tag not in n}
        return skip
    shutil.copytree(addon, dst, ignore=ignore, dirs_exist_ok=True)
    return dst


# ------------------------------------------------------------------------------------------------ host material
def host_project_root(path: str | Path) -> Path | None:
    p = Path(path).resolve()
    for d in (p, *p.parents):
        if (d / "project.godot").exists():
            return d
    return None


def extract_material(scene_file: Path, rtype: str = "Terrain3DMaterial") -> str:
    """Copy the first `rtype` sub-resource of a .tscn (with the ext/sub resources it uses) into a standalone .tres text.
    External paths stay as they are (res:// of the host project), so edits to the host's shader/textures still apply."""
    import re
    text = scene_file.read_text(encoding="utf-8")
    blocks = re.split(r"(?m)^(?=\[)", text)
    ext = {}; sub = {}; order = []
    for b in blocks:
        m = re.match(r'\[ext_resource [^\]]*id="([^"]+)"', b)
        if m:
            ext[m.group(1)] = re.sub(r' uid="[^"]*"', "", b.strip().splitlines()[0])
        m = re.match(r'\[sub_resource type="([^"]+)" id="([^"]+)"\]', b)
        if m:
            sub[m.group(2)] = (m.group(1), b.strip()); order.append(m.group(2))
    root = next((i for i in order if sub[i][0] == rtype), None)
    if root is None:
        raise ValueError(f"{scene_file.name} has no {rtype} sub-resource")
    need_ext: list[str] = []; need_sub: list[str] = []

    def walk(sid):
        for kind, rid in re.findall(r'(ExtResource|SubResource)\("([^"]+)"\)', sub[sid][1]):
            if kind == "ExtResource" and rid not in need_ext:
                need_ext.append(rid)
            elif kind == "SubResource" and rid not in need_sub and rid in sub:
                need_sub.append(rid); walk(rid)
    walk(root)
    body = sub[root][1].split("\n", 1)[1] if "\n" in sub[root][1] else ""
    deps = [sub[i][1] for i in order if i in need_sub]
    return (f'[gd_resource type="{rtype}" format=3]\n\n' + "\n".join(ext[i] for i in need_ext if i in ext) + "\n\n"
            + "\n\n".join(deps) + ("\n\n" if deps else "") + "[resource]\n" + body.strip() + "\n")


def copy_external_deps(pkg: Path, dst_project: Path, host_root: Path | None, prefix: str) -> list[str]:
    """Copy host-project files referenced by the package (res:// paths outside it, e.g. the game's terrain shader and textures) into a temp
    project at the same paths, following references inside copied text files (#include, ext_resource). Lets temp copies load the host's look."""
    import re
    if host_root is None:
        return []
    copied: list[str] = []
    todo = [f for f in dst_project.rglob("*") if f.suffix in (".tscn", ".tres", ".gdshader", ".gdshaderinc") and "addons" not in f.relative_to(dst_project).parts]
    seen: set[str] = set()
    while todo:
        f = todo.pop()
        for ref in re.findall(r'"res://([^"]+)"', f.read_text(encoding="utf-8", errors="ignore")):
            if ref in seen or ref.startswith("addons/") or (prefix != "res://" and f"res://{ref}".startswith(prefix)):
                continue
            seen.add(ref)
            src, dst = host_root / ref, dst_project / ref
            if src.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(src, dst); copied.append(ref)
                if dst.suffix in (".tres", ".gdshader", ".gdshaderinc", ".tscn"):
                    todo.append(dst)
    return copied


# ------------------------------------------------------------------------------------------------ inputs
def pick_region_size(nx: int, ny: int) -> int:
    """Smallest padded area (ties -> fewer, larger regions) that fits in the region map."""
    best = None
    for rs in REGION_SIZES:
        rx, ry = -(-nx // rs), -(-ny // rs)
        if max(rx, ry) > MAX_REGIONS_PER_AXIS:
            continue
        key = (rx * ry * rs * rs, -rs)
        if best is None or key < best[0]:
            best = (key, rs)
    if best is None:
        raise ValueError(f"world too large for Terrain3D ({nx}x{ny} vertices > {MAX_REGIONS_PER_AXIS}x2048)")
    return best[1]


def control_map(weights: np.ndarray, water: np.ndarray | None = None, texture_ids: dict[str, int] | None = None) -> np.ndarray:
    """(ny,nx,8) material weights -> uint32 Terrain3D control map: dominant texture as base, 2nd as overlay, blend = overlay share.
    texture_ids maps our material names to the host's texture slots (several materials may share one slot)."""
    ids = {m: i for i, m in enumerate(MATERIALS)} if texture_ids is None else {m: int(texture_ids.get(m, 0)) for m in MATERIALS}
    if any(not 0 <= v <= 31 for v in ids.values()):
        raise ValueError("Terrain3D texture ids must be in 0..31")
    slots = sorted(set(ids.values()))
    w = np.zeros(weights.shape[:2] + (len(slots),), np.float32)
    for m, i in ids.items():
        w[..., slots.index(i)] += weights[..., MATERIALS.index(m)]
    order = np.argsort(-w, axis=-1)
    a, b = order[..., 0], order[..., 1] if w.shape[-1] > 1 else order[..., 0]
    wa = np.take_along_axis(w, a[..., None], -1)[..., 0]
    wb = np.take_along_axis(w, b[..., None], -1)[..., 0]
    blend = np.where(wa + wb > 1e-6, wb / np.maximum(wa + wb, 1e-6), 0.0)
    slot_arr = np.array(slots, np.uint32)
    base, over = slot_arr[a], slot_arr[b]
    c = (base << 27) | (over << 22) | ((np.clip(blend * 255 + 0.5, 0, 255).astype(np.uint32)) << 14)
    nav = np.ones(c.shape, bool) if water is None else ~water
    c |= nav.astype(np.uint32) << 1
    return c.astype(np.uint32)


def _periodic_noise(n: int, scale: float, rng: np.random.Generator) -> np.ndarray:
    """Tileable fractal noise in [0,1]: white noise filtered in the frequency domain (periodic by construction)."""
    f = np.fft.fftfreq(n)[:, None] ** 2 + np.fft.fftfreq(n)[None, :] ** 2
    amp = 1.0 / np.maximum(np.sqrt(f) * n / scale, 1.0) ** 1.6
    x = np.real(np.fft.ifft2(np.fft.fft2(rng.standard_normal((n, n))) * amp))
    return (x - x.min()) / max(np.ptp(x), 1e-9)


def material_textures(size: int = TEX_SIZE) -> list[dict]:
    """Per material: albedo RGBA8 (sRGB colour with soft variation, height in A) + normal RGBA8 (OpenGL, roughness in A)."""
    out = []
    for i, m in enumerate(MATERIALS):
        contrast, hscale, rough, uv = TEX_STYLE[m]
        rng = np.random.default_rng(97 + i)
        h = _periodic_noise(size, hscale, rng)
        tint = _periodic_noise(size, 2.0, rng)
        col = COLORS[i][None, None, :] * (1.0 + contrast * ((h - 0.5) * 1.4 + (tint - 0.5) * 0.8))[..., None]
        alb = np.concatenate([np.clip(col, 0, 1), h[..., None]], -1)
        gx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * 0.5 * 4.0 * contrast * 10
        gy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * 0.5 * 4.0 * contrast * 10
        nrm = np.stack([-gx, gy, np.ones_like(h)], -1)                    # OpenGL convention (green = +Y up the texture)
        nrm /= np.linalg.norm(nrm, axis=-1, keepdims=True)
        nmap = np.concatenate([nrm * 0.5 + 0.5, np.full(h.shape + (1,), rough)], -1)
        out.append({"name": m, "uv_scale": uv, "albedo": (alb * 255 + 0.5).astype(np.uint8), "normal": (nmap * 255 + 0.5).astype(np.uint8)})
    return out


# ------------------------------------------------------------------------------------------------ bake
def bake_terrain3d(out: Path, hf, weights: np.ndarray, water: np.ndarray | None, godot: str, addon: Path, prefix: str = "res://",
                   texture_ids: dict[str, int] | None = None, tex_size: int = TEX_SIZE, timeout: int = 1200) -> dict:
    """Write <out>/Terrain3D/{data/*.res, textures/*.res, assets.tres, material.tres} using Godot + the real addon in a temp project."""
    import tempfile
    region = pick_region_size(hf.nx, hf.ny)
    td = Path(tempfile.mkdtemp(prefix="aws_t3d_"))
    try:
        (td / "project.godot").write_text('config_version=5\n[application]\nconfig/name="aws_t3d_bake"\n')
        copy_addon(addon, td)
        src = td / "_src"; src.mkdir()
        hf.data.astype("<f4").tofile(src / "height.f32")
        control_map(weights, water, texture_ids).astype("<u4").tofile(src / "control.u32")
        texs = material_textures(tex_size)
        for i, t in enumerate(texs):
            t["albedo"].tofile(src / f"tex_{i}_albedo.rgba"); t["normal"].tofile(src / f"tex_{i}_normal.rgba")
        (src / "textures.json").write_text(json.dumps([{"name": t["name"], "uv_scale": t["uv_scale"]} for t in texs]))
        (td / "_bake.gd").write_text(BAKE_GD)
        sx, sz = hf.size
        rng = np.random.default_rng(7)                      # highest vertex, a corner and random points (mostly on relief)
        iy, ix = np.unravel_index(int(np.argmax(hf.data)), hf.data.shape)
        probes = [(ix * hf.cell_size, iy * hf.cell_size), ((hf.nx - 1) * hf.cell_size, (hf.ny - 1) * hf.cell_size)]
        probes += [(float(i) * hf.cell_size, float(j) * hf.cell_size) for i, j in zip(rng.integers(0, hf.nx, 10), rng.integers(0, hf.ny, 10))]   # on vertices: exact data, no interpolation
        subprocess.run([godot, "--headless", "--path", str(td), "--import"], capture_output=True, text=True, timeout=timeout)
        run = subprocess.run([godot, "--headless", "--path", str(td), "--script", "res://_bake.gd"], capture_output=True, text=True, timeout=timeout,
                             env={**os.environ, "AWS_T3D_SRC": "res://_src/", "AWS_T3D_DST": "res://Terrain3D/", "AWS_T3D_NX": str(hf.nx),
                                  "AWS_T3D_NY": str(hf.ny), "AWS_T3D_REGION": str(region), "AWS_T3D_SPACING": f"{hf.cell_size}",
                                  "AWS_T3D_TEX": str(tex_size), "AWS_T3D_PROBES": ";".join(f"{x:.3f},{z:.3f}" for x, z in probes)})
        line = next((l for l in run.stdout.splitlines() if l.startswith("T3D_JSON ")), None)
        regions = sorted((td / "Terrain3D/data").glob("terrain3d*.res")) if (td / "Terrain3D/data").exists() else []
        if line is None or not regions or not (td / "Terrain3D/assets.tres").exists():
            return {"ok": False, "error": "Terrain3D bake produced no regions", "tail": (run.stdout + run.stderr)[-1500:]}
        info = json.loads(line[len("T3D_JSON "):])
        dst = out / "Terrain3D"
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(td / "Terrain3D", dst, ignore=shutil.ignore_patterns("*.import", "*.uid"))
        if prefix != "res://":
            for f in dst.glob("*.tres"):
                f.write_text(f.read_text(encoding="utf-8").replace('"res://Terrain3D/', f'"{prefix}Terrain3D/'), encoding="utf-8")
        expected = [float(hf.data[int(round(z / hf.cell_size)), int(round(x / hf.cell_size))]) for x, z in probes]
        err = float(np.max(np.abs(np.array(info["probes"], np.float64) - expected)))
        info.update({"ok": err < 0.05 and info.get("godot_region_size") == region, "region_size": region, "vertex_spacing": hf.cell_size, "region_files": [r.name for r in regions],
                     "probe_max_error_m": err, "addon_version": addon_version(addon)})
        if err >= 0.05:
            info["error"] = f"heights read back from Terrain3D differ by {err:.3f} m"
        return info
    finally:
        shutil.rmtree(td, ignore_errors=True)
