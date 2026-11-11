"""Undo/redo via tile snapshots: only tiles touched by a stroke are stored."""
from __future__ import annotations

import numpy as np

from terrain.heightfield.heightfield import Heightfield, Rect

TILE = 64


class Stroke:
    """Collects 'before' tiles lazily; call before_edit(rect) BEFORE mutating, finish() after."""

    def __init__(self, hf: Heightfield, label: str = "stroke"):
        self.hf, self.label = hf, label
        self.before: dict[tuple[int, int], np.ndarray] = {}
        self.after: dict[tuple[int, int], np.ndarray] = {}
        self.op = None   # optional Operation tied to this step (kept in sync with the op log on undo/redo)

    def _tiles(self, r: Rect):
        for ty in range(r[0] // TILE, (r[1] - 1) // TILE + 1):
            for tx in range(r[2] // TILE, (r[3] - 1) // TILE + 1):
                yield ty, tx

    def before_edit(self, rect: Rect) -> None:
        for key in self._tiles(rect):
            if key not in self.before:
                ty, tx = key
                self.before[key] = self.hf.data[ty * TILE:(ty + 1) * TILE, tx * TILE:(tx + 1) * TILE].copy()

    def finish(self) -> None:
        for (ty, tx) in self.before:
            self.after[(ty, tx)] = self.hf.data[ty * TILE:(ty + 1) * TILE, tx * TILE:(tx + 1) * TILE].copy()

    def _write(self, tiles: dict) -> Rect | None:
        rect = None
        for (ty, tx), arr in tiles.items():
            self.hf.data[ty * TILE:ty * TILE + arr.shape[0], tx * TILE:tx * TILE + arr.shape[1]] = arr
            r = (ty * TILE, ty * TILE + arr.shape[0], tx * TILE, tx * TILE + arr.shape[1])
            rect = r if rect is None else (min(rect[0], r[0]), max(rect[1], r[1]), min(rect[2], r[2]), max(rect[3], r[3]))
        return rect

    def undo(self) -> Rect | None:
        return self._write(self.before)

    def redo(self) -> Rect | None:
        return self._write(self.after)


class History:
    def __init__(self, limit: int = 100):
        self.limit = limit
        self._undo: list[Stroke] = []
        self._redo: list[Stroke] = []
        self.last: Stroke | None = None   # step touched by the latest undo()/redo()

    def push(self, s: Stroke) -> None:
        if not s.before and s.op is None:
            return
        self._undo.append(s)
        del self._undo[:-self.limit]
        self._redo.clear()

    def push_always(self, s: Stroke) -> None:
        self._undo.append(s)
        del self._undo[:-self.limit]
        self._redo.clear()

    def undo(self) -> Rect | None:
        if not self._undo:
            return None
        s = self._undo.pop()
        self._redo.append(s)
        self.last = s
        return s.undo()

    def redo(self) -> Rect | None:
        if not self._redo:
            return None
        s = self._redo.pop()
        self._undo.append(s)
        self.last = s
        return s.redo()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)
