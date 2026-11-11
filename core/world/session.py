"""WorldSession: the single service layer used by BOTH the UI and the API/MCP (no duplicated logic)."""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np

from core.history.history import History, Stroke
from core.history.operations import NON_TERRAIN, Operation, dumps, loads, regenerate_transient, run
from core.project.awp import load_world, save_world
from terrain.heightfield.heightfield import Heightfield, Rect


class WorldSession:
    def __init__(self, hf: Heightfield | None = None, name: str = "Untitled"):
        self.lock = threading.RLock()
        self.name = name
        self.path: Path | None = None
        self.hf = hf if hf is not None else Heightfield(513, 513, 2.0)
        self.history = History()
        self.op_log: list[Operation] = []
        self.checkpoints: dict[str, tuple] = {}
        self.revision = 0   # bumps on every change (UI/preview cache invalidation)
        self.origin: np.ndarray | None = self.hf.data.copy()   # terrain before any operation: lets us edit a past operation and replay

    # ---- lifecycle ----
    def replace(self, hf: Heightfield, name: str | None = None, ops: list[Operation] | None = None,
                origin: np.ndarray | None = None) -> None:
        with self.lock:
            self.hf, self.history, self.op_log = hf, History(), list(ops or [])
            self.origin = origin if origin is not None else (hf.data.copy() if not ops else None)
            self.checkpoints.clear()
            if name:
                self.name = name
            self.revision += 1

    def new(self, size_m: float = 1024.0, resolution: int = 513, name: str = "Untitled", base_height: float = 0.0) -> None:
        hf = Heightfield(resolution, resolution, size_m / (resolution - 1))
        hf.data[:] = base_height
        self.path = None
        self.replace(hf, name)

    # ---- operations ----
    def apply(self, op: Operation) -> Rect | None:
        """Run an operation as one undoable step; the op is recorded in the log."""
        with self.lock:
            stroke = Stroke(self.hf, op.kind)
            stroke.op = op
            if op.kind not in NON_TERRAIN:
                stroke.before_edit((0, self.hf.ny, 0, self.hf.nx))
            try:
                rect = run(self.hf, op, self.op_log)
            except Exception:
                stroke.finish(); stroke.undo()          # leave the terrain untouched on failure
                raise
            stroke.finish()
            self.history.push(stroke)
            self.op_log.append(op)
            self.revision += 1
            if op.kind not in NON_TERRAIN:
                self.reground()
            return rect

    def reground(self) -> None:
        """Sit vegetation and placed objects back on the ground after the terrain changed (sculpting, new shapes, undo/redo):
        otherwise they float or sink until the project is reopened."""
        from biomes.scatter.scatter import _sample_many
        for o in self.op_log:
            inst = (o.result or {}).get("_instances")
            if inst is None or not len(inst):
                continue
            y = _sample_many(self.hf, inst[:, 0].astype(np.float64), inst[:, 2].astype(np.float64))
            if o.kind == "place_objects":
                y = y - np.array([float(ob.get("sink", 0.0)) for ob in o.params.get("objects", [])], np.float64)[: len(inst)]
            inst[:, 1] = y.astype(inst.dtype)

    def commit_stroke(self, stroke: Stroke, op: Operation | None = None) -> None:
        """An interactive brush stroke (already applied). With `op` (kind "brush") it is also logged, so it can be replayed."""
        with self.lock:
            stroke.finish()
            if op is not None:
                stroke.op = op
                self.op_log.append(op)
            self.history.push(stroke); self.revision += 1
            self.reground()

    @property
    def can_modify(self) -> bool:
        return self.origin is not None

    def modify(self, index: int, params: dict | None = None, enabled: bool | None = None) -> Rect:
        """Change the parameters of a past operation (e.g. move a river's points) and replay everything from the original terrain:
        later operations adapt (lakes, vegetation, trails...). One undoable step."""
        from core.history.operations import rebuild
        with self.lock:
            if self.origin is None:
                raise ValueError("this world was opened without its original terrain (older project): past operations can't be edited; "
                                 "save it again and reopen, or recreate the feature")
            if not 0 <= index < len(self.op_log):
                raise IndexError(f"operation index {index} out of range (0..{len(self.op_log) - 1})")
            op = self.op_log[index]
            old_params, old_results, old_enabled = op.params, [o.result for o in self.op_log], op.enabled
            hf2 = Heightfield(self.hf.nx, self.hf.ny, self.hf.cell_size, self.origin.copy())
            hf2.style = dict(self.hf.style)
            op.params = {**op.params, **(params or {})}
            if enabled is not None:
                op.enabled = bool(enabled)
            try:
                rebuild(hf2, self.op_log)
            except Exception:
                op.params, op.enabled = old_params, old_enabled
                for o, r in zip(self.op_log, old_results):
                    o.result = r
                raise
            stroke = Stroke(self.hf, "modify")
            stroke.before_edit((0, self.hf.ny, 0, self.hf.nx))
            self.hf.data[:] = hf2.data
            stroke.finish()
            stroke.meta = {"op": op, "params": (old_params, op.params), "enabled": (old_enabled, op.enabled),
                           "results": (old_results, [o.result for o in self.op_log]),
                           "ops": list(self.op_log)}
            self.history.push_always(stroke)
            self.revision += 1
            self.reground()
            return 0, self.hf.ny, 0, self.hf.nx

    def _restore_meta(self, s, which: int) -> None:
        m = getattr(s, "meta", None)
        if not m:
            return
        m["op"].params = m["params"][which]
        m["op"].enabled = m.get("enabled", (True, True))[which]
        for o, r in zip(m["ops"], m["results"][which]):
            o.result = r

    def undo(self) -> Rect | None:
        with self.lock:
            rect = self.history.undo(); s = self.history.last
            if rect is not None or (s and s.op is not None):
                if s is not None and s.op is not None and s.op in self.op_log:
                    self.op_log.remove(s.op)
                self._restore_meta(s, 0)
                self.revision += 1
            if rect is not None:
                self.reground()
            return rect

    def redo(self) -> Rect | None:
        with self.lock:
            rect = self.history.redo(); s = self.history.last
            if s is not None and s.op is not None and s.op not in self.op_log:
                self.op_log.append(s.op)
            if rect is not None:
                self._restore_meta(s, 1)
            self.revision += 1
            if rect is not None:
                self.reground()
            return rect

    # ---- checkpoints ----
    def checkpoint(self, name: str) -> None:
        with self.lock:
            self.checkpoints[name] = (self.hf.data.copy(), list(self.op_log), [(o.params, o.result) for o in self.op_log])

    def rollback(self, name: str) -> None:
        with self.lock:
            data, ops, state = self.checkpoints[name]
            self.hf.data[:] = data
            self.op_log = list(ops)
            for o, (params, result) in zip(self.op_log, state):     # an edited operation goes back to how it was
                o.params, o.result = params, result
            self.history = History()
            self.revision += 1

    # ---- persistence ----
    def save(self, path: str | Path | None = None) -> Path:
        with self.lock:
            p = Path(path) if path else self.path
            if p is None:
                raise ValueError("no path given")
            self.path = save_world(p, self.hf, self.name, dumps(self.op_log))
            if self.origin is not None:          # the terrain before any operation (edit past operations after reopening)
                np.save(self.path / "terrain/heightfields/origin.npy", self.origin)
            return self.path

    def load(self, path: str | Path) -> None:
        with self.lock:
            hf, meta = load_world(path)
            ops_file = Path(path) / "terrain/operations/ops.json"
            ops = loads(ops_file.read_text()) if ops_file.exists() else []
            regenerate_transient(hf, ops)
            self.path = Path(path)
            of = Path(path) / "terrain/heightfields/origin.npy"
            self.replace(hf, meta.get("name", "Untitled"), ops, origin=np.load(of) if of.exists() else None)
