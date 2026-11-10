import json

import numpy as np

from core.history.operations import Operation, dumps, run
from geometry.cliffs.cliff import build_cliff_wall, create_cliff_plateau
from terrain.heightfield.heightfield import Heightfield
from terrain.procedural.noise import fbm

POLY = [(200, 200), (330, 190), (350, 320), (210, 335)]


def terrain():
    h = Heightfield(257, 257, 2.0)
    h.data[:] = fbm(257, 257, 2.0, 5, 1 / 200) * 6
    return h


def test_wall_mesh_valid_and_outward():
    h = terrain()
    rect, rep = create_cliff_plateau(h, POLY, 40, seed=2)
    m = rep["_mesh"]
    assert rep["triangles"] > 1000 and m.degenerate_faces() == 0
    assert np.isfinite(m.vertices).all() and m.faces.max() < len(m.vertices)
    fn = m.face_normals(); fc = m.vertices[m.faces].mean(1)
    cen = np.array([[270, 262]])
    assert np.mean(np.einsum("ij,ij->i", fn[:, [0, 2]], fc[:, [0, 2]] - cen)) > 0       # faces outward
    assert not [w for w in rep["warnings"] if w["severity"] == "error"]


def test_wall_stands_on_terrain_and_reaches_top():
    h = terrain(); _, rep = create_cliff_plateau(h, POLY, 40, seed=2); m = rep["_mesh"]
    ring = len(m.vertices) // 37
    assert abs(m.vertices[:ring, 1] - 40).max() < 1e-4                                      # top row at plateau height
    gap = [abs(v[1] - h.sample(v[0], v[2])) for v in m.vertices[-ring:]]
    assert max(gap) < 0.5
    assert h.sample(270, 262) == 40.0                                                       # heightfield top is flat


def test_wall_has_real_overhang():
    h = terrain(); _, rep = create_cliff_plateau(h, POLY, 40, overhang=4, flare=0, ledge=0, roughness=0); m = rep["_mesh"]
    ring = len(m.vertices) // 37; cen = np.array([270, 262])
    r = np.linalg.norm(m.vertices[:, [0, 2]].reshape(37, ring, 2) - cen, axis=2).mean(1)
    # cap (near top) protrudes further than the mid wall => non-heightfield geometry (radius not monotone)
    assert r[3:6].max() > r[12:20].mean() + 1.0


def test_wall_detached_detected_after_terrain_change():
    from geometry.cliffs.cliff import validate_cliff
    h = terrain(); _, rep = create_cliff_plateau(h, POLY, 40); m = rep["_mesh"]
    h.data[:] += 10                                                                          # terrain moved under the wall
    assert any(w["code"] == "cliff_detached" for w in validate_cliff(h, m, len(m.vertices) // 37))


def test_deterministic_and_serializable():
    op = Operation("cliff_plateau", dict(polygon=POLY, height=40, seed=3))
    a = terrain(); run(a, op); m1 = op.result["_mesh"].vertices.copy()
    b = terrain(); op2 = Operation("cliff_plateau", dict(polygon=POLY, height=40, seed=3)); run(b, op2)
    assert np.array_equal(m1, op2.result["_mesh"].vertices) and np.array_equal(a.data, b.data)
    s = dumps([op]); assert "_mesh" not in s and json.loads(s)[0]["result"]["triangles"] > 0
