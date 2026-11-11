"""Look inside a Godot project to suggest export settings: is Terrain3D installed, which Terrain3DMaterials does it use (in .tres files
or embedded in scenes), and which scripts are water systems that answer surface_at(x, z) (they can draw our water.json)."""
from __future__ import annotations

import re
from pathlib import Path

SKIP_DIRS = {".godot", ".git", "addons", ".import", "android", "build"}
MAX_BYTES = 4_000_000                       # don't read huge scene files byte by byte


def _files(root: Path, suffixes: tuple[str, ...], skip_prefix: str | None = None):
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]) or p.suffix not in suffixes or not p.is_file():
            continue
        if skip_prefix and rel.as_posix().startswith(skip_prefix):
            continue
        yield p, rel


def inspect_host_project(project_dir: str | Path, skip_prefix: str | None = "generated_worlds/") -> dict:
    """{ok, project_name, terrain3d: {installed, version}, terrain3d_materials: [{path, kind, shader_override}],
    water_scripts: [res paths], suggestion: {...export_to_godot kwargs...}}. skip_prefix: ignore our own exported worlds."""
    root = Path(project_dir)
    pg = root / "project.godot"
    if not pg.exists():
        return {"ok": False, "error": f"{root} has no project.godot"}
    name = next((l.split("=", 1)[1].strip().strip('"') for l in pg.read_text(encoding="utf-8", errors="ignore").splitlines()
                 if l.startswith("config/name=")), root.name)
    t3d = root / "addons/terrain_3d"
    version = None
    if (t3d / "plugin.cfg").exists():
        m = re.search(r'version="([^"]+)"', (t3d / "plugin.cfg").read_text(encoding="utf-8", errors="ignore"))
        version = m.group(1) if m else "unknown"
    mats = []
    for p, rel in _files(root, (".tscn", ".tres"), skip_prefix):
        if p.stat().st_size > MAX_BYTES:
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        if "Terrain3DMaterial" not in t:
            continue
        kind = "resource" if t.startswith('[gd_resource type="Terrain3DMaterial"') else ("scene" if p.suffix == ".tscn" else None)
        if kind is None:
            continue
        shader = None
        block = t.split('type="Terrain3DMaterial"', 1)[1].split("\n[", 1)[0]
        sm = re.search(r'shader_override = ExtResource\("([^"]+)"\)', block)
        if sm and "shader_override_enabled = true" in block:
            em = re.search(r'\[ext_resource type="Shader"[^\]]*path="([^"]+)"[^\]]*id="' + re.escape(sm.group(1)) + '"', t)
            shader = em.group(1) if em else "custom"
        mats.append({"path": "res://" + rel.as_posix(), "kind": kind, "shader_override": shader})
    mats.sort(key=lambda m: (m["shader_override"] is None, m["path"]))      # the game's own look (custom shader) first
    water = []
    for p, rel in _files(root, (".gd",), skip_prefix):
        t = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"^func surface_at\(", t, re.M) and "water_file" in t:
            water.append("res://" + rel.as_posix())
    sug: dict = {"terrain": "terrain3d" if version else "mesh"}
    if version and mats and mats[0]["shader_override"]:
        sug["terrain3d_material"] = mats[0]["path"]
    if water:
        sug["water_script"] = water[0]
    return {"ok": True, "project_name": name, "terrain3d": {"installed": bool(version), "version": version},
            "terrain3d_materials": mats, "water_scripts": water, "suggestion": sug}
