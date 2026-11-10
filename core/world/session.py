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
        self.checkpoints: dict[str, tuple[np.ndarray, list[Operation]]] = {}
        self.revision = 0   # bumps on every change (UI/preview cache invalidation)

    # ---- lifecycle ----
    def replace(self, hf: Heightfield, name: str | None = None, ops: list[Operation] | None = None) -> None:
        with self.lock:
            self.hf, self.history, self.op_log = hf, History(), list(ops or [])
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
            return rect

    def commit_stroke(self, stroke: Stroke) -> None:
        with self.lock:
            stroke.finish(); self.history.push(stroke); self.revision += 1

    def undo(self) -> Rect | None:
        with self.lock:
            rect = self.history.undo(); s = self.history.last
            if rect is not None or (s and s.op is not None):
                if s is not None and s.op is not None and s.op in self.op_log:
                    self.op_log.remove(s.op)
                self.revision += 1
            return rect

    def redo(self) -> Rect | None:
        with self.lock:
            rect = self.history.redo(); s = self.history.last
            if s is not None and s.op is not None and s.op not in self.op_log:
                self.op_log.append(s.op)
            self.revision += 1
            return rect

    # ---- checkpoints ----
    def checkpoint(self, name: str) -> None:
        with self.lock:
            self.checkpoints[name] = (self.hf.data.copy(), list(self.op_log))

    def rollback(self, name: str) -> None:
        with self.lock:
            data, ops = self.checkpoints[name]
            self.hf.data[:] = data
            self.op_log = list(ops)
            self.history = History()
            self.revision += 1

    # ---- persistence ----
    def save(self, path: str | Path | None = None) -> Path:
        with self.lock:
            p = Path(path) if path else self.path
            if p is None:
                raise ValueError("no path given")
            self.path = save_world(p, self.hf, self.name, dumps(self.op_log))
            return self.path

    def load(self, path: str | Path) -> None:
        with self.lock:
            hf, meta = load_world(path)
            ops_file = Path(path) / "terrain/operations/ops.json"
            ops = loads(ops_file.read_text()) if ops_file.exists() else []
            regenerate_transient(hf, ops)
            self.path = Path(path)
            self.replace(hf, meta.get("name", "Untitled"), ops)
