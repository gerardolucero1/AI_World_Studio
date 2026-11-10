"""Minimal AWP (AI World Project) persistence: project.json + terrain/heightfields/base.npy."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from terrain.heightfield.heightfield import Heightfield

AWP_VERSION = 1
SUBDIRS = ["terrain/chunks", "terrain/heightfields", "terrain/masks", "terrain/operations",
           "features", "geometry", "biomes", "assets", "materials", "textures",
           "previews", "validation", "exports", "history"]


def save_world(path: str | Path, hf: Heightfield, name: str = "Untitled", ops_json: str | None = None) -> Path:
    root = Path(path)
    for d in SUBDIRS:
        (root / d).mkdir(parents=True, exist_ok=True)
    np.save(root / "terrain/heightfields/base.npy", hf.data)
    if ops_json is not None:
        (root / "terrain/operations/ops.json").write_text(ops_json)
    meta = {"awp_version": AWP_VERSION, "name": name,
            "terrain": {"file": "terrain/heightfields/base.npy", "nx": hf.nx, "ny": hf.ny,
                        "cell_size": hf.cell_size, "dtype": "float32"}, "style": hf.style}
    (root / "project.json").write_text(json.dumps(meta, indent=2))
    return root


def load_world(path: str | Path) -> tuple[Heightfield, dict]:
    root = Path(path)
    meta = json.loads((root / "project.json").read_text())
    if meta.get("awp_version", 0) > AWP_VERSION:
        raise ValueError("Project saved with a newer AWP version")
    t = meta["terrain"]
    data = np.load(root / t["file"])
    hf = Heightfield(t["nx"], t["ny"], t["cell_size"], data)
    hf.style = dict(meta.get("style") or {})
    return hf, meta
