from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Mesh:
    vertices: np.ndarray   # (N,3) float32, X, Y(up), Z
    faces: np.ndarray      # (M,3) uint32
    uvs: np.ndarray        # (N,2) float32

    def face_normals(self) -> np.ndarray:
        v = self.vertices
        n = np.cross(v[self.faces[:, 1]] - v[self.faces[:, 0]], v[self.faces[:, 2]] - v[self.faces[:, 0]])
        return n

    def vertex_normals(self) -> np.ndarray:
        fn = self.face_normals()                       # area-weighted
        out = np.zeros_like(self.vertices)
        for k in range(3):
            np.add.at(out, self.faces[:, k], fn)
        ln = np.linalg.norm(out, axis=1, keepdims=True)
        return (out / np.maximum(ln, 1e-12)).astype(np.float32)

    def degenerate_faces(self, eps: float = 1e-8) -> int:
        return int((np.linalg.norm(self.face_normals(), axis=1) < eps).sum())

    def interleaved(self) -> np.ndarray:
        """float32 [px,py,pz,nx,ny,nz] per vertex, ready for the viewport."""
        return np.concatenate([self.vertices, self.vertex_normals()], 1).astype(np.float32)
