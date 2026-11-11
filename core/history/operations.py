"""Non-destructive-ish operation log: every procedural op is a serializable record that can be replayed."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from terrain.erosion import erosion
from terrain.heightfield.heightfield import Heightfield, Rect
from terrain.procedural import shapes
from features.rivers.river import create_river
from features.lakes.lake import create_lake
from features.paths.path import create_path
from geometry.cliffs.cliff import create_cliff_plateau
from materials.materials import paint_material
from biomes.scatter.scatter import scatter
from biomes.scatter.place import place_objects

ALGO_VERSION = 1


def _sculpt(hf, tool: str, points, radius: float, strength: float = 0.5, hardness: float = 0.0,
            target_height: float = 0.0, repeats: int = 1, strength_scale: float = 2.0):
    """Brush stroke along `points` (meters). Same brushes as the interactive tool."""
    from terrain.sculpt.brushes import BrushParams, Tool, apply_dab, union
    prm = BrushParams(radius, strength, hardness, target_height, strength_scale)
    rect = None
    for _ in range(max(repeats, 1)):
        for x, y in points:
            rect = union(rect, apply_dab(hf, Tool(tool), float(x), float(y), prm))
    return rect


def _brush(hf, dabs, radius: float, strength: float = 0.5, hardness: float = 0.0, target_height: float = 0.0,
           strength_scale: float = 2.0):
    """A brush stroke made in the editor: dabs = [[tool, x, y], ...] in the order they were applied (Ctrl can flip raise/lower mid-stroke)."""
    from terrain.sculpt.brushes import BrushParams, Tool, apply_dab, union
    prm = BrushParams(radius, strength, hardness, target_height, strength_scale)
    rect = None
    for tool, x, y in dabs:
        rect = union(rect, apply_dab(hf, Tool(tool), float(x), float(y), prm))
    return rect


def _thermal(hf, **k): erosion.thermal(hf, **k); return 0, hf.ny, 0, hf.nx
def _hydraulic(hf, **k): erosion.hydraulic(hf, **k); return 0, hf.ny, 0, hf.nx


REGISTRY: dict[str, Callable[..., Rect]] = {
    "sculpt": _sculpt,
    "noise": shapes.apply_noise, "mountain": shapes.mountain, "mountain_range": shapes.mountain_range,
    "plateau": shapes.plateau, "bowl": shapes.bowl, "cap": shapes.cap, "border_mountains": shapes.border_mountains, "terrace": shapes.terrace, "valley": shapes.valley, "basin": shapes.basin,
    "cliff_plateau": create_cliff_plateau, "river": create_river, "lake": create_lake, "path": create_path,
    "paint_material": paint_material, "scatter_vegetation": scatter, "place_objects": place_objects, "brush": _brush,
    "thermal_erosion": _thermal, "hydraulic_erosion": _hydraulic,
}


@dataclass(eq=False)   # identity equality: results may hold numpy arrays / meshes
class Operation:
    kind: str
    params: dict[str, Any]
    enabled: bool = True
    algo_version: int = ALGO_VERSION
    result: dict[str, Any] | None = None   # feature outputs (water levels, warnings); regenerated on replay


# ops that never modify heights: no tile snapshot needed, but they are logged and undoable
NON_TERRAIN = {"paint_material", "scatter_vegetation", "place_objects"}
# ops that need earlier operations (water/path masks)
CONTEXTUAL = {"scatter_vegetation", "place_objects"}


def run(hf: Heightfield, op: Operation, log=()) -> Rect | None:
    if not op.enabled:
        op.result = None                 # a disabled operation leaves nothing behind (no water, no vegetation, no warnings)
        return None
    if op.kind not in REGISTRY:
        raise KeyError(f"unknown operation {op.kind!r}")
    extra = {"_log": list(log)} if op.kind in CONTEXTUAL else {}
    out = REGISTRY[op.kind](hf, **op.params, **extra)
    if isinstance(out, tuple) and len(out) == 2 and isinstance(out[1], dict):   # features return (rect, report)
        out, op.result = out
    return out


def dumps(ops: list[Operation]) -> str:
    def clean(o: Operation) -> dict:
        # transient runtime data (keys starting with "_", e.g. meshes) is never serialized
        res = {k: v for k, v in o.result.items() if not k.startswith("_")} if o.result else None
        return {"kind": o.kind, "params": o.params, "enabled": o.enabled, "algo_version": o.algo_version, "result": res}
    return json.dumps([clean(o) for o in ops], indent=2)


def loads(s: str) -> list[Operation]:
    return [Operation(**d) for d in json.loads(s)]


def rebuild(hf: Heightfield, ops: list[Operation]) -> None:
    """Replay an op list onto a base heightfield, giving each op the earlier ones as context."""
    done: list[Operation] = []
    for op in ops:
        run(hf, op, done)
        done.append(op)


def regenerate_transient(hf: Heightfield, ops: list[Operation]) -> None:
    """After loading a project: rebuild runtime-only data (cliff meshes, vegetation instances) WITHOUT
    re-applying terrain edits (the saved heightfield already contains them)."""
    from geometry.cliffs.cliff import cliff_mesh_only
    done: list[Operation] = []
    for op in ops:
        if op.enabled and op.kind == "cliff_plateau":
            op.result = dict(op.result or {}); op.result["_mesh"] = cliff_mesh_only(hf, **op.params)
        elif op.enabled and op.kind == "scatter_vegetation":
            _, rep = scatter(hf, **op.params, _log=list(done)); op.result = rep
        elif op.enabled and op.kind == "place_objects":
            _, rep = place_objects(hf, **op.params, _log=list(done)); op.result = rep
        done.append(op)
