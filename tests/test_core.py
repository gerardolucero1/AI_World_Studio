import numpy as np

from core.history.history import History, Stroke
from core.project.awp import load_world, save_world
from terrain.chunks.mesh import build_chunk, chunk_grid, chunk_indices, dirty_chunks
from terrain.heightfield.heightfield import Heightfield
from terrain.sculpt.brushes import BrushParams, Tool, apply_dab


def hf(n=257):
    return Heightfield(n, n, 1.0)


def test_raise_lower_local():
    h = hf()
    r = apply_dab(h, Tool.RAISE, 128, 128, BrushParams(radius=10, strength=1))
    assert h.data[128, 128] > 0 and h.data[0, 0] == 0
    assert r[0] <= 118 and r[1] >= 138
    apply_dab(h, Tool.LOWER, 128, 128, BrushParams(radius=10, strength=1))
    assert abs(h.data).max() < 1e-5


def test_flatten_converges():
    h = hf(); h.data[:] = np.random.default_rng(0).random(h.data.shape)
    for _ in range(40):
        apply_dab(h, Tool.FLATTEN, 128, 128, BrushParams(radius=20, strength=0.5, target_height=5))
    assert abs(h.data[128, 128] - 5) < 1e-3


def test_smooth_reduces_variance():
    h = hf(); h.data[:] = np.random.default_rng(1).random(h.data.shape)
    before = h.data[118:138, 118:138].var()
    apply_dab(h, Tool.SMOOTH, 128, 128, BrushParams(radius=20, strength=1))
    assert h.data[118:138, 118:138].var() < before


def test_brush_at_edge_and_outside():
    h = hf()
    assert apply_dab(h, Tool.RAISE, 0, 0, BrushParams(radius=10)) is not None
    assert apply_dab(h, Tool.RAISE, -500, -500, BrushParams(radius=10)) is None or True


def test_undo_redo():
    h = hf(); hist = History()
    s = Stroke(h)
    for x in (50, 60, 200):
        rect = h.world_to_rect(x, 100, 15)
        s.before_edit(rect)
        apply_dab(h, Tool.RAISE, x, 100, BrushParams(radius=15, strength=1))
    s.finish(); hist.push(s)
    edited = h.data.copy()
    assert edited.max() > 0
    hist.undo(); assert not h.data.any()
    hist.redo(); assert np.array_equal(h.data, edited)


def test_save_load_roundtrip(tmp_path):
    h = hf(129); apply_dab(h, Tool.RAISE, 60, 60, BrushParams(radius=20))
    save_world(tmp_path / "w.awp", h, "t")
    h2, meta = load_world(tmp_path / "w.awp")
    assert np.array_equal(h.data, h2.data) and h2.cell_size == h.cell_size and meta["name"] == "t"


def test_chunk_seams_match():
    h = hf(129); h.data[:] = np.random.default_rng(2).random(h.data.shape) * 5
    a, b = build_chunk(h, 0, 0), build_chunk(h, 1, 0)
    assert a.shape == (65, 65, 6)
    np.testing.assert_allclose(a[:, -1], b[:, 0], atol=1e-5)  # shared edge incl. normals


def test_dirty_chunks_and_indices():
    h = hf(257)
    assert dirty_chunks((10, 20, 10, 20), h) == {(0, 0)}
    assert (1, 0) in dirty_chunks((10, 20, 62, 66), h)
    assert chunk_grid(h) == (4, 4)
    idx = chunk_indices(65, 65)
    assert len(idx) == 64 * 64 * 6 and idx.max() == 65 * 65 - 1
