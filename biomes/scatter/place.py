"""Hand-placed objects (a landmark tree, a boulder, a lone pine): exact position, size and rotation, grounded on the terrain.
Same result format as scatter() (`_instances` rows [x, y, z, yaw_deg, radius, height, species_id]), so the editor, validation,
previews and the Godot exporter treat them like any vegetation. Heights are re-sampled on replay, so they follow later terrain edits."""
from __future__ import annotations

import numpy as np

from biomes.masks.masks import water_mask
from biomes.scatter.scatter import validate_vegetation
from terrain.heightfield.heightfield import Heightfield

# base size of each kind at scale 1 (radius m, height m) - same meshes as the scatter presets
KINDS = {"oak": (4.6, 9.5), "pine": (2.6, 13.0), "shrub": (1.3, 1.4), "reed": (0.6, 1.4), "rock": (2.0, 1.6)}


def place_objects(hf: Heightfield, species: list[dict], objects: list[dict], _log=()):
    """species: [{"name": kind, "max_slope": deg}] (one per kind used); objects: [{"kind", "x", "y", "scale", "yaw", "sink"}]."""
    sid = {s["name"]: i for i, s in enumerate(species)}
    rows = []
    for o in objects:
        k = o["kind"]
        r0, h0 = KINDS[k]
        sc = float(o.get("scale", 1.0))
        x, z = float(o["x"]), float(o["y"])
        y = hf.sample(x, z) - float(o.get("sink", 0.0))
        rows.append([x, y, z, float(o.get("yaw", 0.0)), r0 * sc, h0 * sc, sid[k]])
    inst = np.array(rows, np.float32).reshape(-1, 7)
    counts: dict[str, int] = {}
    for o in objects:
        counts[o["kind"]] = counts.get(o["kind"], 0) + 1
    return None, {"counts": counts, "total": int(len(inst)), "warnings": validate_vegetation(hf, inst, species, water_mask(hf, list(_log))),
                  "_instances": inst}


def place_params(objects: list[dict], world_size: tuple[float, float]) -> dict:
    """Validate objects and build the operation params (one species entry per kind used). Raises ValueError with a clear message."""
    if not objects:
        raise ValueError("give at least one object")
    sx, sz = world_size
    kinds: list[str] = []
    objs = []
    for o in objects:
        k = o.get("kind")
        if k not in KINDS:
            raise ValueError(f"unknown kind {k!r}; choose from {list(KINDS)}")
        x, y, sc = float(o["x"]), float(o["y"]), float(o.get("scale", 1.0))
        if not (0 <= x <= sx and 0 <= y <= sz):
            raise ValueError(f"object at ({x}, {y}) is outside the world (0..{sx} x 0..{sz})")
        if not 0.05 <= sc <= 20:
            raise ValueError("scale must be within 0.05..20")
        if k not in kinds:
            kinds.append(k)
        objs.append(dict(kind=k, x=x, y=y, scale=sc, yaw=float(o.get("yaw", 0.0)), sink=float(o.get("sink", 0.0))))
    return dict(species=[dict(name=k, max_slope=80.0 if k == "rock" else 35.0) for k in kinds], objects=objs)
